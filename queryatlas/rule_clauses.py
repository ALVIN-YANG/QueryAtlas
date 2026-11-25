"""Resolve published document locations for SQL template authors and readers."""

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .db import scope


class RuleClause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(gt=0, le=2**53 - 1, strict=True)
    seq: int = Field(ge=0, le=2**31 - 1, strict=True)


def documents(c, user, space):
    allowed = scope(c, user, space, "search")
    return c.execute(
        """SELECT asset,version FROM docs WHERE space=%s
           AND (%s::text[] IS NULL OR asset=ANY(%s)) ORDER BY asset""",
        (space, allowed, allowed),
    ).fetchall()


def document_clauses(c, user, space, version):
    allowed = scope(c, user, space, "search")
    document = c.execute(
        """SELECT j.asset FROM jobs j JOIN docs d ON (d.space,d.asset)=(j.space,j.asset)
           WHERE j.space=%s AND j.id=%s AND j.status='ready'
             AND (%s::text[] IS NULL OR j.asset=ANY(%s))""",
        (space, version, allowed, allowed),
    ).fetchone()
    if not document:
        raise HTTPException(404, "制度文档不可查看")
    return c.execute(
        """SELECT version,seq,anchor,left(body,160) AS excerpt
           FROM nodes WHERE version=%s ORDER BY seq""",
        (version,),
    ).fetchall()


def resolve_clauses(c, user, space, clauses):
    if not clauses:
        return []
    allowed = scope(c, user, space, "search")
    if allowed == []:
        return []
    return c.execute(
        """SELECT j.asset,n.version,n.seq,n.anchor,n.body
           FROM unnest(%s::bigint[],%s::int[]) WITH ORDINALITY AS ref(version,seq,position)
           JOIN nodes n ON (n.version,n.seq)=(ref.version,ref.seq)
           JOIN jobs j ON j.id=n.version
           JOIN docs d ON (d.space,d.asset)=(j.space,j.asset)
           WHERE j.space=%s AND j.status='ready'
             AND (%s::text[] IS NULL OR j.asset=ANY(%s))
           ORDER BY ref.position""",
        ([r["version"] for r in clauses], [r["seq"] for r in clauses], space, allowed, allowed),
    ).fetchall()


def require_clauses(c, user, space, clauses):
    if len(resolve_clauses(c, user, space, clauses)) != len(clauses):
        raise HTTPException(422, "部分制度条款不可查看，请重新选择有权限的段落")
