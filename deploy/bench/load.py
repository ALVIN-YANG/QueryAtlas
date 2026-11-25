"""Prepare three private SF1 databases from pinned upstream generators. Run on the test host."""

import csv
import hashlib
import json
import re
import runpy
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path.home() / "queryatlas-bench"
REF = "b4b4c8941f13dccfc1245de9131d40c697e409f7"
BASE = f"https://raw.githubusercontent.com/StarRocks/starrocks/{REF}/"
SSB = BASE + "fe/fe-core/src/test/resources/sql/ssb/"
CONTAINER = "queryatlas-starrocks-1"
CACHE = ROOT / "source"
CACHE.mkdir(exist_ok=True)
REPORT = ROOT / "loaded.json"
SIZES = {"tpch": 8, "ssb": 5, "tpcds": 24}


def fetch(url):
    p = CACHE / url.split("/")[-1]
    if not p.exists():
        with urlopen(url, timeout=60) as f:
            p.write_bytes(f.read())
    return p.read_text()


def sql(query, database=None):
    cmd = [
        "sudo",
        "docker",
        "exec",
        "-i",
        CONTAINER,
        "mysql",
        "--protocol=tcp",
        "-h127.0.0.1",
        "-P9030",
        "-uroot",
        "-NB",
    ]
    if database:
        cmd.append(database)
    v = subprocess.run(cmd, input=query, text=True, capture_output=True)
    if v.returncode:
        raise ValueError(v.stderr[:800])
    return v.stdout.strip()


def submit(db, table, file, columns, rows):
    actual = int(sql(f"select count(*) from `{table}`", db))
    if actual == rows and rows > 0:
        return
    if actual:
        raise ValueError(f"Unexpected nonempty candidate {db}.{table}: {actual}")
    h = hashlib.sha256()
    with file.open("rb") as f:
        while chunk := f.read(2**20):
            h.update(chunk)
    with file.open("rb") as f:
        args = [
            "sudo",
            "docker",
            "exec",
            "-i",
            CONTAINER,
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "-u",
            "root:",
            "-H",
            "Expect: 100-continue",
            "-H",
            "strict_mode: true",
            "-H",
            "max_filter_ratio: 0",
            "-H",
            "column_separator: |",
            "-H",
            "timeout: 1200",
            "-H",
            f"label: qa_{db}_{table}_{h.hexdigest()[:16]}",
        ]
        if columns:
            args += ["-H", "columns: " + ",".join(columns)]
        args += [
            "--upload-file",
            "-",
            f"http://127.0.0.1:8040/api/{db}/{table}/_stream_load",
        ]
        res = json.loads(subprocess.check_output(args, stdin=f))
    if res["Status"] not in ("Success", "Publish Timeout", "Label Already Exists"):
        raise ValueError(f"{table}: {res}")
    if int(sql(f"select count(*) from `{table}`", db)) != rows:
        raise ValueError(f"count mismatch: {table}")


def normalize(source, suite, table):
    out = ROOT / f"{suite}-{table}.pipe"
    n = 0
    with source.open() as f, out.open("w") as w:
        if suite == "ssb":
            for values in csv.reader(f):
                if values[-1] == "":
                    values.pop()
                if table == "lineorder":
                    for i in [5, 15]:
                        values[i] = values[i].replace("-", "")
                w.write("|".join(values) + "\n")
                n += 1
        else:
            for line in f:
                values = line.rstrip("\r\n").split("|")
                if suite == "tpcds":
                    if values[-1] == "":
                        values.pop()
                    values = [v if v else r"\N" for v in values]
                w.write("|".join(values) + "\n")
                n += 1
    return out, n


def statements(suite):
    upstream = ROOT / "upstream"
    if suite == "tpch":
        raw = (upstream / "sql/tpch/ddl_100/tpch_create.sql").read_text()
        cols = runpy.run_path(str(upstream / "src/config/columns_config.py"))[
            "tpch_columns"
        ]
        for m in re.finditer(r"CREATE TABLE\s+`?(\w+)`?\s*\(.*?;", raw, re.I | re.S):
            q = re.sub(r'"replication_num"\s*=\s*"\d+"', '"replication_num"="1"', m[0])
            q = re.sub(r" BUCKETS (?:96|24|12)", " BUCKETS 4", q)
            t = m[1]
            yield t, q, ROOT / f"sf1/{t}.tbl", cols.get(t)
    if suite == "ssb":
        for t in ("customer", "dates", "part", "supplier", "lineorder"):
            raw = fetch(SSB + t + ".sql")
            body = raw[: raw.upper().index(") ENGINE")] + ")"
            first = re.search(r"\(\s*`(\w+)`", body)[1]
            ddl = (
                body
                + f' duplicate key(`{first}`) distributed by hash(`{first}`) buckets 4 properties("replication_num"="1")'
            )
            yield t, ddl, ROOT / f"ssb-clean/{'date' if t == 'dates' else t}.tbl", None
    if suite == "tpcds":
        raw = (ROOT / "dsgen/tools/tpcds.sql").read_text()
        for m in re.finditer(r"create table (\w+)\s*\((.*?)\);", raw, re.I | re.S):
            t = m[1]
            if t == "dbgen_version":
                continue
            fields = []
            for name, typ in re.findall(
                r"^\s*(\w+)\s+(integer|date|(?:var)?char\s*\(\d+\)|decimal\s*\(\d+\s*,\s*\d+\))",
                m[2],
                re.I | re.M,
            ):
                fields.append(f"`{name}` {typ} NULL")
            first = fields[0].split()[0]
            yield (
                t,
                f'create table `{t}` ({",".join(fields)}) duplicate key({first}) distributed by hash({first}) buckets 4 properties("replication_num"="1")',
                ROOT / f"ds1/{t}.dat",
                None,
            )


def prepare(suite):
    db = f"qa_{suite}_sf1"
    sql("create database if not exists " + db)
    entries = list(statements(suite))
    assert len(entries) == SIZES[suite]
    counts = {}
    for t, q, p, columns in entries:
        sql(
            re.sub(
                r"(?i)create table (?!if not exists)", "create table if not exists ", q
            ),
            db,
        )
        f, n = normalize(p, suite, t)
        submit(db, t, f, columns, n)
        counts[t] = n
        print(db, t, n, flush=True)
    if suite == "tpch":
        raw = (ROOT / "upstream/sql/tpch/ddl_100/tpch_create.sql").read_text()
        sql(
            "create view if not exists revenue0 "
            + raw.split("create view revenue0 ")[1],
            db,
        )
    if suite == "ssb":
        joins = " from lineorder join dates on lo_orderdate=d_datekey join customer on lo_custkey=c_custkey join supplier on lo_suppkey=s_suppkey join part on lo_partkey=p_partkey"
        sql(
            'create table if not exists lineorder_flat distributed by hash(lo_orderkey) buckets 4 properties("replication_num"="1") as select lineorder.*,dates.*,customer.*,supplier.*,part.*'
            + joins,
            db,
        )
        counts["lineorder_flat"] = int(sql("select count(*) from lineorder_flat", db))
        assert counts["lineorder_flat"] == counts["lineorder"]
    return counts


if __name__ == "__main__":
    for suite in sys.argv[1:] or SIZES:
        counts = prepare(suite)
        report = json.loads(REPORT.read_text()) if REPORT.exists() else {}
        report[suite] = counts
        REPORT.write_text(json.dumps(report, indent=2))
