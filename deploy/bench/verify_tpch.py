"""Validate TPC-H SF1 with pinned upstream SQL and TPC qualification answers."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from functools import partial
from pathlib import Path


REVISION = "0f9ce82ea978318e2830767ad27028659c7434fa"
STATEMENT = "sql/tpch/query/tpch/q4.sql"
ANSWER = "thirdparty/tpch-dbgen/answers/q4.out"
STATEMENT_SHA = "a808089546277d6dc24400e475950860d14ffa6dedad422224181e303a6ef714"
ANSWER_SHA = "9a6c7dbfde7735463a1754c74494512dbd82040b40c65c5e08cb72731fac5de7"
QUALIFICATION_SQL_SHA = "e92656545e7deee877826103170ae9d5259d67937831ba2562cd9976d2f738f0"
QUALIFICATION_ANSWERS_SHA = "718dec38e919ffbfea6a146d2680cff9338ae3cc006ed9408a7ec15a00a159d0"
COUNTS = json.loads(Path(__file__).with_name("results").joinpath("sf1-counts.json").read_text())["tpch"]


def pinned_text(directory, relative, expected):
    content = (directory / relative).read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError(f"File differs from BenchmarkTool {REVISION}: {relative}")
    return content.decode()


def query(container, database, statement, *, headers=False):
    return subprocess.run(
        ["docker", "exec", "-i", container, "mysql", "--protocol=tcp", "-h127.0.0.1", "-P9030",
         "-uroot", "-B" if headers else "-NB", "--database=" + database],
        input=statement, text=True, capture_output=True, check=True,
    ).stdout.strip()


def rows(text, separator):
    result = []
    for line in text.splitlines():
        priority, count = line.split(separator)
        result.append([priority.strip(), int(count)])
    return result


def qualification_sql(text, number):
    sql, limit, tail = re.split(r"(?im)^where rownum <= (-?\d+);\s*$", text)
    if tail.strip():
        raise ValueError(f"Unexpected content after QGEN row limit for Q{number}")
    sql = re.sub(r"(?i)\b(day|month|year)\s*\(\d+\)", r"\1", sql)
    if number == 15:
        # The load script installs the equivalent revenue0 view for the fixed date.
        sql = next(part for part in sql.split(";") if part.strip().lower().startswith("select"))
    if number == 22:
        sql = sql.replace("substring(c_phone from 1 for 2)", "substring(c_phone, 1, 2)")
    sql = sql.rstrip().rstrip(";")
    return sql + (f" LIMIT {limit}" if int(limit) > 0 else "")


def qualification_fingerprint(directory, extension, expected):
    manifest = []
    for number in range(1, 23):
        path = directory / f"q{number}.{extension}"
        sha = hashlib.sha256(path.read_text().encode()).hexdigest()
        manifest.append(f"{path.name}:{sha}\n")
    fingerprint = hashlib.sha256("".join(manifest).encode()).hexdigest()
    if fingerprint != expected:
        raise ValueError(f"Qualification {extension} files differ from BenchmarkTool {REVISION}")
    return fingerprint


def qualify(kit, generated, output, cmd):
    output.mkdir(parents=True, exist_ok=True)
    checker = kit / "check_answers"
    shutil.copyfile(checker / "colprecision.txt", output / "colprecision.txt")
    results = {}
    for number in range(1, 23):
        label = f"q{number}"
        sql = qualification_sql((generated / f"{label}.sql").read_text(), number)
        (output / f"{label}.sql").write_text(sql + ";\n")
        try:
            actual = cmd(sql, headers=True)
        except subprocess.CalledProcessError as error:
            results[label] = {"passed": False, "error": error.stderr.strip()}
            continue
        actual_path = output / f"{label}.out"
        actual_path.write_text(actual.replace("\t", "|") + "\n")
        expected_path = kit / "answers" / f"{label}.out"
        subprocess.run(["perl", str(checker / "cmpq.pl"), str(number), str(expected_path), actual_path.name],
                       cwd=output, capture_output=True, check=True)
        log = (output / f"analysis_{number}.log").read_text()
        results[label] = {
            "passed": "Found 0 unacceptable missmatches\n" in log,
            "expected_rows": len(expected_path.read_text().splitlines()) - 1,
            "actual_rows": len(actual.splitlines()) - 1,
        }
    return results


def source_fingerprint(directory):
    hashes = {}
    for name in sorted(COUNTS):
        with (directory / f"{name}.tbl").open("rb") as source:
            hashes[name] = hashlib.file_digest(source, "sha256").hexdigest()
    manifest = "".join(f"{name}:{sha}\n" for name, sha in hashes.items())
    return "tpch-sf1-" + hashlib.sha256(manifest.encode()).hexdigest()[:16], hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream", type=Path, help="Pinned BenchmarkTool checkout or archive")
    parser.add_argument("--container", default="queryatlas-starrocks-1")
    parser.add_argument("--database", default="qa_tpch_sf1")
    parser.add_argument("--data-directory", type=Path, help="Source SF1 .tbl files from gen-tpch.sh")
    parser.add_argument("--qualification-sql", type=Path, help="Q1–Q22 SQL produced by pinned qgen -d -s 1")
    parser.add_argument("--output", type=Path, help="Directory for qualification SQL, rows and comparison logs")
    options = parser.parse_args()
    if bool(options.output) != bool(options.qualification_sql):
        parser.error("--qualification-sql and --output must be used together")
    stmt = pinned_text(options.upstream, STATEMENT, STATEMENT_SHA)
    expected = rows(pinned_text(options.upstream, ANSWER, ANSWER_SHA).split("\n", 1)[1], "|")
    cmd = partial(query, options.container, options.database)
    counts = {table: int(cmd(f"SELECT COUNT(*) FROM `{table}`")) for table in COUNTS}
    actual = rows(cmd(stmt), "\t")
    report = {
        "benchmark": "TPC-H SF1",
        "exact_row_comparison": "Q4",
        "starrocks_version": cmd("SELECT current_version()"),
        "benchmarktool_commit": REVISION,
        "sql_sha256": STATEMENT_SHA,
        "reference_sha256": ANSWER_SHA,
        "table_counts": counts,
        "expected": expected,
        "actual": actual,
        "passed": counts == COUNTS and actual == expected,
    }
    if options.data_directory:
        report["data_batch"], report["source_sha256"] = source_fingerprint(options.data_directory)
    if options.qualification_sql:
        kit = options.upstream.resolve() / "thirdparty/tpch-dbgen"
        report["qualification_sql_sha256"] = qualification_fingerprint(
            options.qualification_sql, "sql", QUALIFICATION_SQL_SHA
        )
        report["qualification_answers_sha256"] = qualification_fingerprint(
            kit / "answers", "out", QUALIFICATION_ANSWERS_SHA
        )
        results = qualify(kit, options.qualification_sql, options.output, cmd)
        report["qualification"] = results
        report["passed"] &= all(result["passed"] for result in results.values())
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
