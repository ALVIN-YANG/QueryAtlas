"""Load pinned TPC-H DBGEN files into an isolated MySQL benchmark source."""

import argparse
from contextlib import closing
from pathlib import Path

from deploy.bench.verify_tpch import COUNTS
from queryatlas.analytics import ident
from queryatlas.mysql_source import SyncSpec, mysql

SPEC = SyncSpec.model_validate_json(Path(__file__).with_name("tpch-sf1.json").read_text())


def load(cur, table, path, columns):
    if not path.is_file():
        raise FileNotFoundError(path.name)
    fields = ",".join(map(ident, columns))
    cur.execute(
        f"LOAD DATA LOCAL INFILE %s INTO TABLE {ident(table)} "
        "FIELDS TERMINATED BY '|' ESCAPED BY '' LINES TERMINATED BY '\\n' "
        f"({fields}, @trailing)",
        (str(path.resolve()),),
    )
    if cur.warning_count:
        raise ValueError(f"MySQL returned {cur.warning_count} warnings loading {path.name}")
    return cur.rowcount


def seed(cur, folder):
    for name in SPEC.tables:
        if not (folder / f"{name}.tbl").is_file():
            raise FileNotFoundError(f"{name}.tbl")
    cur.execute("SELECT GET_LOCK(CONCAT(DATABASE(),'.tpch_refresh'), 10)")
    if cur.fetchone() != (1,):
        raise ValueError("could not acquire benchmark fixture lock")
    for name, spec in SPEC.tables.items():
        columns = ", ".join(f"{ident(col)} {kind} NOT NULL" for col, kind in spec.columns.items())
        key = ",".join(map(ident, spec.key))
        cur.execute(f"CREATE TABLE IF NOT EXISTS {ident(name)} ({columns}, PRIMARY KEY ({key})) ENGINE=InnoDB")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS qa_etl_changes (batch_id BIGINT UNSIGNED NOT NULL, "
        "table_name VARCHAR(64) NOT NULL,key_json JSON NOT NULL,KEY (table_name,batch_id),KEY (batch_id)) ENGINE=InnoDB"
    )
    for name in (*SPEC.tables, "qa_etl_changes"):
        cur.execute(f"SELECT 1 FROM {ident(name)} LIMIT 1")
        if cur.fetchone():
            raise ValueError(f"{name} already has data; seed an empty fixture database")
    for name, spec in SPEC.tables.items():
        count = load(cur, name, folder / f"{name}.tbl", spec.columns)
        if count != COUNTS[name]:
            raise ValueError(f"{name}: expected {COUNTS[name]} rows for SF1, received {count}")
        print(name, count, flush=True)


def refresh(cur, folder, batch):
    # The named lock serializes fixture writers, including across a commit.
    cur.execute("SELECT GET_LOCK(CONCAT(DATABASE(),'.tpch_refresh'), 10)")
    if cur.fetchone() != (1,):
        raise ValueError("could not acquire benchmark refresh lock")
    cur.execute("SELECT COALESCE(MAX(batch_id),0) FROM qa_etl_changes")
    if cur.fetchone()[0] + 1 != batch:
        raise ValueError("apply each official refresh set once, in order")
    cur.execute("CREATE TEMPORARY TABLE remove_orders (orderkey BIGINT PRIMARY KEY)")
    print("delete order keys", load(cur, "remove_orders", folder / f"delete.{batch}", ["orderkey"]), flush=True)
    for name, spec in SPEC.tables.items():
        table = ident(name)
        insert = ident(f"insert_{name}")
        cur.execute(f"CREATE TEMPORARY TABLE {insert} LIKE {table}")
        print(name, "insert", load(cur, f"insert_{name}", folder / f"{name}.tbl.u{batch}", spec.columns), flush=True)
        key = ",".join(map(ident, spec.key))
        first = ident(spec.key[0])
        cur.execute(
            "INSERT INTO qa_etl_changes SELECT %s,%s,JSON_ARRAY(" + key + ") "
            f"FROM {table} JOIN remove_orders ON {first}=orderkey "
            f"UNION ALL SELECT %s,%s,JSON_ARRAY({key}) FROM {insert}",
            (batch, name, batch, name),
        )
        cur.execute(f"DELETE t FROM {table} t JOIN remove_orders ON {first}=orderkey")
        print(name, "delete", cur.rowcount, flush=True)
        cur.execute(f"INSERT INTO {table} SELECT * FROM {insert}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["seed", "refresh"])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--batch", type=int, default=1)
    args = parser.parse_args()
    with closing(mysql(writer=True)) as conn, conn.cursor() as cur:
        if args.mode == "seed":
            seed(cur, args.folder)
        else:
            refresh(cur, args.folder, args.batch)
        conn.commit()
