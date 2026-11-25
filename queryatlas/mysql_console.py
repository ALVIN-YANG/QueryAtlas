"""Workspace API for sync configuration and durable execution requests."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .db import db, log, require, scope
from .mysql_profiles import profiles
from .mysql_source import SourceTable, SyncRejected, SyncSpec
from .security import actor

router = APIRouter(prefix="/api/spaces/{space}/mysql-sync")


class SourceInput(BaseModel):
    profile: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=160)
    tables: dict[str, SourceTable] = Field(max_length=32)


class RunInput(BaseModel):
    mode: Literal["full", "incremental"]


def available_profiles(space):
    try:
        return profiles(space)
    except SyncRejected as error:
        raise HTTPException(503, str(error)) from error


def active_run(c, space, dataset):
    return c.execute(
        """SELECT 1 FROM mysql_sync_runs WHERE (space,dataset)=(%s,%s)
           AND status IN ('queued','running')""",
        (space, dataset),
    ).fetchone()


@router.get("")
def overview(space: str, user=Depends(actor)):
    with db() as c:
        require(c, user, space, "mysql_view")
        may_configure = scope(c, user, space, "mysql_manage") is None
        available = available_profiles(space) if may_configure else {}
        sources = c.execute(
            """SELECT s.dataset,s.description,s.profile,s.tables,d.batch
               FROM mysql_sources s LEFT JOIN datasets d ON (s.space,s.dataset)=(d.space,d.name)
               WHERE s.space=%s ORDER BY s.dataset""",
            (space,),
        ).fetchall()
        if not may_configure:
            for source in sources:
                source.pop("profile")
                source.pop("tables")
        runs = c.execute(
            """SELECT id,dataset,mode,actor,status,created,started,finished,error,result
               FROM mysql_sync_runs WHERE space=%s ORDER BY id DESC LIMIT 100""",
            (space,),
        ).fetchall()
        return {
            "profiles": [{"value": name, "label": profile.label} for name, profile in available.items()],
            "sources": sources,
            "runs": runs,
        }


@router.put("/sources/{dataset}")
def save_source(space: str, dataset: str, body: SourceInput, user=Depends(actor)):
    with db() as c:
        require(c, user, space, "mysql_manage")
        try:
            spec = SyncSpec(space=space, dataset=dataset, description=body.description, tables=body.tables)
        except ValueError as error:
            raise HTTPException(422, "请检查数据集名称和表映射") from error
        available = available_profiles(space)
        if body.profile not in available:
            raise HTTPException(422, "请选择本工作空间的 MySQL 连接")
        c.execute(
            """INSERT INTO mysql_sources(space,dataset,profile,description,tables) VALUES(%s,%s,%s,%s,%s)
               ON CONFLICT(space,dataset) DO NOTHING""",
            (space, dataset, body.profile, body.description, Jsonb(spec.model_dump()["tables"])),
        )
        c.execute("SELECT 1 FROM mysql_sources WHERE (space,dataset)=(%s,%s) FOR UPDATE", (space, dataset))
        if active_run(c, space, dataset):
            raise HTTPException(409, "同步完成后再修改数据源")
        current = c.execute(
            "SELECT fingerprint FROM mysql_sync_checkpoints WHERE (space,name)=(%s,%s)", (space, dataset)
        ).fetchone()
        if current and current["fingerprint"] != spec.fingerprint(available[body.profile]):
            raise HTTPException(409, "源库或表结构已变，请为新来源使用另一个数据集名称")
        c.execute(
            """UPDATE mysql_sources SET profile=%s,description=%s,tables=%s WHERE (space,dataset)=(%s,%s)""",
            (body.profile, body.description, Jsonb(spec.model_dump()["tables"]), space, dataset),
        )
        log(c, user, "data.mysql.configure", f"{space}/{dataset}")
        return {"dataset": dataset}


@router.post("/sources/{dataset}/runs", status_code=202)
def enqueue(space: str, dataset: str, body: RunInput, user=Depends(actor)):
    with db() as c:
        require(c, user, space, "mysql_run")
        if not c.execute(
            "SELECT 1 FROM mysql_sources WHERE (space,dataset)=(%s,%s) FOR UPDATE", (space, dataset)
        ).fetchone():
            raise HTTPException(404, "该数据源不存在")
        if active_run(c, space, dataset):
            raise HTTPException(409, "此数据源已有待执行任务")
        job = c.execute(
            """INSERT INTO mysql_sync_runs(space,dataset,mode,actor)
               VALUES (%s,%s,%s,%s) RETURNING id""",
            (space, dataset, body.mode, user["name"]),
        ).fetchone()
        log(c, user, "data.mysql.enqueue", f"{space}/{dataset} · #{job['id']} · {body.mode}")
        return job
