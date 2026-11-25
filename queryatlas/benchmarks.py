import math
import re
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from .analytics import sr, ident, execute
from .db import db, require, log
from .security import actor

router = APIRouter()


@router.get("/api/spaces/{space}/benchmarks")
def packs(space: str, user=Depends(actor)):
    with db() as c:
        require(c, user, space, "publish")
        rows = []
        for b in c.execute(
            "SELECT name,counts,timings FROM benchmark_packs ORDER BY name"
        ):
            elapsed = sorted(t for v in b["timings"].values() for t in v["ms"])
            rows.append(
                dict(
                    name=b["name"],
                    counts=b["counts"],
                    nquery=len(b["timings"]),
                    p95=elapsed[math.ceil(len(elapsed) * 0.95) - 1],
                )
            )
        return rows


@router.post("/api/spaces/{space}/benchmarks/{name}")
def attach_pack(space: str, name: str, user=Depends(actor)):
    with db() as c:
        require(c, user, space, "publish")
        pack = c.execute(
            "SELECT counts FROM benchmark_packs WHERE name=%s", (name,)
        ).fetchone()
        if not pack:
            raise HTTPException(404, "基准包尚未装载")
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{space}/{name}",))
        if c.execute(
            "SELECT 1 FROM mysql_sync_checkpoints WHERE space=%s AND name=%s", (space, name)
        ).fetchone():
            raise HTTPException(409, "该名称已用于 MySQL 同步")
        template = c.execute(
            "SELECT physical,description,tables,batch FROM datasets WHERE space='demo' AND name=%s",
            (name,),
        ).fetchone()
        if not template:
            raise HTTPException(409, "尚未注册基准库")
        with sr(template["physical"]) as s, s.cursor() as cur:
            for t, count in pack["counts"].items():
                cur.execute("SELECT COUNT(*) FROM " + ident(t))
                if cur.fetchone()[0] != count:
                    raise HTTPException(409, "源表行数校验失败：" + t)
        c.execute(
            """INSERT INTO datasets(space,name,physical,description,tables,batch) VALUES(%s,%s,%s,%s,%s,%s)
               ON CONFLICT(space,name) DO UPDATE SET physical=excluded.physical,description=excluded.description,
                   tables=excluded.tables,batch=excluded.batch""",
            (
                space,
                name,
                template["physical"],
                template["description"],
                Jsonb(template["tables"]),
                template["batch"],
            ),
        )
        log(c, user, "benchmark.attach", space + "/" + name)
    return {"ok": True}


def numbered(q, meta, policy, detail):
    match = re.search(r"(?<![a-zA-Z])Q\s*(\d+(?:\.\d+)?)(?!\d)", q, re.I)
    if not match or meta["physical"] != "qa_" + meta["name"] + "_sf1":
        return None
    if not detail or policy:
        raise HTTPException(403, "完整基准查询需要该数据集的完整明细授权。")
    label = "Q" + match[1]
    with db() as c:
        pack = c.execute(
            "SELECT queries->>%s AS sql FROM benchmark_packs WHERE name=%s",
            (label, meta["name"]),
        ).fetchone()
    if not pack or not pack["sql"]:
        raise HTTPException(422, "未找到该基准题号")
    stmts = [
        s for s in pack["sql"].split(";") if re.search(r"\b(select|with)\b", s, re.I)
    ]
    parts = [execute(meta["physical"], s) for s in stmts]
    first = parts[0]
    if len(parts) > 1:
        first["additional"] = [
            {k: v for k, v in r.items() if k != "sql"} for r in parts[1:]
        ]
    first["sql"] = pack["sql"]
    return first
