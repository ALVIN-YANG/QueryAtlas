"""StarRocks catalogs, read-only querying and per-member projection/filtering."""

import logging
import os, re, time
from decimal import Decimal
from datetime import date
import pymysql
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from .db import db, scope, root, log
from .security import actor

router = APIRouter()
logger = logging.getLogger(__name__)
IDENT = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}\Z")


def ident(name):
    if not IDENT.fullmatch(name):
        raise HTTPException(422, "字段或表名不合法")
    return "`" + name + "`"


def sr(database=None, writer=False):
    return pymysql.connect(
        host="starrocks",
        port=9030,
        user="qa_load" if writer else "qa_read",
        password=os.environ["QA_SR_LOAD" if writer else "QA_SR_READ"],
        database=database,
        connect_timeout=4,
        read_timeout=35,
        autocommit=True,
        charset="utf8mb4",
    )


def permitted(c, u, space, action, name):
    allowed = scope(c, u, space, action)
    return allowed is None or "@data/" + name in allowed


def dataset(c, u, space, name, action="analysis"):
    if not permitted(c, u, space, action, name):
        raise HTTPException(403, "没有该数据集的权限")
    record = c.execute(
        "SELECT * FROM datasets WHERE space=%s AND name=%s", (space, name)
    ).fetchone()
    if not record:
        raise HTTPException(404, "数据集不存在")
    policies = (
        {}
        if u["root"]
        else {
            r["table_name"]: r
            for r in c.execute(
                "SELECT * FROM data_limits WHERE person=%s AND space=%s AND name=%s",
                (u["name"], space, name),
            )
        }
    )
    return record, policies


def catalog(c, u, space):
    return [
        dict(
            name=r["name"],
            batch=r["batch"],
            description=(
                "已发布分析表" if r["physical"] == "qa_imports" else r["description"]
            ),
        )
        for r in c.execute(
            "SELECT name,description,physical,batch FROM datasets WHERE space=%s ORDER BY name",
            (space,),
        )
        if permitted(c, u, space, "analysis", r["name"])
    ]


@router.get("/api/spaces/{space}/datasets")
def datasets(space: str, user=Depends(actor)):
    with db() as c:
        return catalog(c, user, space)


def columns(meta, policies, table):
    if (
        policies
        and (table == "revenue0" or table == "lineorder_flat")
        and any(t != table for t in policies)
    ):
        return []
    full = meta["tables"][table]
    return (
        [x for x in full if x in policies[table]["fields"]]
        if table in policies
        else full
    )


def visible_table(cur, meta, policies, table):
    if table not in meta["tables"]:
        raise HTTPException(422, "表不在数据集中")
    fields = columns(meta, policies, table)
    if not fields:
        raise HTTPException(403, "此表没有可查看的字段")
    sql = (
        "SELECT "
        + ",".join(map(ident, fields))
        + " FROM "
        + ident(meta["description"] if meta["physical"] == "qa_imports" else table)
    )
    p = policies.get(table, {})
    if p.get("filter_col"):
        sql += (
            " WHERE "
            + ident(p["filter_col"])
            + "="
            + cur.mogrify("%s", (p["filter_value"],))
        )
    return sql


@router.get("/api/spaces/{space}/datasets/{name}")
def info(space: str, name: str, user=Depends(actor)):
    with db() as c:
        d, p = dataset(c, user, space, name)
        return {
            "name": name,
            "description": "已发布分析表" if d["physical"] == "qa_imports" else d["description"],
            "batch": d["batch"],
            "tables": {t: columns(d, p, t) for t in d["tables"] if columns(d, p, t)},
            "detail": permitted(c, user, space, "detail", name),
            "sql": permitted(c, user, space, "sql", name),
        }


def json_value(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


def execute(database, statement):
    start = time.perf_counter()
    try:
        with sr(database) as conn, conn.cursor() as cur:
            cur.execute("SET query_timeout=25")
            cur.execute(statement)
            rows = cur.fetchmany(101)
            result = {
                "truncated": len(rows) > 100,
                "columns": [c[0] for c in cur.description],
                "rows": [[json_value(v) for v in row] for row in rows][:100],
                "sql": statement,
                "ms": round(1000 * (time.perf_counter() - start)),
            }
            lookup = time.perf_counter()
            try:
                cur.execute("SELECT LAST_QUERY_ID()")
                result["query_id"] = cur.fetchone()[0]
            except pymysql.MySQLError:
                logger.warning("Could not read the StarRocks query ID")
            result["query_id_ms"] = round(1000 * (time.perf_counter() - lookup), 2)
            return result
    except pymysql.MySQLError:
        raise HTTPException(422, "StarRocks 未能在限定时间内执行该查询")


class Restriction(BaseModel):
    person: str
    table: str
    fields: list[str]
    filter_col: str | None = None
    filter_value: str | None = Field(default=None, max_length=200)
    remove: bool = False


@router.get("/api/admin/limits/{space}/{name}")
def limits(space: str, name: str, user=Depends(actor)):
    root(user)
    with db() as c:
        return list(
            c.execute(
                "SELECT * FROM data_limits WHERE space=%s AND name=%s", (space, name)
            )
        )


@router.post("/api/admin/limits/{space}/{name}")
def limit(space: str, name: str, b: Restriction, user=Depends(actor)):
    root(user)
    with db() as c:
        d, _ = dataset(c, user, space, name)
        allcols = d["tables"].get(b.table)
        if (
            not allcols
            or set(b.fields) - set(allcols)
            or (
                b.filter_col and (b.filter_col not in allcols or b.filter_value is None)
            )
        ):
            raise HTTPException(422, "字段范围无效")
        if not c.execute(
            "SELECT 1 FROM people WHERE name=%s AND NOT root", (b.person,)
        ).fetchone():
            raise HTTPException(422, "请选择有效成员")
        key = (b.person, space, name, b.table)
        c.execute(
            "DELETE FROM data_limits WHERE person=%s AND space=%s AND name=%s AND table_name=%s",
            key,
        )
        if not b.remove:
            c.execute(
                "INSERT INTO data_limits VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (*key, b.fields, b.filter_col, b.filter_value),
            )
        log(c, user, "analysis.scope", f"{b.person} / {space} / {name} / {b.table}")
    return {"ok": True}
