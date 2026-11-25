"""Workspace-managed SQL templates and import of legacy deployment files."""

import os
from pathlib import Path
from typing import Annotated, NamedTuple

import pymysql
from fastapi import APIRouter, Depends, HTTPException, Path as RoutePath
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, field_validator

from .analytics import catalog, dataset, execute, permitted, sr
from .answers import prepare_sql
from .db import db, log, require
from .rule_clauses import RuleClause, document_clauses, documents, require_clauses, resolve_clauses
from .security import actor
from .sql_template_parameters import (
    ParamName, Parameters, alias_pattern, bind_sql, normalize,
)


router = APIRouter(prefix="/api/spaces/{space}/sql-templates")
ID_PATTERN = r"^[a-zA-Z0-9_-]{1,80}$"
TemplateId = Annotated[str, Field(pattern=ID_PATTERN)]
PathId = Annotated[str, RoutePath(pattern=ID_PATTERN)]
DatasetName = Annotated[str, Field(min_length=1, max_length=180)]
SqlText = Annotated[str, Field(min_length=1, max_length=20000)]
Alias = Annotated[str, Field(min_length=1, max_length=500)]
Revision = Annotated[int, Field(ge=1)]
PREVIEW_ROWS = 20


class DraftQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dataset: DatasetName
    sql: SqlText
    parameters: Parameters = Field(default_factory=dict)


class PreviewQuery(DraftQuery):
    values: dict[ParamName, str | StrictInt] = Field(default_factory=dict, max_length=3)


class TemplateContent(DraftQuery):
    aliases: tuple[Alias, ...] = Field(min_length=1, max_length=20)
    definition: str = Field(default="", max_length=650)
    rule_clauses: tuple[RuleClause, ...] | None = Field(default=None, max_length=5)

    @field_validator("aliases")
    @classmethod
    def unique_aliases(cls, aliases):
        normalized = {normalize(alias) for alias in aliases}
        if "" in normalized or len(normalized) != len(aliases):
            raise ValueError("触发问句不能为空或重复")
        return aliases

    @field_validator("rule_clauses")
    @classmethod
    def unique_clauses(cls, clauses):
        if clauses and len({(r.version, r.seq) for r in clauses}) != len(clauses):
            raise ValueError("制度条款不能重复")
        return clauses


class NewTemplate(TemplateContent):
    id: TemplateId


class SqlTemplate(NewTemplate):
    space: str = Field(min_length=1, max_length=80)


class TemplateEdit(TemplateContent):
    revision: Revision


class TemplateStatus(BaseModel):
    enabled: bool
    revision: Revision


class TemplateMatch(NamedTuple):
    id: str
    sql: str
    values: dict | None = None
    definition: str = ""
    problem: str = ""
    revision: int = 1
    rule_clauses: tuple = ()


def decode_parameters(record):
    return TypeAdapter(Parameters).validate_python(record)


def matching_templates(c, space, question, allowed_datasets):
    if not allowed_datasets:
        return {}
    question = normalize(question)
    options = {}
    exact = set()
    for row in c.execute(
        """
        SELECT t.dataset,t.id,t.sql,t.parameters,t.definition,t.revision,t.rule_clauses,a.normalized
        FROM sql_template_aliases a
        JOIN sql_templates t ON (t.space,t.id,t.dataset)=(a.space,a.template_id,a.dataset)
        WHERE a.space=%s AND a.dataset=ANY(%s) AND t.enabled
          AND (a.normalized=%s OR t.parameters<>'{}'::jsonb)
        ORDER BY (a.normalized=%s) DESC,t.id,a.position
        """,
        (space, allowed_datasets, question, question),
    ):
        name = row["dataset"]
        if name in exact:
            continue
        parameters = decode_parameters(row["parameters"])
        if row["normalized"] == question:
            exact.add(name)
            raw = {}
        else:
            captured = alias_pattern(row["normalized"], parameters).fullmatch(question)
            if not captured:
                continue
            raw = captured.groupdict()
        try:
            sql, values = bind_sql(row["sql"], parameters, raw)
            candidate = TemplateMatch(
                row["id"], sql, values, row["definition"],
                revision=row["revision"], rule_clauses=tuple(row["rule_clauses"]),
            )
        except ValueError as exc:
            candidate = TemplateMatch(row["id"], "", problem=str(exc))
        options.setdefault(name, []).append(candidate)
    matches = {}
    for dataset_name, candidates in options.items():
        viable = [candidate for candidate in candidates if not candidate.problem]
        pool = viable or candidates
        matches[dataset_name] = pool[0] if all(item == pool[0] for item in pool) else None
    return matches


def require_management(c, user, space):
    require(c, user, space, "templates")
    if not c.execute("SELECT 1 FROM spaces WHERE name=%s", (space,)).fetchone():
        raise HTTPException(404, "工作空间不存在")


def author_dataset(c, user, space, name):
    meta, policy = dataset(c, user, space, name)
    if not permitted(c, user, space, "sql", name):
        raise HTTPException(403, "管理该模板需要相应数据集的 SQL 权限")
    return meta, policy


def bound_sql(sql, parameters, values):
    try:
        return bind_sql(sql, parameters, values)[0]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def check_statement(meta, policy, sql, parameters):
    values = {name: p.example() for name, p in parameters.items()}
    statement = prepare_sql(bound_sql(sql, parameters, values), meta, policy)
    try:
        with sr(meta["physical"]) as conn, conn.cursor() as cur:
            cur.execute("EXPLAIN " + statement)
    except pymysql.MySQLError as exc:
        raise HTTPException(422, "SQL 无法在当前数据集和访问范围中执行") from exc


def write_aliases(c, space, id, content):
    try:
        for alias in content.aliases:
            alias_pattern(normalize(alias), content.parameters)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with c.cursor() as cur:
        cur.executemany(
            """INSERT INTO sql_template_aliases(space,template_id,dataset,normalized,alias,position)
               VALUES(%s,%s,%s,%s,%s,%s)""",
            [
                (space, id, content.dataset, normalize(alias), alias, position)
                for position, alias in enumerate(content.aliases)
            ],
        )


def insert_template(c, space, content, *, enabled=False, import_existing=False):
    data = content.model_dump()
    sql = """INSERT INTO sql_templates(space,id,dataset,sql,enabled,parameters,definition,rule_clauses)
             VALUES(%s,%s,%s,%s,%s,%s,%s,%s)"""
    if import_existing:
        sql += " ON CONFLICT(space,id) DO NOTHING"
    row = c.execute(sql + " RETURNING *", (
        space, content.id, content.dataset, content.sql, enabled,
        Jsonb(data["parameters"]), content.definition, Jsonb(data["rule_clauses"] or []),
    )).fetchone()
    if row:
        write_aliases(c, space, content.id, content)
    return row


def current(c, user, space, id, revision):
    row = c.execute(
        "SELECT * FROM sql_templates WHERE space=%s AND id=%s FOR UPDATE",
        (space, id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "模板不存在")
    meta, policy = author_dataset(c, user, space, row["dataset"])
    if row["revision"] != revision:
        raise HTTPException(409, "模板已被修改，请刷新列表后重新操作")
    return row, meta, policy


@router.get("")
def list_templates(space: str, user=Depends(actor)):
    with db() as c:
        require_management(c, user, space)
        datasets = [
            {"name": row["name"], "detail": permitted(c, user, space, "detail", row["name"])}
            for row in catalog(c, user, space)
            if permitted(c, user, space, "sql", row["name"])
        ]
        rows = c.execute(
            """
            SELECT t.*, ARRAY(
                SELECT a.alias FROM sql_template_aliases a
                WHERE a.space=t.space AND a.template_id=t.id ORDER BY a.position
            ) AS aliases
            FROM sql_templates t WHERE t.space=%s AND t.dataset=ANY(%s)
            ORDER BY t.updated DESC,t.id
            """,
            (space, [row["name"] for row in datasets]),
        ).fetchall()
        refs = {
            (ref["version"], ref["seq"]): ref for row in rows for ref in row["rule_clauses"]
        }
        visible = {
            (ref["version"], ref["seq"]): {k: v for k, v in ref.items() if k != "body"}
            for ref in resolve_clauses(c, user, space, list(refs.values()))
        }
        for row in rows:
            keys = [(ref["version"], ref["seq"]) for ref in row["rule_clauses"]]
            row["rules_restricted"] = any(key not in visible for key in keys)
            row["rule_clauses"] = [visible[key] for key in keys if key in visible]
            if row["rules_restricted"]:
                row["definition"] = ""
    return {"templates": rows, "datasets": datasets}


@router.get("/rule-documents")
def rule_documents(space: str, user=Depends(actor)):
    with db() as c:
        require_management(c, user, space)
        return documents(c, user, space)


@router.get("/rule-documents/{version}/clauses")
def rule_document_clauses(space: str, version: Annotated[int, RoutePath(gt=0, le=2**53 - 1)], user=Depends(actor)):
    with db() as c:
        require_management(c, user, space)
        return document_clauses(c, user, space, version)


@router.post("")
def create_template(space: str, body: NewTemplate, user=Depends(actor)):
    try:
        with db() as c:
            require_management(c, user, space)
            meta, policy = author_dataset(c, user, space, body.dataset)
            require_clauses(c, user, space, body.model_dump()["rule_clauses"] or [])
            check_statement(meta, policy, body.sql, body.parameters)
            row = insert_template(c, space, body)
            log(c, user, "sql_template.create", f"{space}/{body.id}")
            return row
    except UniqueViolation as exc:
        raise HTTPException(409, "模板编号或该数据集的触发问句已存在") from exc


@router.put("/{id}")
def edit_template(space: str, id: PathId, body: TemplateEdit, user=Depends(actor)):
    try:
        with db() as c:
            require_management(c, user, space)
            existing, _, _ = current(c, user, space, id, body.revision)
            meta, policy = author_dataset(c, user, space, body.dataset)
            data = body.model_dump()
            clauses = data["rule_clauses"]
            if clauses is None:
                clauses = existing["rule_clauses"]
            require_clauses(c, user, space, existing["rule_clauses"])
            if list(clauses) != existing["rule_clauses"]:
                require_clauses(c, user, space, clauses)
            check_statement(meta, policy, body.sql, body.parameters)
            c.execute(
                "DELETE FROM sql_template_aliases WHERE space=%s AND template_id=%s",
                (space, id),
            )
            row = c.execute(
                """UPDATE sql_templates SET dataset=%s,sql=%s,parameters=%s,definition=%s,rule_clauses=%s,
                   revision=revision+1,updated=now()
                   WHERE space=%s AND id=%s RETURNING *""",
                (body.dataset, body.sql, Jsonb(data["parameters"]), body.definition, Jsonb(clauses), space, id),
            ).fetchone()
            write_aliases(c, space, id, body)
            log(c, user, "sql_template.edit", f"{space}/{id}")
            return row
    except UniqueViolation as exc:
        raise HTTPException(409, "该数据集的触发问句已存在") from exc


@router.post("/{id}/status")
def change_status(space: str, id: PathId, body: TemplateStatus, user=Depends(actor)):
    with db() as c:
        require_management(c, user, space)
        row, meta, policy = current(c, user, space, id, body.revision)
        if row["enabled"] != body.enabled:
            if body.enabled:
                require_clauses(c, user, space, row["rule_clauses"])
                check_statement(meta, policy, row["sql"], decode_parameters(row["parameters"]))
            row = c.execute(
                """UPDATE sql_templates SET enabled=%s,revision=revision+1,updated=now()
                   WHERE space=%s AND id=%s RETURNING *""",
                (body.enabled, space, id),
            ).fetchone()
            log(c, user, "sql_template.enable" if body.enabled else "sql_template.disable", f"{space}/{id}")
        return {k: v for k, v in row.items() if k not in {"rule_clauses", "definition"}}


@router.post("/preview")
def preview_template(space: str, body: PreviewQuery, user=Depends(actor)):
    with db() as c:
        require_management(c, user, space)
        meta, policy = author_dataset(c, user, space, body.dataset)
        if not permitted(c, user, space, "detail", body.dataset):
            raise HTTPException(403, "试跑需要该数据集的数据明细权限")
        sql = bound_sql(body.sql, body.parameters, body.values)
        stmt = prepare_sql(sql, meta, policy, max_rows=PREVIEW_ROWS + 1)
        result = execute(meta["physical"], stmt)
        result["truncated"] = len(result["rows"]) > PREVIEW_ROWS
        result["rows"] = result["rows"][:PREVIEW_ROWS]
        log(c, user, "sql_template.preview", f"{space}/{body.dataset}")
        return result


def import_file(path):
    records = TypeAdapter(list[SqlTemplate]).validate_json(Path(path).read_bytes())
    with db() as c:
        return sum(
            insert_template(c, row.space, row, enabled=True, import_existing=True)
            is not None
            for row in records
        )


if __name__ == "__main__":
    print(f"Imported {import_file(os.environ['QA_SQL_TEMPLATE_FILE'])} SQL templates")
