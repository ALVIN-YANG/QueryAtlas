import argparse
import hashlib
import os
import sys
import tempfile
import time
import threading
import psycopg
from pathlib import Path

from .db import FILES, db, identify, init, require
from .embedding import LOCAL_MODEL, MODEL, index_nodes
from .reindex import INGEST_LOCK, backfill, store_vectors
from .extract import MAX_BYTES, SUFFIXES, ReviewNeeded, extract


def enqueue(conn, user, space, name, data):
    require(conn, user, space, "ingest")
    if not conn.execute("SELECT 1 FROM spaces WHERE name=%s", (space,)).fetchone():
        raise ValueError("空间不存在")
    ext = Path(name).suffix.lower()
    if (
        not name
        or name.startswith(".")
        or len(name) > 180
        or any(c in name for c in "/\\")
        or any(ord(c) < 32 for c in name)
        or ext not in SUFFIXES
    ):
        raise ValueError("请使用受支持的文件名和格式")
    if not 0 < len(data) <= MAX_BYTES:
        raise ValueError("文件须为 1 字节至 12 MiB")
    sha = hashlib.sha256(data).hexdigest()
    FILES.mkdir(parents=True, exist_ok=True)
    target = FILES / (sha + ext)
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=FILES, delete=False) as tmp:
            tmp.write(data)
        os.replace(tmp.name, target)
    conn.execute("SELECT pg_advisory_xact_lock(75288513)")
    return conn.execute(
        "INSERT INTO jobs(space,asset,hash,actor) VALUES (%s,%s,%s,%s) RETURNING id",
        (space, name, sha, user["name"]),
    ).fetchone()["id"]


def progress(job, stage):
    with db() as c:
        c.execute(
            "INSERT INTO job_progress VALUES(%s,%s) ON CONFLICT(job) DO UPDATE SET stage=excluded.stage",
            (job, stage),
        )


def run_one():
    # The session lock and transaction survive until the candidate is fully published.
    # A process crash rolls back, leaving the queued item eligible on restart.
    with db() as conn:
        if not conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s) AS ok", (INGEST_LOCK,)
        ).fetchone()["ok"]:
            return False
        job = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if not job:
            return False
        try:
            with conn.transaction():
                actor = conn.execute(
                    "SELECT name,root FROM people WHERE name=%s AND enabled",
                    (job["actor"],),
                ).fetchone()
                if not actor:
                    raise ReviewNeeded("提交账号已停用")
                require(conn, actor, job["space"], "ingest")
                current = conn.execute(
                    """SELECT j.hash FROM docs d JOIN jobs j ON j.id=d.version
                                          WHERE d.space=%s AND d.asset=%s""",
                    (job["space"], job["asset"]),
                ).fetchone()
                if current and current["hash"] == job["hash"]:
                    status, note = "ready", "内容与在线版本相同"
                else:
                    path = FILES / (job["hash"] + Path(job["asset"]).suffix.lower())
                    progress(job["id"], "正在解析原件（扫描页执行 OCR）")
                    parts = extract(path)
                    progress(job["id"], "正在生成向量索引")
                    nodes = index_nodes(parts)
                    with conn.cursor() as cursor:
                        cursor.executemany(
                            "INSERT INTO nodes VALUES (%s,%s,%s,%s,%s,%s::vector)",
                            [
                                (
                                    job["id"],
                                    i,
                                    n.metadata["anchor"],
                                    n.metadata["method"],
                                    n.text,
                                    str(n.embedding) if MODEL == LOCAL_MODEL else None,
                                )
                                for i, n in enumerate(nodes)
                            ],
                        )
                    if MODEL != LOCAL_MODEL:
                        store_vectors(
                            conn,
                            [(job["id"], i, n.embedding) for i, n in enumerate(nodes)],
                        )
                    conn.execute(
                        """INSERT INTO docs VALUES (%s,%s,%s) ON CONFLICT(space,asset)
                                    DO UPDATE SET version=excluded.version""",
                        (job["space"], job["asset"], job["id"]),
                    )
                    status, note = "ready", f"{len(nodes)} 个片段"
        except Exception as err:
            # Do not expose parser errors containing document data or credentials.
            status = "failed"
            note = (
                str(err)
                if isinstance(err, ReviewNeeded)
                else f"解析或索引失败（{type(err).__name__}）"
            )
        conn.execute(
            "UPDATE jobs SET status=%s,note=%s,finished=clock_timestamp() WHERE id=%s",
            (status, note, job["id"]),
        )
        print(f"import job={job['id']} status={status}", flush=True)
    return True


def heartbeat():
    while True:
        try:
            with db() as conn:
                conn.execute("""INSERT INTO heartbeat VALUES ('worker',now()) ON CONFLICT(name)
                                DO UPDATE SET ts=now()""")
        except psycopg.OperationalError:
            pass
        time.sleep(15)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("worker")
    sub.add_parser("reindex")
    cmd = sub.add_parser("import")
    cmd.add_argument("files", nargs="+", type=Path)
    cmd.add_argument("--space", default="demo")
    args = parser.parse_args()
    if args.command == "init":
        init()
    elif args.command == "reindex":
        backfill()
    elif args.command == "import":
        key = os.environ.get("QUERYATLAS_TOKEN") or sys.stdin.readline().strip()
        with db() as conn:
            user = identify(conn, key)
            for path in args.files:
                with path.open("rb") as f:
                    print(
                        enqueue(
                            conn, user, args.space, path.name, f.read(MAX_BYTES + 1)
                        )
                    )
    else:
        threading.Thread(target=heartbeat, daemon=True).start()
        while True:
            if not run_one():
                time.sleep(2)


if __name__ == "__main__":
    main()
