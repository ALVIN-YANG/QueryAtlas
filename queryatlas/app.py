import os
import secrets
import time
import uuid
from contextlib import closing
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

from . import monitor, telemetry, feishu, analytics, tabular, chat, benchmarks, sql_templates, mysql_console
from .db import ACTIONS, digest, FILES, db, identify, require, root, scope
from .dashscope import EmbeddingUnavailable
from .embedding import LOCAL_MODEL, MODEL, active_embedding, prepare_embedding
from .extract import MAX_BYTES
from .ingest import enqueue
from .security import actor, key_from, router
from .source_preview import html_preview

app = FastAPI(title="QueryAtlas", version="0.1", docs_url=None, redoc_url=None)
app.include_router(router)
app.include_router(feishu.router)
for module in (analytics, tabular, chat, benchmarks, sql_templates, mysql_console):
    app.include_router(module.router)
static = Path(__file__).with_name("web")
if not static.is_dir():
    static = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=static), name="static")
requests_count = Counter(
    "queryatlas_http_requests", "Requests by status class", ["status"]
)
latency = Histogram("queryatlas_http_duration_seconds", "End-to-end HTTP time")
probe_lock = Lock()


@app.middleware("http")
async def trace(request: Request, call_next):
    start = time.monotonic()
    code = 500
    try:
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not (
            request.headers.get("X-QueryAtlas") == "1"
            or request.headers.get("Authorization", "").startswith("Bearer ")
        ):
            code = 403
            return JSONResponse({"detail": "请求缺少操作标识"}, status_code=403)
        if request.method == "POST" and request.url.path.endswith("/imports"):
            length = request.headers.get("Content-Length", "")
            if not length.isdigit() or len(length) > 12:
                code = 411
                return JSONResponse({"detail": "文件上传需提供长度"}, status_code=code)
            if int(length) > MAX_BYTES + 8192:
                code = 413
                return JSONResponse({"detail": "文件超出 12 MiB"}, status_code=code)
        res = await call_next(request)
        code = res.status_code
        res.headers.update(
            {
                "X-Request-ID": uuid.uuid4().hex,
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            }
        )
        res.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-ancestors 'none'; object-src 'none'",
        )
        return res
    finally:
        requests_count.labels(str(code // 100) + "xx").inc()
        elapsed = time.monotonic() - start
        latency.observe(elapsed)
        if request.url.path.startswith("/api/"):
            telemetry.record(code, elapsed)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(static / "index.html")


@app.get("/readyz")
def ready():
    try:
        with db() as c:
            c.execute("SELECT 1 FROM docs LIMIT 1")
            c.execute("SELECT 1 FROM sql_templates LIMIT 1")
        with probe_lock:
            prepare_embedding()
        return {"ready": True}
    except Exception:
        return JSONResponse({"ready": False}, status_code=503)


@app.get("/internal/window", include_in_schema=False)
def internal_window(request: Request):
    import secrets

    if not secrets.compare_digest(
        request.headers.get("X-Probe-Key", ""), os.environ["PROBE_KEY"]
    ):
        raise HTTPException(403, "需要监控身份")
    return telemetry.window()


@app.get("/metrics")
def metrics(user=Depends(actor)):
    root(user)
    return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


class Login(BaseModel):
    token: str = Field(min_length=12, max_length=200)


@app.post("/api/login")
def login(b: Login):
    with db() as c:
        user = identify(c, b.token)
        session = secrets.token_urlsafe(40)
        c.execute("DELETE FROM sessions WHERE expires<now()")
        c.execute(
            "INSERT INTO sessions VALUES(%s,%s,now()+interval '8 hours')",
            (digest(session), user["name"]),
        )
    res = JSONResponse(user)
    res.set_cookie(
        "qa_key",
        session,
        httponly=True,
        samesite="strict",
        max_age=8 * 3600,
        secure=os.getenv("COOKIE_SECURE") == "1",
    )
    return res


@app.post("/api/logout")
def logout(request: Request):
    with db() as c:
        c.execute(
            "DELETE FROM sessions WHERE digest=%s",
            (digest(request.cookies.get("qa_key", "")),),
        )
    res = JSONResponse({"ok": True})
    res.delete_cookie("qa_key")
    return res


@app.get("/api/me")
def me(user=Depends(actor)):
    with db() as c:
        if user["root"]:
            spaces = c.execute("SELECT name FROM spaces ORDER BY name")
        else:
            spaces = c.execute(
                "SELECT DISTINCT space AS name FROM grants_ WHERE person=%s ORDER BY name",
                (user["name"],),
            )
        return {**user, "spaces": [r["name"] for r in spaces]}


@app.get("/api/spaces/{space}/assets")
def assets(space: str, user=Depends(actor)):
    with db() as c:
        actions = {a: scope(c, user, space, a) for a in ACTIONS}
        allowed = (
            None
            if actions["search"] is None or actions["original"] is None
            else list(set(actions["search"] + actions["original"]))
        )
        rows = c.execute(
            """SELECT d.asset,d.version,j.finished AS updated,j.note FROM docs d
            JOIN jobs j ON j.id=d.version WHERE d.space=%s AND (%s::text[] IS NULL OR d.asset=ANY(%s))
            ORDER BY d.asset""",
            (space, allowed, allowed),
        ).fetchall()
        for r in rows:
            r["original"] = (
                actions["original"] is None or r["asset"] in actions["original"]
            )
        return {
            "assets": rows,
            "actions": [a for a, v in actions.items() if v is None or (v and not a.startswith("mysql_"))],
        }


@app.post("/api/spaces/{space}/imports", status_code=202)
def upload(space: str, file: UploadFile = File(...), user=Depends(actor)):
    with db() as c:
        require(c, user, space, "ingest")
        try:
            return {
                "job": enqueue(
                    c, user, space, file.filename or "", file.file.read(MAX_BYTES + 1)
                )
            }
        except ValueError as error:
            raise HTTPException(422, str(error))


@app.get("/api/spaces/{space}/jobs")
def jobs(space: str, user=Depends(actor)):
    with db() as c:
        require(c, user, space, "jobs")
        return c.execute(
            """SELECT id,asset,status,CASE WHEN status='queued' THEN coalesce(p.stage,note) ELSE note END AS note,actor,created,finished FROM jobs LEFT JOIN job_progress p ON p.job=jobs.id
                            WHERE space=%s ORDER BY id DESC LIMIT 80""",
            (space,),
        ).fetchall()


class Question(BaseModel):
    q: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


@app.post("/api/spaces/{space}/search")
def search(space: str, q: Question, key: str = Depends(key_from)):
    with db() as c:
        user = identify(c, key)
        if scope(c, user, space, "search") == []:
            raise HTTPException(403, "当前身份没有检索权限")
    start = time.perf_counter()
    try:
        vector = str(active_embedding().get_query_embedding(q.q))
    except EmbeddingUnavailable as error:
        raise HTTPException(503, str(error)) from error
    timings = {"embedding": telemetry.elapsed_ms(start)}
    if MODEL == LOCAL_MODEL:
        source = ""
        column = "n.embedding"
        model_params = ()
    else:
        source = "JOIN node_embeddings e ON e.version=n.version AND e.seq=n.seq AND e.model=%s"
        column = "e.embedding"
        model_params = (MODEL,)
    with db() as c:
        user = identify(c, key)
        allowed = scope(c, user, space, "search")
        if allowed == []:
            raise HTTPException(403, "检索权限已撤销")
        start = time.perf_counter()
        rows = c.execute(
            f"""SELECT d.asset,d.version,n.seq,n.body,n.anchor,n.method,
            1-({column} <=> %s::vector) AS score FROM docs d JOIN nodes n ON n.version=d.version {source}
            WHERE {column} IS NOT NULL AND d.space=%s AND (%s::text[] IS NULL OR d.asset=ANY(%s))
            ORDER BY score DESC,d.asset,n.seq LIMIT %s""",
            (vector, *model_params, space, allowed, allowed, q.limit),
        ).fetchall()
        timings["vector_search"] = telemetry.elapsed_ms(start)
        current = scope(c, identify(c, key), space, "search")
        if current == []:
            raise HTTPException(403, "检索权限已撤销")
        if current is not None:
            rows = [row for row in rows if row["asset"] in current]
        return {"model": MODEL, "hits": rows, "timings_ms": timings}


@app.get("/api/spaces/{space}/source/{version}")
def source(space: str, version: int, view: Literal["download", "inline"] = "download", user=Depends(actor)):
    with db() as c:
        row = c.execute(
            """SELECT j.asset,j.hash FROM jobs j JOIN nodes n ON n.version=j.id
                         WHERE j.space=%s AND j.id=%s LIMIT 1""",
            (space, version),
        ).fetchone()
        allowed = scope(c, user, space, "original")
        if not row or (allowed is not None and row["asset"] not in allowed):
            raise HTTPException(404, "原件不可访问")
    suffix = Path(row["asset"]).suffix.lower()
    path = FILES / (row["hash"] + suffix)
    if view == "inline" and suffix == ".html":
        if not path.is_file():
            raise HTTPException(404, "原件不可访问")
        return html_preview(path, row["asset"])
    inline_pdf = view == "inline" and suffix == ".pdf"
    return FileResponse(
        path,
        filename=row["asset"],
        media_type="application/pdf" if inline_pdf else "application/octet-stream",
        content_disposition_type="inline" if inline_pdf else "attachment",
    )


@app.get("/api/spaces/{space}/monitor")
def status(space: str, user=Depends(actor)):
    with db() as c:
        require(c, user, space, "monitor")
        counts = c.execute(
            """SELECT count(*) AS total,count(*) FILTER (WHERE status='failed') AS failed,
            count(*) FILTER (WHERE status='queued') AS queued FROM jobs
            WHERE space=%s AND created>now()-interval '24 hours'""",
            (space,),
        ).fetchone()
    return {
        **monitor.snapshot(space, user["root"]),
        "jobs24": counts,
    }


@app.post("/api/spaces/{space}/alerts/{event}/ack")
def acknowledge(space: str, event: int, user=Depends(actor)):
    with db() as c:
        require(c, user, space, "ack")
    with closing(monitor.connect()) as c, c:
        row = c.execute(
            """UPDATE incidents SET ack_by=?,ack_at=? WHERE id=? AND closed IS NULL
            AND ack_by IS NULL AND (space=? OR (space='' AND ?)) RETURNING id""",
            (user["name"], int(time.time()), event, space, user["root"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "没有可认领的告警")
    return {"ok": True}
