"""Run the pinned upstream statements on the loaded SF1 data, including multipart DS cases."""

import json, time, re
from load import BASE, SSB, ROOT, fetch, SIZES


def queries(suite):
    if suite == "tpch":
        for i in range(1, 23):
            yield f"Q{i}", (ROOT / f"upstream/sql/tpch/query/tpch/q{i}.sql").read_text()
    elif suite == "tpcds":
        text = (
            fetch(BASE + "docs/en/benchmarking/tpc_ds_99_sql.md")
            .split("```sql", 1)[-1]
            .split("```")[0]
        )
        parts = re.split(r"(?im)^--\s*query\s+(\d+)[^\n]*\n", text)
        for k, v in zip(parts[1::2], parts[2::2]):
            # The official 2.10.0 kit gives the review-date surrogate its _sk suffix.
            if k == "30":
                v = re.sub(
                    r"\bc_last_review_date\b", "c_last_review_date_sk", v, flags=re.I
                )
            yield "Q" + k, v
    else:
        for group, total in enumerate((3, 3, 4, 3), 1):
            for n in range(1, total + 1):
                label = f"Q{group}.{n}"
                raw = fetch(SSB + label + ".sql")
                yield (
                    label,
                    re.sub(
                        r"\bfrom\s+lineorder\b.*?\bwhere\b",
                        "from lineorder_flat where",
                        raw,
                        flags=re.I | re.S,
                    ),
                )


def run():
    import pymysql, subprocess

    host = subprocess.check_output(
        [
            "sudo",
            "docker",
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            "queryatlas-starrocks-1",
        ],
        text=True,
    ).strip()
    results = {}
    failures = []
    for suite in SIZES:
        db = "qa_" + suite + "_sf1"
        conn = pymysql.connect(
            host=host, port=9030, user="root", database=db, read_timeout=120
        )
        entries = list(queries(suite))
        assert len(entries) == {"tpch": 22, "ssb": 13, "tpcds": 99}[suite], (
            suite,
            len(entries),
        )
        with conn.cursor() as cur:
            cur.execute("SET query_timeout=90")
            cur.execute("SET pipeline_dop=3")
            for name, stmt in entries:
                clock = []
                rows = []
                try:
                    for repeat in range(3):
                        start = time.perf_counter()
                        rows = []
                        for part in stmt.split(";"):
                            if re.search(r"\b(select|with)\b", part, re.I):
                                cur.execute(part)
                                rows.append(len(cur.fetchall()))
                        if not rows:
                            raise ValueError("empty query")
                        clock.append(round(1000 * (time.perf_counter() - start), 2))
                    result = {"rows": rows, "ms": clock}
                except Exception as e:
                    failures.append(suite + name)
                    result = {"error": str(e)[:450]}
                results.setdefault(suite, {})[name] = result
                print(suite, name, result, flush=True)
                (ROOT / "timings.json").write_text(json.dumps(results, indent=2))
        conn.close()
    if failures:
        raise SystemExit(str(failures))


if __name__ == "__main__":
    run()
