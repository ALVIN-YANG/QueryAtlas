"""Exercise workspace permissions and template state on a deployed test instance."""

import sys
import uuid

import httpx


def run(base_url, token):
    marker = uuid.uuid4().hex[:8]
    space = "模板验收-" + marker
    person = "模板验收成员-" + marker
    sql = (
        "SELECT o_orderpriority AS priority, COUNT(*) AS order_count FROM orders "
        "GROUP BY o_orderpriority ORDER BY o_orderpriority"
    )
    alias = "订单分组验收 " + marker
    data = {"id": "priority_counts", "dataset": "tpch", "aliases": [alias], "sql": sql}
    base = f"/api/spaces/{space}"
    templates = base + "/sql-templates"
    root_headers = {"Authorization": "Bearer " + token}

    with httpx.Client(base_url=base_url, timeout=30) as client:
        def call(method, path, body=None, *, headers=root_headers, code=200):
            r = client.request(method, path, json=body, headers=headers)
            assert r.status_code == code, (method, path, r.status_code, r.text[:350])
            return r.json()

        call("POST", "/api/admin", {"kind": "space", "name": space})
        new_token = call("POST", "/api/admin", {"kind": "account", "name": person})["token"]
        headers = {"Authorization": "Bearer " + new_token}

        def member(method, path, body=None, code=200):
            return call(method, path, body, headers=headers, code=code)

        def grant(role, asset=""):
            call("POST", "/api/admin", {
                "kind": "grant", "name": person, "space": space, "role": role, "asset": asset,
            })

        def ask(question):
            chat = member("POST", base + "/chats")["id"]
            return member("POST", base + f"/chats/{chat}/ask", {
                "q": question, "source": "auto", "request_id": str(uuid.uuid4()),
            })

        try:
            call("POST", base + "/benchmarks/tpch")
            member("GET", templates, code=403)
            grant("SQL 模板管理员")
            assert member("GET", templates)["datasets"] == []
            member("POST", templates, data, 403)
            for role in ("分析阅读者", "分析明细查看者"):
                grant(role, "@data/tpch")
            member("GET", "/api/spaces/demo/sql-templates", code=403)
            member("POST", "/api/spaces/demo/sql-templates/preview", {
                "dataset": "tpch", "sql": sql,
            }, 403)

            call("POST", "/api/admin/limits/" + space + "/tpch", {
                "person": person, "table": "orders", "fields": ["o_orderpriority"],
                "filter_col": "o_orderpriority", "filter_value": "1-URGENT",
            })
            result = member("POST", templates + "/preview", {"dataset": "tpch", "sql": sql})
            assert len(result["rows"]) == 1 and result["rows"][0][0] == "1-URGENT"
            item = member("POST", templates, data)
            assert not item["enabled"]
            member("POST", templates, {**data, "id": "duplicate"}, 409)
            template = templates + "/" + item["id"]
            assert "result" not in ask(alias)
            item = member("POST", template + "/status", {"revision": item["revision"], "enabled": True})
            answer = ask(alias)
            assert answer["diagnostics"]["sql_source"]["kind"] == "template"
            assert answer["result"]["rows"] == result["rows"]

            edit = {"dataset": "tpch", "aliases": [alias + " 更新"], "sql": sql}
            member("PUT", template, {**edit, "revision": item["revision"] - 1}, 409)
            item = member("PUT", template, {**edit, "revision": item["revision"]})
            assert "result" not in ask(alias)
            assert ask(edit["aliases"][0])["result"]["rows"] == result["rows"]
            member("POST", template + "/status", {"revision": item["revision"], "enabled": False})
            assert "result" not in ask(edit["aliases"][0])
            print("Verified cross-space denial, data scope, duplicate and stale guards, enable/edit/disable.")
        finally:
            call("POST", "/api/admin", {"kind": "disable", "name": person})


if __name__ == "__main__":
    run("http://127.0.0.1:8000", sys.stdin.readline().strip())
