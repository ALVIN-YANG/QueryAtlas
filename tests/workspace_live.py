"""Exercise the deployed chat, publication and permission boundaries in an isolated space."""

import sys, uuid
import httpx


def run(url, token):
    c = httpx.Client(
        base_url=url, headers={"Authorization": "Bearer " + token}, timeout=90
    )

    def call(method, path, **kwargs):
        r = c.request(method, path, **kwargs)
        if r.is_error:
            raise RuntimeError(f"{path}: {r.status_code} {r.text[:280]}")
        return r.json()

    def post(path, data):
        return call("POST", path, json=data)

    def chat(space, q, source, id=None):
        path = f"/api/spaces/{space}/chats"
        if not id:
            id = post(path, {})["id"]
        a = post(
            path + f"/{id}/ask", dict(q=q, source=source, request_id=str(uuid.uuid4()))
        )
        return a, id

    q = "1993 年第三季度按优先级统计延期订单占比"
    a, thread = chat("demo", q, "tpch")
    assert len(a["result"]["rows"]) == 5 and a["refs"][0]["sql"]
    print("TPC-H", a["result"]["rows"], flush=True)
    a, _ = chat("demo", "同一张订单有两条延期明细算几次？", "docs", thread)
    print("document", a["text"], len(a["refs"]), flush=True)
    assert a["refs"] and any("只计一次" in x.get("body", "") for x in a["refs"])
    for name, table, count in [
        ("ssb", "lineorder_flat", 6001171),
        ("tpcds", "store_sales", 2880404),
    ]:
        a, _ = chat("demo", f"{table} 有多少行", name)
        assert a["result"]["rows"] == [[count]]
        print(name, count, flush=True)
    marker = uuid.uuid4().hex[:8]
    space = "工作台验收-" + marker
    person = "验收成员-" + marker
    post("/api/admin", dict(kind="space", name=space))
    key = post("/api/admin", dict(kind="account", name=person))["token"]
    base = f"/api/spaces/{space}"
    try:
        r = call(
            "POST",
            base + "/candidates",
            data={"name": "sales"},
            files={
                "file": (
                    "sales.csv",
                    "region,amount\nEast,10\nWest,20\nEast,40\n".encode(),
                )
            },
        )
        id = r["id"]
        post(
            base + f"/candidates/{id}/validate",
            {
                "columns": [
                    {"name": "region", "type": "VARCHAR"},
                    {"name": "amount", "type": "BIGINT"},
                ]
            },
        )
        post(base + f"/candidates/{id}/publish", {})
        post(
            "/api/admin",
            dict(
                kind="grant",
                name=person,
                space=space,
                role="分析阅读者",
                asset="@data/sales",
            ),
        )
        post(
            "/api/admin/limits/" + space + "/sales",
            dict(
                person=person,
                table="sales",
                fields=["region"],
                filter_col="region",
                filter_value="East",
            ),
        )
        c.headers["Authorization"] = "Bearer " + key
        info = call("GET", base + "/datasets/sales")
        assert info["tables"] == {"sales": ["region"]} and not info["sql"]
        a, t = chat(space, "sales 有多少行", "sales")
        assert a["result"]["rows"] == [[2]] and "sql" not in a["refs"][0]
        c.headers["Authorization"] = "Bearer " + token
        post(
            "/api/admin",
            dict(
                kind="revoke",
                name=person,
                space=space,
                role="分析阅读者",
                asset="@data/sales",
            ),
        )
        post("/api/admin", dict(kind="grant", name=person, space=space, role="阅读者"))
        c.headers["Authorization"] = "Bearer " + key
        a = call("GET", base + f"/chats/{t}")
        assert not a[0]["refs"] and "result" not in a[0]
        print(
            "CSV validated/published; projected/filtered member count=2; history revoked",
            flush=True,
        )
    finally:
        c.headers["Authorization"] = "Bearer " + token
        post("/api/admin", dict(kind="disable", name=person))


if __name__ == "__main__":
    run("http://127.0.0.1:8000", sys.stdin.readline().strip())
