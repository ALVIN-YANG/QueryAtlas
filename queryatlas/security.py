from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from psycopg.errors import IntegrityError

from .db import ACTIONS, db, identify, issue, log, root

router = APIRouter()
bearer = HTTPBearer(auto_error=False, scheme_name="ServiceToken")


def key_from(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)
):
    return credentials.credentials if credentials else request.cookies.get("qa_key", "")


def actor(key: str = Depends(key_from)):
    with db() as conn:
        return identify(conn, key)


class Change(BaseModel):
    kind: Literal["space", "account", "role", "grant", "revoke", "disable"]
    name: str = Field(min_length=1, max_length=80, pattern=r"^[\w.\-\u3400-\u9fff]+$")
    actions: list[str] = Field(default_factory=list, max_length=12)
    space: str = Field(default="", max_length=80)
    role: str = Field(default="", max_length=80)
    asset: str = Field(default="", max_length=180)


@router.get("/api/admin")
def directory(user=Depends(actor)):
    root(user)
    with db() as c:
        return {
            "spaces": [
                r["name"] for r in c.execute("SELECT name FROM spaces ORDER BY name")
            ],
            "people": c.execute(
                """SELECT p.name,p.enabled,p.root,e.display_name,CASE WHEN e.person IS NULL THEN '密钥' ELSE '飞书' END AS origin FROM people p LEFT JOIN external_users e ON e.person=p.name ORDER BY p.name"""
            ).fetchall(),
            "roles": c.execute("SELECT * FROM roles ORDER BY name").fetchall(),
            "grants": c.execute(
                "SELECT * FROM grants_ ORDER BY person,space,role,asset"
            ).fetchall(),
            "actions": sorted(ACTIONS),
            "audit": c.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT 40"
            ).fetchall(),
        }


@router.post("/api/admin")
def change(body: Change, user=Depends(actor)):
    root(user)
    try:
        with db() as c:
            token = None
            if body.kind == "account":
                token = issue(c, body.name)
            elif body.kind == "space":
                c.execute("INSERT INTO spaces VALUES (%s)", (body.name,))
            elif body.kind == "role":
                if not body.actions or set(body.actions) - ACTIONS:
                    raise HTTPException(422, "动作不合法")
                c.execute(
                    "INSERT INTO roles VALUES (%s,%s)",
                    (body.name, sorted(set(body.actions))),
                )
            elif body.kind == "disable":
                if not c.execute(
                    "UPDATE people SET enabled=false WHERE name=%s AND NOT root RETURNING name",
                    (body.name,),
                ).fetchone():
                    raise HTTPException(422, "账号不存在或为引导管理员")
            else:
                key = (body.name, body.space, body.role, body.asset)
                if body.kind == "grant":
                    role = c.execute(
                        "SELECT actions FROM roles WHERE name=%s", (body.role,)
                    ).fetchone()
                    if body.asset and (
                        not role
                        or set(role["actions"])
                        - (
                            {"analysis", "sql", "detail"}
                            if body.asset.startswith("@data/")
                            else {"search", "original"}
                        )
                    ):
                        raise HTTPException(422, "所选动作为空间级权限或与资源类型不符")
                    c.execute(
                        "INSERT INTO grants_ VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        key,
                    )
                elif not c.execute(
                    """DELETE FROM grants_ WHERE person=%s AND space=%s AND role=%s
                                      AND asset=%s RETURNING person""",
                    key,
                ).fetchone():
                    raise HTTPException(404, "授权不存在")
            log(c, user, body.kind, body.model_dump_json(exclude_defaults=True))
            return {"ok": True, "token": token}
    except IntegrityError:
        raise HTTPException(409, "名称重复或所选空间、角色、账号不存在")
