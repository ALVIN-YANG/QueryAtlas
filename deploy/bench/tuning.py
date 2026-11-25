"""Run paired StarRocks SF1 tuning cases on the benchmark host."""

import argparse
import json
import math
import re
import statistics
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pymysql


SOURCE = "qa_tpch_sf1"
LINEITEM_COLUMNS = (
    "l_shipdate,l_orderkey,l_linenumber,l_quantity,l_extendedprice,l_discount"
)
TABLE_COLUMNS = """
    l_shipdate DATE NOT NULL, l_orderkey INT NOT NULL, l_linenumber INT NOT NULL,
    l_quantity DECIMAL(15,2) NOT NULL, l_extendedprice DECIMAL(15,2) NOT NULL,
    l_discount DECIMAL(15,2) NOT NULL
"""
MONTHLY = """SELECT DATE_TRUNC('month', l_shipdate) AS ship_month,
    SUM(l_extendedprice * l_discount) AS discount_amount, COUNT(*) AS line_count
    FROM lineitem_base GROUP BY DATE_TRUNC('month', l_shipdate)"""
IN_QUARTER = "o_orderdate >= '1993-07-01' AND o_orderdate < '1993-10-01'"
JOIN_BASE = f"""SELECT o_orderpriority, COUNT(*) AS total, COUNT(x.k) AS delayed
FROM {SOURCE}.orders o LEFT JOIN (
    SELECT DISTINCT l_orderkey AS k FROM {SOURCE}.lineitem
    WHERE l_receiptdate > l_commitdate
) x ON x.k=o.o_orderkey
WHERE {IN_QUARTER}
GROUP BY o_orderpriority ORDER BY o_orderpriority"""
JOIN_REWRITE = f"""WITH quarter_orders AS (
    SELECT o_orderkey, o_orderpriority FROM {SOURCE}.orders WHERE {IN_QUARTER}
), totals AS (
    SELECT o_orderpriority, COUNT(*) AS total FROM quarter_orders
    GROUP BY o_orderpriority
), late AS (
    SELECT o_orderpriority, COUNT(*) AS delayed FROM quarter_orders q
    WHERE EXISTS (
        SELECT 1 FROM {SOURCE}.lineitem l WHERE l.l_orderkey=q.o_orderkey
          AND l.l_receiptdate > l.l_commitdate
    )
    GROUP BY o_orderpriority
)
SELECT t.o_orderpriority, t.total, COALESCE(l.delayed, 0) AS delayed
FROM totals t LEFT JOIN late l ON t.o_orderpriority=l.o_orderpriority
ORDER BY t.o_orderpriority"""


def q6(table):
    return f"""SELECT SUM(l_extendedprice * l_discount) AS discount_amount FROM {table}
WHERE l_shipdate >= '1994-01-01' AND l_shipdate < '1995-01-01'
  AND l_discount BETWEEN 0.05 AND 0.07 AND l_quantity < 24"""


CASES = {
    "partition": ((q6("lineitem_base"), False), (q6("lineitem_year"), False)),
    "materialized_view": (
        (MONTHLY + " ORDER BY ship_month", False),
        (MONTHLY + " ORDER BY ship_month", True),
    ),
    "join": ((JOIN_BASE, False), (JOIN_REWRITE, False)),
}


@contextmanager
def session(host, database=None, rewrite=False, profiling=False):
    with pymysql.connect(
        host=host, port=9030, user="root", database=database, autocommit=True,
        connect_timeout=5, read_timeout=180, charset="utf8mb4",
    ) as conn, conn.cursor() as cur:
        cur.execute("SET query_timeout=120")
        cur.execute("SET pipeline_dop=3")
        cur.execute("SET enable_query_cache=false")
        cur.execute("SET enable_materialized_view_rewrite=%s", (rewrite,))
        cur.execute("SET enable_profile=%s", (profiling,))
        yield cur


def setup(host, database, output):
    partitions = ", ".join(
        f"PARTITION p{year} VALUES LESS THAN ('{year + 1}-01-01')"
        for year in range(1992, 1999)
    )
    statements = [f"CREATE DATABASE {database}"]
    for name, partition in [
        ("lineitem_base", ""),
        ("lineitem_year", f"PARTITION BY RANGE(l_shipdate) ({partitions})"),
    ]:
        statements += [
            f"""CREATE TABLE {database}.{name} ({TABLE_COLUMNS})
            DUPLICATE KEY(l_shipdate,l_orderkey) {partition}
            DISTRIBUTED BY HASH(l_orderkey) BUCKETS 4
            PROPERTIES("replication_num"="1", "compression"="LZ4")""",
            f"INSERT INTO {database}.{name} SELECT {LINEITEM_COLUMNS} FROM {SOURCE}.lineitem",
            f"ANALYZE TABLE {database}.{name} WITH SYNC MODE",
        ]
    statements += [
        f"""CREATE MATERIALIZED VIEW {database}.mv_monthly_discount
          DISTRIBUTED BY HASH(ship_month) BUCKETS 4
          REFRESH DEFERRED MANUAL
          PROPERTIES("replication_num"="1")
          AS {MONTHLY.replace('FROM lineitem_base', f'FROM {database}.lineitem_base')}""",
        f"REFRESH MATERIALIZED VIEW {database}.mv_monthly_discount WITH SYNC MODE",
    ]
    (output / "setup.sql").write_text(";\n\n".join(statements) + ";\n")
    info = {}
    with session(host) as cur:
        for stmt in statements:
            start = time.perf_counter()
            cur.execute(stmt)
            info[stmt.splitlines()[0]] = round(time.perf_counter() - start, 3)
            print(stmt.splitlines()[0], info[stmt.splitlines()[0]], "s", flush=True)
        totals = []
        for table in (f"{SOURCE}.lineitem", f"{database}.lineitem_base", f"{database}.lineitem_year"):
            cur.execute(f"SELECT COUNT(*), SUM(l_orderkey), SUM(l_extendedprice) FROM {table}")
            totals.append(cur.fetchone())
        if len(set(totals)) != 1 or totals[0][0] != 6001215:
            raise RuntimeError(f"Lineitem snapshot verification failed: {totals}")
        info["verified_source_and_copy_totals"] = totals[0]
        cur.execute("SELECT CURRENT_VERSION()")
        info["starrocks_version"] = cur.fetchone()[0]
        cur.execute(f"SHOW MATERIALIZED VIEWS FROM {database}")
        info["materialized_views"] = [
            dict(zip((field[0] for field in cur.description), row)) for row in cur.fetchall()
        ]
    return info


def query(cur, statement):
    start = time.perf_counter()
    cur.execute(statement)
    rows = cur.fetchall()
    ms = (time.perf_counter() - start) * 1000
    cur.execute("SELECT LAST_QUERY_ID()")
    return rows, {"ms": round(ms, 3), "query_id": cur.fetchone()[0]}


def measure(host, database, output, name, variants, repeats):
    directory = output / name
    directory.mkdir()
    samples = {"before": [], "after": []}
    labels = ("before", "after")
    reference = None
    with session(host, database, variants[0][1]) as before, session(
        host, database, variants[1][1]
    ) as after:
        cursors = {"before": before, "after": after}
        statements = dict(zip(labels, (v[0] for v in variants)))
        for label, (_, rewrite) in zip(labels, variants):
            statement = statements[label]
            (directory / f"{label}.sql").write_text(
                f"USE {database};\nSET enable_materialized_view_rewrite={str(rewrite).lower()};\n"
                f"{statement};\n"
            )
            cursors[label].execute("EXPLAIN " + statement)
            plan = "\n".join(row[0] for row in cursors[label].fetchall())
            (directory / f"{label}.plan.txt").write_text(plan + "\n")
        # Alternate which side runs first. Warm-ups use the same checks as measured runs.
        for pair in range(repeats + 3):
            for label in labels if pair % 2 == 0 else labels[::-1]:
                rows, sample = query(cursors[label], statements[label])
                if reference is None:
                    reference = rows
                if rows != reference:
                    raise RuntimeError(f"{name} {label} returned different results")
                if pair >= 3:
                    samples[label].append(sample)

    profiles = {}
    for label, (statement, rewrite) in zip(labels, variants):
        with session(host, database, rewrite, profiling=True) as cur:
            rows, sample = query(cur, statement)
            if rows != reference:
                raise RuntimeError(f"{name} {label} profiled result differed")
            profile = None
            for _ in range(10):
                cur.execute("SELECT GET_QUERY_PROFILE(%s)", (sample["query_id"],))
                profile = cur.fetchone()[0]
                if profile and "QueryId:" in profile:
                    break
                time.sleep(0.2)
            if not profile or sample["query_id"] not in profile:
                raise RuntimeError(f"Query Profile unavailable for {sample['query_id']}")
            (directory / f"{label}.profile.txt").write_text(profile)
            profiles[label] = sample["query_id"]

    stats = {}
    for label, runs in samples.items():
        durations = sorted(run["ms"] for run in runs)
        stats[label] = {
            "p50_ms": round(statistics.median(durations), 3),
            "p95_ms": durations[math.ceil(len(durations) * 0.95) - 1],
            "profile_query_id": profiles[label],
            "runs": runs,
        }
    result = {"result_rows": reference, "matched": True, **stats}
    (directory / "measurements.json").write_text(
        json.dumps(result, default=str, ensure_ascii=False, indent=2) + "\n"
    )
    print(name, "equal", len(reference), "rows", "p95",
          stats["before"]["p95_ms"], "->", stats["after"]["p95_ms"], "ms", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--repeats", type=int, default=20)
    args = parser.parse_args()
    if args.repeats < 20:
        parser.error("--repeats must be at least 20")
    args.output.mkdir(parents=True, exist_ok=False)
    database = "qa_tuning_" + uuid.uuid4().hex[:8]
    host = subprocess.check_output([
        "sudo", "docker", "inspect", "-f",
        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", "queryatlas-starrocks-1",
    ], text=True).strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host):
        raise RuntimeError("StarRocks container address is unavailable")
    report = {"database": database, "repeats_each": args.repeats, "cases": {}}
    report["setup"] = setup(host, database, args.output)
    summary = args.output / "summary.json"
    for name, variants in CASES.items():
        report["cases"][name] = measure(host, database, args.output, name, variants, args.repeats)
        summary.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2) + "\n")


if __name__ == "__main__":
    main()
