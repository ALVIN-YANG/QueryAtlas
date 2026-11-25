"""Independently reconcile the published TPC-H orders and lineitems with MySQL."""

import argparse
import json
from contextlib import closing
from pathlib import Path

from deploy.bench.verify_tpch import (
    ANSWER, ANSWER_SHA, COUNTS, REVISION, STATEMENT, STATEMENT_SHA, pinned_text, rows,
)
from queryatlas.analytics import ident
from queryatlas.db import db
from queryatlas.mysql_source import mysql
from queryatlas.mysql_sync import warehouse_connection
from deploy.etl.tpch_source import SPEC

TOTALS = {
    "orders": "SELECT COUNT(*), SUM(o_totalprice) FROM orders",
    "lineitem": "SELECT COUNT(*), SUM(l_extendedprice), SUM(l_quantity) FROM lineitem",
}


def comparison(cur, sql):
    totals = {}
    for name, stmt in TOTALS.items():
        cur.execute(stmt)
        count, *values = cur.fetchone()
        totals[name] = {"rows": int(count), "sums": [format(v, ".2f") for v in values]}
    cur.execute(sql)
    return {"totals": totals, "q4": [[priority.strip(), int(count)] for priority, count in cur]}


def verify(upstream, full):
    # MySQL requires a space after the line-comment marker. The pinned header is --Q4.
    sql = pinned_text(upstream, STATEMENT, STATEMENT_SHA).split("\n", 1)[1]
    expected = rows(pinned_text(upstream, ANSWER, ANSWER_SHA).split("\n", 1)[1], "|")
    with db() as c:
        published = c.execute(
            """SELECT d.physical,d.batch,m.watermark FROM datasets d
               JOIN mysql_sync_checkpoints m USING (space,name,physical)
               WHERE (space,name)=(%s,%s)""",
            (SPEC.space, SPEC.dataset),
        ).fetchone()
    with closing(mysql()) as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        cur.execute("SELECT COALESCE(MAX(batch_id),0) FROM qa_etl_changes")
        head = int(cur.fetchone()[0])
        if head != published["watermark"]:
            raise ValueError("source advanced after last publish")
        source = comparison(cur, sql)
    with closing(warehouse_connection()) as conn, conn.cursor() as cur:
        cur.execute(f"USE {ident(published['physical'])}")
        target = comparison(cur, sql)
        cur.execute("SELECT CURRENT_VERSION()")
        (version,) = cur.fetchone()
    actual_counts = {k: v["rows"] for k, v in target["totals"].items()}
    passed = source == target and (
        not full or (
            target["q4"] == expected
            and actual_counts == {k: COUNTS[k] for k in SPEC.tables}
        )
    )
    return {
        "benchmarktool_commit": REVISION, "starrocks_version": version,
        "batch": published["batch"], "watermark": head,
        "source": source, "target": target, "official_baseline_checked": full, "passed": passed,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream", type=Path)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    result = verify(args.upstream, args.full)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
