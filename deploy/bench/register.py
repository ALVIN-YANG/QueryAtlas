"""Run inside the api container, after provisioning read and import database roles."""

from queryatlas.db import db
from queryatlas.analytics import sr
from psycopg.types.json import Jsonb

for name in ("tpch", "ssb", "tpcds"):
    physical = f"qa_{name}_sf1"
    with sr(physical) as s, s.cursor() as cur:
        cur.execute("SHOW FULL TABLES")
        tables = {}
        for t, _ in cur.fetchall():
            cur.execute(f"DESCRIBE `{t}`")
            tables[t] = [x[0] for x in cur.fetchall()]
    with db() as c:
        c.execute(
            """INSERT INTO datasets VALUES('demo',%s,%s,%s,%s) ON CONFLICT(space,name) DO UPDATE SET tables=excluded.tables,physical=excluded.physical""",
            (
                name,
                physical,
                {
                    "tpch": "TPC-H · 订单与供应链",
                    "ssb": "SSB · 星型销售分析",
                    "tpcds": "TPC-DS · 零售决策支持",
                }[name],
                Jsonb(tables),
            ),
        )
    print(name, len(tables))

import json
from pathlib import Path

pack = Path("/tmp/queryatlas-packs.json")
if pack.exists():
    with db() as c:
        for name, rec in json.loads(pack.read_text()).items():
            c.execute(
                """INSERT INTO benchmark_packs VALUES(%s,%s,%s,%s)
                      ON CONFLICT(name) DO UPDATE SET counts=excluded.counts,queries=excluded.queries,timings=excluded.timings""",
                (
                    name,
                    Jsonb(rec["counts"]),
                    Jsonb(rec["queries"]),
                    Jsonb(rec["timings"]),
                ),
            )
