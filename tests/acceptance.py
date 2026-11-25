"""Live acceptance for a disposable test space. Also load the sample files into demo on request."""

import argparse
import io
import sys
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from PIL import Image

from make_samples import create


def until(fn, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(1)
    raise AssertionError("operation did not reach expected state")


def check(base, key, demo):
    client = httpx.Client(
        base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=20
    )

    def change(kind, name, **kw):
        r = client.post("/api/admin", json=dict(kind=kind, name=name, **kw))
        r.raise_for_status()
        return r.json()

    suffix = uuid.uuid4().hex[:8]
    space = "demo" if demo else "验收-" + suffix
    if not demo:
        change("space", space)
    prefix = "/api/spaces/" + space

    def upload(name, content):
        response = client.post(prefix + "/imports", files={"file": (name, content)})
        response.raise_for_status()
        return response.json()["job"]

    def job(i):
        rows = client.get(prefix + "/jobs").json()
        return next((r for r in rows if r["id"] == i and r["status"] != "queued"), None)

    def search(who, q):
        return who.post(prefix + "/search", json={"q": q, "limit": 20})

    def event(name):
        return next(
            (
                r
                for r in client.get(prefix + "/monitor").json()["events"]
                if r["target"] == name
            ),
            None,
        )

    with TemporaryDirectory() as td:
        folder = Path(td)
        create(folder)
        pending = {
            p.name: upload(p.name, p.read_bytes()) for p in sorted(folder.iterdir())
        }
        for name, i in pending.items():
            r = until(lambda: job(i))
            print(f"{name}  #{i}  {r['status']}  {r['note']}", flush=True)
            assert r["status"] == "ready", r["note"]
        probes = [
            ("归档手册.pdf", "Manual reference QA-739", "第 1 页", "QA-739", "text"),
            ("归档手册.pdf", "归档期限为九十天", "第 2 页", "9206", "ocr"),
            ("复核单.png", "异常订单由谁复核", "原图", "4827", "ocr"),
            ("复核单照片.jpg", "异常订单由谁复核", "原图", "4827", "ocr"),
            (
                "指标口径.docx",
                "两条延期明细的订单计算几次",
                "第 2 段",
                "只计一次",
                "text",
            ),
            ("版本说明.html", "核对发布", "发布说明", "7102", "text"),
            ("字段字典.csv", "订单键", "第 2 行", "5120", "text"),
            ("审核清单.xlsx", "供应分析复核编号", "审核表", "6552", "text"),
        ]
        for name, q, anchor, expected, method in probes:
            res = search(client, q)
            res.raise_for_status()
            ranked = res.json()["hits"]
            hit = next(
                (
                    (i + 1, h)
                    for i, h in enumerate(ranked)
                    if h["asset"] == name
                    and anchor in h["anchor"]
                    and expected in h["body"].replace(" ", "")
                    and h["method"] == method
                ),
                None,
            )
            assert hit, (name, [(h["asset"], h["anchor"], h["body"]) for h in ranked])
            print(f"{name}  {hit[1]['anchor']}  rank={hit[0]}  {method}", flush=True)
        if demo:
            return
        doc = "指标口径.docx"
        name = "测试工具-" + suffix
        token = change("account", name)["token"]
        limited = httpx.Client(
            base_url=base, headers={"Authorization": f"Bearer {token}"}
        )
        try:
            assert search(limited, "延期").status_code == 403
            change("grant", name, space=space, role="阅读者", asset=doc)
            hits = search(limited, "延期").json()["hits"]
            assert hits and {r["asset"] for r in hits} == {doc}
            assert limited.get("/api/spaces/demo/assets").json()["assets"] == []
            assert (
                limited.post("/api/spaces/demo/search", json={"q": "延期"}).status_code
                == 403
            )
            assert limited.get(prefix + "/monitor").status_code == 403
            endpoint = prefix + "/source/" + str(hits[0]["version"])
            assert limited.get(endpoint).status_code == 404
            change("grant", name, space=space, role="原件查看者", asset=doc)
            assert limited.get(endpoint).content == (folder / doc).read_bytes()
            change("revoke", name, space=space, role="原件查看者", asset=doc)
            assert limited.get(endpoint).status_code == 404
            change("revoke", name, space=space, role="阅读者", asset=doc)
            assert search(limited, "延期").status_code == 403
            print("权限：跨空间、文件范围、原件独立授权及撤权通过", flush=True)
        finally:
            change("disable", name)
        assert limited.get("/api/me").status_code == 401
        pic = "复核单.png"
        published = next(
            x["version"]
            for x in client.get(prefix + "/assets").json()["assets"]
            if x["asset"] == pic
        )
        blank = io.BytesIO()
        Image.new("RGB", (300, 200), "white").save(blank, "PNG")
        failed = upload(pic, blank.getvalue())
        assert until(lambda: job(failed))["status"] == "failed"
        assert (
            client.get(prefix + f"/source/{published}").content
            == (folder / pic).read_bytes()
        )
        ev = until(lambda: event(pic))
        assert not ev["closed"]
        assert client.post(prefix + f"/alerts/{ev['id']}/ack").status_code == 200
        assert event(pic)["ack_by"] == "admin"
        retry = upload(pic, (folder / pic).read_bytes())
        assert until(lambda: job(retry))["status"] == "ready"
        assert until(lambda: event(pic)["closed"])
        assert (
            next(
                x["version"]
                for x in client.get(prefix + "/assets").json()["assets"]
                if x["asset"] == pic
            )
            == published
        )
        print(
            f"监控：空白 OCR → 失败 #{failed} → 告警 #{ev['id']} → 认领 → 恢复；原件和版本不变",
            flush=True,
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true")
    p.add_argument("--url", default="http://api:8000")
    args = p.parse_args()
    check(args.url, sys.stdin.readline().strip(), args.demo)
