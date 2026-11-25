import csv
import io
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb
from .db import db, require, log
from .analytics import sr, ident
from .security import actor

router = APIRouter()
TYPES = {
    "VARCHAR": "VARCHAR(2048)",
    "BIGINT": "BIGINT",
    "DECIMAL": "DECIMAL(18,4)",
    "DATE": "DATE",
}
MAX = 4 * 1024 * 1024


def parse(raw):
    try:
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig")), strict=True))
    except (UnicodeError, csv.Error):
        raise HTTPException(422, "请选择 UTF-8 CSV，检查分隔符及引号")
    if (
        not rows
        or not 1 <= len(rows[0]) <= 48
        or len(set(rows[0])) != len(rows[0])
        or any(not h.strip() for h in rows[0])
    ):
        raise HTTPException(422, "CSV 首行应为不重复的列名，最多 48 列")
    if len(rows) < 2 or len(rows) > 50001:
        raise HTTPException(422, "单次支持 1 至 50000 行数据")
    for i, r in enumerate(rows[1:], 2):
        if len(r) != len(rows[0]):
            raise HTTPException(422, f"第 {i} 行列数不一致")
    return rows


class Col(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: str


class Mapping(BaseModel):
    columns: list[Col] = Field(min_length=1, max_length=48)


def value(v, t):
    if v == "":
        return None
    if t == "DATE":
        return date.fromisoformat(v)
    if t == "BIGINT":
        x = int(v)
        if not -(2**63) <= x < 2**63:
            raise ValueError()
        return x
    if t == "DECIMAL":
        n = Decimal(v)
        if (
            not n.is_finite()
            or n.copy_abs() >= 10**14
            or n != n.quantize(Decimal(".0001"))
        ):
            raise ValueError()
        return n
    if len(v.encode()) > 2048:
        raise ValueError()
    return v


@router.post("/api/spaces/{space}/candidates")
def preview(
    space: str, name: str = Form(...), file: UploadFile = File(...), u=Depends(actor)
):
    ident(name)
    if name.lower() in {"tpch", "ssb", "tpcds"}:
        raise HTTPException(422, "该名称为内置基准保留")
    with db() as c:
        require(c, u, space, "ingest")
        raw = file.file.read(MAX + 1)
        if len(raw) > MAX:
            raise HTTPException(413, "CSV 最大 4 MiB")
        rows = parse(raw)
        id = uuid.uuid4()
        c.execute(
            "INSERT INTO data_candidates(id,space,name,owner_name,raw) VALUES (%s,%s,%s,%s,%s)",
            (id, space, name, u["name"], raw),
        )
    return {
        "id": str(id),
        "header": rows[0],
        "sample": rows[1:4],
        "count": len(rows) - 1,
    }


def candidate(c, u, space, id, action):
    require(c, u, space, action)
    rec = c.execute(
        "SELECT * FROM data_candidates WHERE id=%s AND space=%s FOR UPDATE", (id, space)
    ).fetchone()
    if not rec:
        raise HTTPException(404, "找不到候选版本")
    return rec


@router.post("/api/spaces/{space}/candidates/{id}/validate")
def validate(space: str, id: uuid.UUID, m: Mapping, u=Depends(actor)):
    with db() as c:
        rec = candidate(c, u, space, id, "ingest")
        if rec["status"] == "published":
            raise HTTPException(409, "已发布版本不可修改")
        rows = parse(bytes(rec["raw"]))
        cols = [(ident(x.name), x.type) for x in m.columns]
        if (
            any(x.name.lower() == "qa_row" for x in m.columns)
            or len(cols) != len(rows[0])
            or len({x[0].lower() for x in cols}) != len(cols)
            or any(t not in TYPES for _, t in cols)
        ):
            raise HTTPException(422, "字段映射不合法")
        for n, row in enumerate(rows[1:], 2):
            for v, (k, t) in zip(row, cols):
                try:
                    value(v, t)
                except (ValueError, InvalidOperation):
                    raise HTTPException(422, f"第 {n} 行 {k} 无法转换为 {t}")
        c.execute(
            "UPDATE data_candidates SET status='validated',mapping=%s WHERE id=%s",
            (Jsonb(m.model_dump()), id),
        )
    return {"count": len(rows) - 1, "status": "validated"}


@router.get("/api/spaces/{space}/candidates")
def queue(space: str, u=Depends(actor)):
    from .db import scope

    with db() as c:
        if (
            scope(c, u, space, "ingest") is not None
            and scope(c, u, space, "publish") is not None
        ):
            raise HTTPException(403, "无权查看导入队列")
        return list(
            c.execute(
                "SELECT id,name,owner_name,status,created FROM data_candidates WHERE space=%s ORDER BY created DESC LIMIT 60",
                (space,),
            )
        )


@router.post("/api/spaces/{space}/candidates/{id}/publish")
def publish(space: str, id: uuid.UUID, u=Depends(actor)):
    with db() as c:
        rec = candidate(c, u, space, id, "publish")
        if rec["status"] == "published":
            return {"ok": True}
        if rec["status"] != "validated":
            raise HTTPException(409, "请先完成校验")
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{space}/{rec['name']}",))
        if c.execute(
            "SELECT 1 FROM mysql_sync_checkpoints WHERE space=%s AND name=%s",
            (space, rec["name"]),
        ).fetchone():
            raise HTTPException(409, "该名称已用于 MySQL 同步")
        # Each attempt writes an unreferenced new table. Only the committed PG pointer is visible.
        table = "v_" + uuid.uuid4().hex
        cols = rec["mapping"]["columns"]
        rows = parse(bytes(rec["raw"]))[1:]
        with sr("qa_imports", True) as s, s.cursor() as cur:
            fields = ",".join(ident(x["name"]) + " " + TYPES[x["type"]] for x in cols)
            cur.execute(
                f'CREATE TABLE {ident(table)} (qa_row BIGINT,{fields}) DUPLICATE KEY(qa_row) DISTRIBUTED BY HASH(qa_row) BUCKETS 1 PROPERTIES("replication_num"="1")'
            )
            stmt = (
                f"INSERT INTO {ident(table)} VALUES ("
                + ",".join(["%s"] * (1 + len(cols)))
                + ")"
            )
            for start in range(0, len(rows), 500):
                cur.executemany(
                    stmt,
                    [
                        (i, *[value(v, col["type"]) for v, col in zip(row, cols)])
                        for i, row in enumerate(rows[start : start + 500], start + 1)
                    ],
                )
            cur.execute("SELECT COUNT(*) FROM " + ident(table))
            if cur.fetchone()[0] != len(rows):
                raise HTTPException(409, "数据库行数校验失败")
        # Stable logical table name for member policy, opaque immutable physical table for this version.
        c.execute(
            """INSERT INTO datasets(space,name,physical,description,tables,batch)
               VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(space,name) DO UPDATE
               SET physical=excluded.physical,tables=excluded.tables,
                   description=excluded.description,batch=excluded.batch""",
            (
                space,
                rec["name"],
                "qa_imports",
                table,
                Jsonb({rec["name"]: [x["name"] for x in cols]}),
                str(id),
            ),
        )
        c.execute(
            "UPDATE data_candidates SET status='published',physical=%s WHERE id=%s",
            (table, id),
        )
        log(c, u, "data.publish", f"{space}/{rec['name']} {len(rows)} 行")
    return {"ok": True, "rows": len(rows)}
