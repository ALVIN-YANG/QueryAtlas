"""Publish a verified, immutable StarRocks version from a MySQL snapshot."""

import argparse
import json
import os
import time
import uuid
from contextlib import closing
from pathlib import Path

import httpx
import pymysql
from psycopg.types.json import Jsonb

from .analytics import ident
from .db import db, log, require
from .mysql_source import MysqlConnection, SyncRejected, SyncSpec, changes, check_schema, keys_in, mysql, rows


class Warehouse:
    def __init__(self, conn, client, namespace):
        self.cur = conn.cursor()
        self.client = client
        self.name = namespace
        self.schema = ident(namespace)

    def table(self, name):
        return f"{self.schema}.{ident(name)}"

    def create(self, name, spec):
        key = ",".join(map(ident, spec.key))
        columns = ", ".join(
            f"{ident(col)} {spec.columns[col]} {'NOT NULL' if col in spec.key else 'NULL'}"
            for col in spec.fields
        )
        self.cur.execute(
            f"CREATE TABLE {self.table(name)} ({columns}) PRIMARY KEY ({key}) "
            f"DISTRIBUTED BY HASH ({ident(spec.key[0])}) BUCKETS 4 "
            'PROPERTIES("replication_num"="1")'
        )

    def load(self, name, spec, values):
        payload = json.dumps(
            [dict(zip(spec.fields, row)) for row in values],
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
        url = f"{os.environ['QA_ETL_SR_LOAD_URL'].rstrip('/')}/api/{self.name}/{name}/_stream_load"
        reply = self.client.put(
            url,
            content=payload,
            headers={
                "Expect": "100-continue",
                "format": "json",
                "strip_outer_array": "true",
                "strict_mode": "true",
                "max_filter_ratio": "0",
                "timeout": "120",
                "label": "qa_sync_" + uuid.uuid4().hex,
            },
        )
        reply.raise_for_status()
        result = reply.json()
        if result.get("Status") != "Success" or result.get("NumberLoadedRows") != len(values):
            raise SyncRejected(f"{name}: Stream Load failed ({result.get('Status')}, {result.get('NumberFilteredRows')} filtered)")

    def delete(self, name, fields, keys):
        if len(fields) == 1:
            predicate, params = keys_in(fields, keys)
        else:
            # StarRocks parses tuple IN with a subquery, not a list of literal tuples.
            match = "(" + " AND ".join(ident(field) + "=%s" for field in fields) + ")"
            predicate = " OR ".join([match] * len(keys))
            params = tuple(value for key in keys for value in key)
        self.cur.execute(f"DELETE FROM {self.table(name)} WHERE {predicate}", params)

    def count(self, name):
        self.cur.execute(f"SELECT COUNT(*) FROM {self.table(name)}")
        return int(self.cur.fetchone()[0])


def warehouse_connection():
    return pymysql.connect(
        host=os.environ["QA_ETL_SR_HOST"],
        port=int(os.getenv("QA_ETL_SR_PORT", "9030")),
        user=os.environ["QA_ETL_SR_USER"],
        password=os.environ["QA_ETL_SR_PASSWORD"],
        connect_timeout=5,
        read_timeout=180,
        autocommit=True,
        charset="utf8mb4",
    )


def transfer(source, cur, store, name, table, previous, mode, head):
    store.create(name, table)
    read = deleted = 0
    if mode == "full":
        for batch in rows(source, name, table):
            store.load(name, table, batch)
            read += len(batch)
    else:
        dest = store.table(name)
        fields = ",".join(map(ident, table.fields))
        store.cur.execute(
            f"INSERT INTO {dest} ({fields}) SELECT {fields} FROM {ident(previous['physical'])}.{ident(name)}"
        )
        keys = changes(cur, name, table, previous["watermark"], head)
        for start in range(0, len(keys), 500):
            group = keys[start : start + 500]
            predicate, params = keys_in(table.key, group)
            present = set()
            for batch in rows(source, name, table, f"WHERE {predicate}", params):
                present.update(row[: len(table.key)] for row in batch)
                store.load(name, table, batch)
                read += len(batch)
            removed = [key for key in group if key not in present]
            if removed:
                store.delete(name, table.key, removed)
                deleted += len(removed)
    cur.execute(f"SELECT COUNT(*) FROM {ident(name)}")
    (expected,) = cur.fetchone()
    actual = store.count(name)
    if actual != expected:
        raise SyncRejected(f"{name}: row count differs (source {expected}, target {actual})")
    return {"rows": actual, "read": read, "delete_keys": deleted}


def prepare(c, spec, mode, operator, fingerprint, *, managed):
    ref = f"{spec.space}/{spec.dataset}"
    c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (ref,))
    if managed:
        authorize_job(c, spec.space, operator)
    elif not c.execute("SELECT 1 FROM people WHERE name=%s AND root AND enabled", (operator,)).fetchone():
        raise SyncRejected("operator must be an enabled platform admin")
    if not c.execute("SELECT 1 FROM spaces WHERE name=%s", (spec.space,)).fetchone():
        raise SyncRejected("workspace does not exist")
    record = c.execute(
        "SELECT physical FROM datasets WHERE space=%s AND name=%s FOR UPDATE",
        (spec.space, spec.dataset),
    ).fetchone()
    checkpoint = c.execute(
        "SELECT physical,watermark,fingerprint FROM mysql_sync_checkpoints WHERE space=%s AND name=%s",
        (spec.space, spec.dataset),
    ).fetchone()
    if record and (not checkpoint or checkpoint["physical"] != record["physical"]):
        raise SyncRejected("dataset name is in use by a different import")
    if checkpoint and checkpoint["fingerprint"] != fingerprint:
        raise SyncRejected("source or schema changed; use a new dataset name")
    if mode == "incremental" and not checkpoint:
        raise SyncRejected("publish a full sync before running incremental sync")
    return checkpoint


def authorize_job(c, space, operator):
    actor = c.execute(
        "SELECT name,root FROM people WHERE name=%s AND enabled", (operator,)
    ).fetchone()
    if not actor:
        raise SyncRejected("任务操作人已停用")
    require(c, actor, space, "mysql_run")


def publish(c, spec, store, head, stats, batch, operator, fingerprint):
    c.execute(
        """INSERT INTO datasets(space,name,physical,description,tables,batch) VALUES(%s,%s,%s,%s,%s,%s)
           ON CONFLICT(space,name) DO UPDATE SET physical=EXCLUDED.physical,
             description=EXCLUDED.description,tables=EXCLUDED.tables,batch=EXCLUDED.batch""",
        (spec.space, spec.dataset, store.name, spec.description,
         Jsonb({name: table.fields for name, table in spec.tables.items()}), batch),
    )
    c.execute(
        """INSERT INTO mysql_sync_checkpoints VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT(space,name) DO UPDATE SET watermark=EXCLUDED.watermark, physical=EXCLUDED.physical""",
        (spec.space, spec.dataset, fingerprint, head, store.name),
    )
    counts = "，".join(f"{table} {info['rows']} 行" for table, info in stats.items())
    log(c, {"name": operator}, "data.mysql.sync", f"{spec.space}/{spec.dataset} · {batch} · {counts}")


def sync(spec, mode, operator, *, connection=None, record_result=None):
    started = time.monotonic()
    auth = (os.environ["QA_ETL_SR_USER"], os.environ["QA_ETL_SR_PASSWORD"])
    connection = connection or MysqlConnection.from_environment()
    fingerprint = spec.fingerprint(connection)
    with db() as c:
        previous = prepare(c, spec, mode, operator, fingerprint, managed=record_result is not None)
        with closing(mysql(connection=connection)) as source, source.cursor() as cur:
            return transfer_snapshot(c, source, cur, spec, mode, operator, previous, fingerprint,
                                     auth, started, record_result)


def transfer_snapshot(c, source, cur, spec, mode, operator, previous, fingerprint,
                      auth, started, record_result):
    cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
    check_schema(cur, spec)
    cur.execute("SELECT COALESCE(MAX(batch_id),0) FROM qa_etl_changes")
    head = int(cur.fetchone()[0])
    if previous and head < previous["watermark"]:
        raise SyncRejected("source change journal moved backward")
    if mode == "incremental" and head == previous["watermark"]:
        result = {"status": "unchanged", "watermark": head}
        if record_result:
            record_result(c, result)
        return result
    with closing(warehouse_connection()) as conn, httpx.Client(auth=auth, timeout=130, trust_env=False) as client:
        suffix = uuid.uuid4().hex
        store = Warehouse(conn, client, "qa_mysql_" + suffix)
        batch = f"mysql-{head}-{suffix[:12]}"
        reader = ident(os.getenv("QA_ETL_SR_READER", "qa_read"))
        store.cur.execute(f"CREATE DATABASE {store.schema}")
        try:
            stats = {
                name: transfer(source, cur, store, name, table, previous, mode, head)
                for name, table in spec.tables.items()
            }
            store.cur.execute(f"GRANT SELECT ON ALL TABLES IN DATABASE {store.schema} TO USER {reader}")
            result = {
                "status": "published", "mode": mode, "batch": batch, "watermark": head,
                "physical": store.name, "tables": stats, "seconds": round(time.monotonic() - started, 3),
            }
            if record_result:
                authorize_job(c, spec.space, operator)
                record_result(c, result)
            publish(c, spec, store, head, stats, batch, operator, fingerprint)
        except Exception:
            store.cur.execute(f"DROP DATABASE {store.schema}")
            raise
        # Commit outcome can be unknown after a connection loss. Keep the version in that case.
        c.commit()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["full", "incremental"])
    parser.add_argument("spec", type=Path)
    parser.add_argument("--operator", default="admin")
    args = parser.parse_args()
    print(json.dumps(sync(SyncSpec.model_validate_json(args.spec.read_text()), args.mode, args.operator), ensure_ascii=False))
