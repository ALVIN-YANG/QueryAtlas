"""MySQL snapshot and change-journal protocol for batch synchronization."""

import hashlib
import json
import os
import re
from contextlib import closing

import pymysql
from pymysql.cursors import SSCursor
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from .analytics import ident

TYPE = re.compile(
    r"BIGINT|INT|SMALLINT|TINYINT|DATE|DATETIME|BOOLEAN|DOUBLE|"
    r"VARCHAR\([1-9][0-9]{0,3}\)|DECIMAL\([1-9][0-9]?,[0-9]{1,2}\)"
)
CHUNK = 30_000


class SyncRejected(ValueError):
    """An operator-facing reason that contains no database error text."""


class SourceTable(BaseModel):
    key: list[str]
    columns: dict[str, str]

    @model_validator(mode="after")
    def validate_columns(self):
        if not self.key or len(set(self.key)) != len(self.key) or not set(self.key) <= self.columns.keys():
            raise ValueError("primary key must contain distinct configured columns")
        for name, kind in self.columns.items():
            ident(name)
            if not TYPE.fullmatch(kind):
                raise ValueError(f"unsupported column type: {name}")
        if any(self.columns[k] not in {"BIGINT", "INT", "SMALLINT", "TINYINT"} for k in self.key):
            raise ValueError("sync primary keys must be integers")
        return self

    @property
    def fields(self):
        return [*self.key, *(name for name in self.columns if name not in self.key)]


class SyncSpec(BaseModel):
    space: str = Field(min_length=1, max_length=80)
    dataset: str
    description: str
    tables: dict[str, SourceTable]

    @field_validator("dataset")
    @classmethod
    def logical_name(cls, value):
        ident(value)
        if value.lower() in {"tpch", "ssb", "tpcds"}:
            raise ValueError("choose a dataset name outside the built-in benchmarks")
        return value

    @field_validator("tables")
    @classmethod
    def table_names(cls, tables):
        if not tables or "qa_etl_changes" in tables:
            raise ValueError("configure at least one source table")
        for name in tables:
            ident(name)
        return tables

    def fingerprint(self, connection):
        contract = {
            "host": connection.host,
            "port": str(connection.port),
            "database": connection.database,
            "tables": {k: v.model_dump() for k, v in self.tables.items()},
        }
        return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()


class MysqlConnection(BaseModel):
    host: str
    port: int = Field(default=3306, ge=1, le=65535)
    database: str
    user: str
    password: SecretStr
    ca: str | None = None

    @classmethod
    def from_environment(cls):
        return cls(
            host=os.environ["QA_ETL_MYSQL_HOST"],
            port=int(os.getenv("QA_ETL_MYSQL_PORT", "3306")),
            database=os.environ["QA_ETL_MYSQL_DB"],
            user=os.environ["QA_ETL_MYSQL_USER"],
            password=os.environ["QA_ETL_MYSQL_PASSWORD"],
            ca=os.getenv("QA_ETL_MYSQL_CA"),
        )


def mysql(*, writer=False, connection=None):
    options = connection or MysqlConnection.from_environment()
    return pymysql.connect(
        host=options.host,
        port=options.port,
        database=options.database,
        user=options.user,
        password=options.password.get_secret_value(),
        local_infile=writer,
        connect_timeout=5,
        read_timeout=120,
        charset="utf8mb4",
        **({"ssl_ca": options.ca, "ssl_verify_identity": True} if options.ca else {}),
    )


def check_schema(cur, spec):
    for name, key in [("qa_etl_changes", []), *((n, t.key) for n, t in spec.tables.items())]:
        cur.execute("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (name,))
        if cur.fetchone() != ("InnoDB",):
            raise SyncRejected(f"{name}: an InnoDB source table is required")
        if key:
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY' "
                "ORDER BY ORDINAL_POSITION",
                (name,),
            )
            if [row[0] for row in cur] != key:
                raise SyncRejected(f"{name}: configured key differs from the MySQL primary key")


def keys_in(fields, keys):
    key_columns = ",".join(map(ident, fields))
    placeholders = ",".join(["%s"] * len(fields))
    if len(fields) == 1:
        return f"{key_columns} IN ({','.join(['%s'] * len(keys))})", tuple(key[0] for key in keys)
    return f"({key_columns}) IN ({','.join([f'({placeholders})'] * len(keys))})", tuple(
        value for key in keys for value in key
    )


def changes(cur, name, table, after, head):
    cur.execute(
        "SELECT key_json FROM qa_etl_changes WHERE table_name=%s AND batch_id>%s AND batch_id<=%s",
        (name, after, head),
    )
    unique = set()
    for (raw,) in cur:
        key = json.loads(raw)
        if not isinstance(key, list) or len(key) != len(table.key) or any(type(v) is not int for v in key):
            raise SyncRejected(f"{name}: invalid key in MySQL change journal")
        unique.add(tuple(key))
    return sorted(unique)


def rows(conn, name, table, where="", args=()):
    fields = ",".join(map(ident, table.fields))
    with closing(conn.cursor(SSCursor)) as cur:
        cur.execute(f"SELECT {fields} FROM {ident(name)} {where}", args)
        while batch := cur.fetchmany(CHUNK):
            yield batch
