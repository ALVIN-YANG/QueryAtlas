"""Independent watchdog. Keep incidents and deliveries available during PostgreSQL outages."""

import base64
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib import request

import httpx

from .db import db
from .llm import DEEPSEEK_MODEL, DEEPSEEK_URL, deepseek_headers, uses_deepseek

STATE = Path(os.getenv("MONITOR_DB", "/monitor/events.db"))
INTERVAL = 10
_last_deepseek_probe = (float("-inf"), "待探测")


def connect():
    c = sqlite3.connect(STATE, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_monitor():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY, space TEXT, rule TEXT, target TEXT,
          opened INTEGER, last_seen INTEGER, ack_by TEXT, ack_at INTEGER, closed INTEGER);
        CREATE UNIQUE INDEX IF NOT EXISTS active ON incidents(space,rule,target) WHERE closed IS NULL;
        CREATE TABLE IF NOT EXISTS delivery(id INTEGER PRIMARY KEY, incident INTEGER, event TEXT, due INTEGER,
          tries INTEGER DEFAULT 0, sent INTEGER, error TEXT);
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, val INTEGER);
        """)


def signal(c, space, rule, target, bad):
    row = c.execute(
        "SELECT id FROM incidents WHERE space=? AND rule=? AND target=? AND closed IS NULL",
        (space, rule, target),
    ).fetchone()
    now = int(time.time())
    if bad and row:
        c.execute("UPDATE incidents SET last_seen=? WHERE id=?", (now, row["id"]))
        return
    if bad:
        ident = c.execute(
            "INSERT INTO incidents(space,rule,target,opened,last_seen) VALUES(?,?,?,?,?)",
            (space, rule, target, now, now),
        ).lastrowid
    elif row:
        ident = row["id"]
        c.execute("UPDATE incidents SET closed=? WHERE id=?", (now, ident))
    else:
        return
    c.execute(
        "INSERT INTO delivery(incident,event,due) VALUES(?,?,?)",
        (ident, "触发" if bad else "恢复", now),
    )


def model_health():
    global _last_deepseek_probe
    if not uses_deepseek():
        try:
            with request.urlopen("http://llm:8080/health", timeout=3) as response:
                return "本地问答模型", "正常" if response.status == 200 else "未就绪"
        except OSError:
            return "本地问答模型", "未就绪"

    now = time.monotonic()
    last, status = _last_deepseek_probe
    if now - last >= 60:
        try:
            response = httpx.get(
                f"{DEEPSEEK_URL}/models", headers=deepseek_headers(), timeout=5
            )
            response.raise_for_status()
            status = (
                "正常"
                if any(
                    model["id"] == DEEPSEEK_MODEL for model in response.json()["data"]
                )
                else "模型不可用"
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            status = "接口检查失败"
        _last_deepseek_probe = (now, status)
    return "DeepSeek Flash", status


def observe():
    try:
        with request.urlopen(
            os.getenv("PROBE_URL", "http://api:8000/readyz"), timeout=3
        ) as resp:
            api_ok = resp.status == 200
    except OSError:
        api_ok = False
    with closing(connect()) as c, c:
        signal(c, "", "服务就绪检查失败", "FastAPI", not api_ok)
        if api_ok:
            try:
                req = request.Request(
                    "http://api:8000/internal/window",
                    headers={"X-Probe-Key": os.environ["PROBE_KEY"]},
                )
                with request.urlopen(req, timeout=3) as resp:
                    window = json.load(resp)
                enough = window["samples"] >= 10
                slow = window["p95_upper_s"] in (30, ">30")
                signal(
                    c,
                    "",
                    "API 五分钟错误率达到 5%",
                    "FastAPI",
                    enough and window["errors"] / max(window["samples"], 1) >= 0.05,
                )
                signal(c, "", "API 五分钟 P95 超过 15 秒", "FastAPI", enough and slow)
                signal(c, "", "API 指标采集失败", "FastAPI", False)
                c.execute(
                    "INSERT OR REPLACE INTO state VALUES('window',?)",
                    (json.dumps(window),),
                )
            except (OSError, ValueError, KeyError):
                signal(c, "", "API 指标采集失败", "FastAPI", True)
        components = {"API 就绪探测": "正常" if api_ok else "失败"}
        if os.getenv("QA_SR_READ"):
            from .analytics import sr

            try:
                with sr() as s, s.cursor() as cur:
                    cur.execute("SELECT 1")
                components["StarRocks"] = "正常"
            except Exception:
                components["StarRocks"] = "连接失败"
            signal(
                c,
                "",
                "分析引擎连接失败",
                "StarRocks",
                components["StarRocks"] != "正常",
            )
        model, status = model_health()
        components[model] = status
        for target in ("llama.cpp", DEEPSEEK_MODEL):
            signal(
                c,
                "",
                "问答模型未就绪",
                target,
                status != "正常"
                and target == (DEEPSEEK_MODEL if uses_deepseek() else "llama.cpp"),
            )
        free = shutil.disk_usage(STATE.parent)
        components["监控卷剩余"] = f"{free.free / free.total:.1%}"
        signal(
            c, "", "磁盘可用空间低于 10%", "监控数据卷", free.free / free.total < 0.10
        )
        try:
            with db() as pg:
                beat = pg.execute(
                    "SELECT now()-ts > interval '5 minutes' AS stale FROM heartbeat WHERE name='worker'"
                ).fetchone()
                cursor = c.execute("SELECT val FROM state WHERE key='job'").fetchone()
                rows = pg.execute(
                    """SELECT id,space,asset,status FROM jobs WHERE id>%s
                                  AND status IN ('ready','failed') ORDER BY id LIMIT 500""",
                    (cursor[0] if cursor else 0,),
                ).fetchall()
                for r in rows:
                    signal(
                        c,
                        r["space"],
                        "文档导入失败",
                        r["asset"],
                        r["status"] == "failed",
                    )
                    c.execute(
                        "INSERT OR REPLACE INTO state VALUES('job',?)", (r["id"],)
                    )
                components["导入进程"] = (
                    "心跳超时" if not beat or beat["stale"] else "正常"
                )
                components["PostgreSQL"] = "正常"
                signal(c, "", "导入进程心跳超时", "worker", not beat or beat["stale"])
            signal(c, "", "数据库连接失败", "PostgreSQL", False)
        except Exception as exc:
            components.update({"PostgreSQL": "连接失败", "导入进程": "心跳不可读取"})
            print(f"database probe: {type(exc).__name__}", flush=True)
            signal(c, "", "数据库连接失败", "PostgreSQL", True)
        c.executemany(
            "INSERT OR REPLACE INTO state VALUES(?,?)",
            [
                ("probe", int(time.time())),
                ("components", json.dumps(components)),
                ("channel", int(bool(os.getenv("FEISHU_WEBHOOK")))),
            ],
        )


def feishu_payload(text, secret, now):
    data = {"msg_type": "text", "content": {"text": text}}
    if secret:
        key = f"{now}\n{secret}".encode()
        data.update(
            timestamp=str(now),
            sign=base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode(),
        )
    return data


def deliver(url, secret, now):
    if not url:
        with closing(connect()) as c, c:
            c.execute(
                "UPDATE delivery SET error='station_only' WHERE sent IS NULL AND error IS NULL AND tries=0"
            )
        return
    with closing(connect()) as c, c:
        rows = c.execute(
            """SELECT d.*,i.rule FROM delivery d JOIN incidents i ON i.id=d.incident
            WHERE d.sent IS NULL AND COALESCE(d.error,'') != 'station_only' AND d.due<=? AND NOT EXISTS
            (SELECT 1 FROM delivery p WHERE p.incident=d.incident AND p.id<d.id AND p.sent IS NULL AND COALESCE(p.error,'') != 'station_only')
            ORDER BY d.id LIMIT 8""",
            (now,),
        ).fetchall()
        for r in rows:
            # No filenames, source text, account names or query bodies in external notifications.
            payload = feishu_payload(
                f"QueryAtlas {r['event']}｜{r['rule']}｜事件 #{r['incident']}",
                secret,
                now,
            )
            req = request.Request(
                url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            try:
                with request.urlopen(req, timeout=4) as res:
                    result = json.load(res)
                if result.get("code", result.get("StatusCode")) != 0:
                    raise ValueError("receiver rejected message")
            except (ValueError, OSError) as exc:
                delay = min(3600, 10 * 2 ** min(r["tries"], 9))
                c.execute(
                    "UPDATE delivery SET tries=tries+1,due=?,error=? WHERE id=?",
                    (now + delay, type(exc).__name__, r["id"]),
                )
            else:
                c.execute(
                    "UPDATE delivery SET sent=?,tries=tries+1,error=NULL WHERE id=?",
                    (now, r["id"]),
                )


def snapshot(space, global_view):
    with closing(connect()) as c:
        clauses = "i.space=? OR (i.space='' AND ?)"
        rows = c.execute(
            f"""SELECT i.*,(SELECT CASE WHEN d.sent IS NOT NULL THEN '已送达'
             WHEN d.error='station_only' THEN '站内' WHEN d.error IS NOT NULL THEN '等待重试' ELSE '待发送' END FROM delivery d
             WHERE d.incident=i.id ORDER BY d.id DESC LIMIT 1) AS delivery FROM incidents i
             WHERE {clauses} ORDER BY i.closed IS NULL DESC,i.id DESC LIMIT 100""",
            (space, global_view),
        )
        events = [dict(r) for r in rows]
        channel = c.execute("SELECT val FROM state WHERE key='channel'").fetchone()
        components = (
            c.execute("SELECT val FROM state WHERE key='components'").fetchone()
            if global_view
            else None
        )
        probe = c.execute("SELECT val FROM state WHERE key='probe'").fetchone()
        active = c.execute(
            f"SELECT count(*) FROM incidents i WHERE ({clauses}) AND i.closed IS NULL",
            (space, global_view),
        ).fetchone()[0]
        window = (
            c.execute("SELECT val FROM state WHERE key='window'").fetchone()
            if global_view
            else None
        )
    return {
        "channel": "飞书" if channel and channel[0] else "站内告警",
        "components": json.loads(components[0]) if components else {},
        "events": events,
        "active": active,
        "probe": probe[0] if probe else None,
        "http5": json.loads(window[0]) if window else None,
    }


def main():
    init_monitor()
    while True:
        observe()
        deliver(
            os.getenv("FEISHU_WEBHOOK"), os.getenv("FEISHU_SECRET"), int(time.time())
        )
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
