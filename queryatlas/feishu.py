import hashlib
import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .db import db, digest

router = APIRouter()
CALLBACK = "/auth/feishu/callback"


def config():
    if os.getenv("FEISHU_LOGIN_ENABLED", "0") != "1":
        return ("", "", "")
    return tuple(
        os.getenv(k, "")
        for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_REDIRECT_URI")
    )


@router.get("/api/auth/options")
def options():
    return {"feishu": all(config())}


@router.get("/auth/feishu/start")
def start():
    app, secret, redirect = config()
    if not all((app, secret, redirect)):
        raise HTTPException(503, "飞书登录尚未配置")
    state = secrets.token_urlsafe(32)
    with db() as c:
        c.execute("DELETE FROM oauth_states WHERE expires < now()")
        c.execute(
            "INSERT INTO oauth_states VALUES (%s,now()+interval '10 minutes')",
            (digest(state),),
        )
    params = {
        "client_id": app,
        "redirect_uri": redirect,
        "response_type": "code",
        "state": state,
    }
    res = RedirectResponse(
        "https://accounts.feishu.cn/open-apis/authen/v1/authorize?" + urlencode(params)
    )
    res.set_cookie(
        "qa_oauth",
        state,
        httponly=True,
        samesite="lax",
        secure=redirect.startswith("https:"),
        max_age=600,
        path=CALLBACK,
    )
    return res


@router.get(CALLBACK)
def callback(request: Request, state: str = "", code: str = ""):
    fail = RedirectResponse("/?login_error=feishu", 303)
    fail.delete_cookie("qa_oauth", path=CALLBACK)
    cookie = request.cookies.get("qa_oauth", "")
    if not cookie or not secrets.compare_digest(state, cookie):
        return fail
    with db() as c:
        if not c.execute(
            "DELETE FROM oauth_states WHERE digest=%s AND expires>now() RETURNING digest",
            (digest(state),),
        ).fetchone():
            return fail
    if not code or not all(config()):
        return fail
    app, secret, redirect = config()
    try:
        with httpx.Client(timeout=10) as client:
            t = client.post(
                "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": app,
                    "client_secret": secret,
                    "redirect_uri": redirect,
                },
            )
            t.raise_for_status()
            token = t.json()["access_token"]
            r = client.get(
                "https://open.feishu.cn/open-apis/authen/v1/user_info",
                headers={"Authorization": "Bearer " + token},
            )
            r.raise_for_status()
            profile = r.json()["data"]
            tenant, openid = profile["tenant_key"], profile["open_id"]
            if (
                not tenant
                or not openid
                or os.getenv("FEISHU_TENANT_KEY") not in (None, "", tenant)
            ):
                return fail
    except (KeyError, TypeError, ValueError, httpx.HTTPError):
        return fail
    # The configured Feishu enterprise self-built app is single-tenant. Never map by email or display name.
    person = "fs-" + hashlib.sha256((tenant + "/" + openid).encode()).hexdigest()[:32]
    session = secrets.token_urlsafe(40)
    with db() as c:
        c.execute(
            "INSERT INTO people(name,digest) VALUES(%s,%s) ON CONFLICT(name) DO NOTHING",
            (person, digest(secrets.token_urlsafe(48))),
        )
        if not c.execute(
            "SELECT 1 FROM people WHERE name=%s AND enabled", (person,)
        ).fetchone():
            return fail
        c.execute(
            """INSERT INTO external_users VALUES(%s,%s,%s,%s) ON CONFLICT(person) DO UPDATE SET display_name=excluded.display_name""",
            (person, tenant, openid, profile.get("name") or "飞书成员"),
        )
        c.execute("DELETE FROM sessions WHERE expires<now()")
        c.execute(
            "INSERT INTO sessions VALUES(%s,%s,now()+interval '8 hours')",
            (digest(session), person),
        )
    res = RedirectResponse("/", 303)
    res.delete_cookie("qa_oauth", path=CALLBACK)
    res.set_cookie(
        "qa_key",
        session,
        httponly=True,
        samesite="lax",
        secure=redirect.startswith("https:"),
        max_age=28800,
    )
    return res
