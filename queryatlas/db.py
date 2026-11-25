import hashlib
import os
import secrets
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from fastapi import HTTPException

ACTIONS = {
    "search",
    "original",
    "ingest",
    "jobs",
    "monitor",
    "ack",
    "analysis",
    "publish",
    "sql",
    "detail",
    "templates",
    "mysql_view",
    "mysql_manage",
    "mysql_run",
}
FILES = Path(os.getenv("SOURCE_DIR", "/data/originals"))


def db():
    return psycopg.connect(
        os.environ["DATABASE_URL"], row_factory=dict_row, connect_timeout=4
    )


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def init():
    with db() as conn:
        conn.execute(Path(__file__).with_name("schema.sql").read_text(), prepare=False)
        if not conn.execute("SELECT 1 FROM people WHERE root").fetchone():
            token = Path(os.environ["BOOTSTRAP_FILE"]).read_text().strip()
            if len(token) < 32:
                raise ValueError("管理员初始密钥过短")
            conn.execute(
                "INSERT INTO people VALUES ('admin',%s,true,true)", (digest(token),)
            )


def identify(conn, key):
    user = (
        conn.execute(
            """SELECT p.name,p.root,coalesce(e.display_name,p.name) AS display_name FROM people p LEFT JOIN external_users e ON e.person=p.name WHERE p.enabled AND (p.digest=%s OR EXISTS(SELECT 1 FROM sessions s WHERE s.person=p.name AND s.digest=%s AND s.expires>now()))""",
            (digest(key), digest(key)),
        ).fetchone()
        if key
        else None
    )
    if not user:
        raise HTTPException(401, "请使用有效访问密钥登录")
    return user


def root(user):
    if not user["root"]:
        raise HTTPException(403, "需要平台管理员权限")


def scope(conn, user, space, action):
    if user["root"]:
        return None
    rows = conn.execute(
        """SELECT DISTINCT g.asset FROM grants_ g JOIN roles r ON r.name=g.role
                         WHERE g.person=%s AND g.space=%s AND %s=ANY(r.actions)""",
        (user["name"], space, action),
    ).fetchall()
    assets = [r["asset"] for r in rows]
    return None if "" in assets else assets


def require(conn, user, space, action, asset=None):
    assets = scope(conn, user, space, action)
    if assets is not None and (asset is None or asset not in assets):
        raise HTTPException(403, "当前身份没有此项权限")


def log(conn, user, action, details):
    conn.execute(
        "INSERT INTO audit(actor,action,details) VALUES (%s,%s,%s)",
        (user["name"], action, details),
    )


def issue(conn, name):
    token = secrets.token_urlsafe(36)
    conn.execute(
        "INSERT INTO people(name,digest) VALUES (%s,%s)", (name, digest(token))
    )
    return token
