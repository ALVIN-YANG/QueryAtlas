"""Run queued MySQL synchronization outside the web request lifecycle."""

import logging
import time
from contextlib import closing

import httpx
import pymysql
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .db import db
from .mysql_profiles import get_profile
from .mysql_source import SyncRejected, SyncSpec
from .mysql_sync import sync

logger = logging.getLogger(__name__)


def failure(error):
    if isinstance(error, SyncRejected):
        exact = {
            "publish a full sync before running incremental sync": "请先完成一次全量同步",
            "dataset name is in use by a different import": "该数据集名称已被其他导入占用",
            "source or schema changed; use a new dataset name": "源库或表结构已变，请使用新的数据集名称",
            "source change journal moved backward": "源库变更批次回退，请检查变更表",
        }
        return exact.get(str(error), str(error))[:500]
    if isinstance(error, HTTPException) and error.status_code == 403:
        return "操作人已失去当前工作空间的同步权限"
    if isinstance(error, pymysql.MySQLError):
        code = error.args[0] if error.args and isinstance(error.args[0], int) else 0
        known = {
            1044: "数据库账号缺少访问权限",
            1045: "数据库连接认证失败",
            1054: "字段不存在，请检查表映射",
            1142: "数据库账号缺少表操作权限",
            1146: "数据库表不存在，请检查源表和变更表",
            2003: "数据库连接失败，请检查连接和网络",
            2013: "数据库连接中断，请检查连接和网络",
        }
        return known.get(code, f"数据库操作失败（错误码 {code}）")
    if isinstance(error, httpx.HTTPError):
        return "StarRocks 写入请求失败，请检查 Stream Load 服务"
    return "同步运行失败，请联系管理员并提供任务编号"


def claim(c):
    return c.execute(
        """UPDATE mysql_sync_runs SET status='running',started=now()
           WHERE id=(SELECT id FROM mysql_sync_runs WHERE status='queued' ORDER BY id LIMIT 1)
           RETURNING *"""
    ).fetchone()


def record(c, run, result):
    payload = {k: v for k, v in result.items() if k != "physical"}
    if not c.execute(
        """UPDATE mysql_sync_runs SET status=%s,result=%s,finished=clock_timestamp()
           WHERE id=%s AND status='running' RETURNING id""",
        (result["status"], Jsonb(payload), run["id"]),
    ).fetchone():
        raise SyncRejected("任务已中断，请重新触发")


def execute(run):
    try:
        with db() as c:
            source = c.execute(
                "SELECT * FROM mysql_sources WHERE (space,dataset)=(%s,%s)",
                (run["space"], run["dataset"]),
            ).fetchone()
        profile = get_profile(run["space"], source["profile"])
        sync(
            SyncSpec.model_validate(source),
            run["mode"],
            run["actor"],
            connection=profile,
            record_result=lambda c, result: record(c, run, result),
        )
    except Exception as error:
        logger.warning("MySQL sync task %s failed (%s)", run["id"], type(error).__name__)
        with db() as c:
            c.execute(
                """UPDATE mysql_sync_runs SET status='failed',error=%s,finished=now()
                   WHERE id=%s AND status='running'""",
                (failure(error), run["id"]),
            )


def worker():
    with closing(db()) as leader:
        leader.autocommit = True
        while not leader.execute(
            "SELECT pg_try_advisory_lock(hashtextextended('queryatlas:mysql-worker',0)) AS acquired"
        ).fetchone()["acquired"]:
            time.sleep(2)
        leader.execute(
            """UPDATE mysql_sync_runs SET status='failed',error='同步进程中断，请重新触发',finished=now()
               WHERE status='running'"""
        )
        while True:
            run = claim(leader)
            if run:
                execute(run)
            else:
                time.sleep(2)


if __name__ == "__main__":
    worker()
