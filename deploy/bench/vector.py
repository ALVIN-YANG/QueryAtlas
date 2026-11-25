"""Compare actual demo document embeddings with StarRocks on this single-node build."""

from load import sql, ROOT
import json, subprocess, secrets

db = "qa_vector_test"
sql("CREATE DATABASE IF NOT EXISTS " + db)
table = "hnsw_" + secrets.token_hex(3)
pg = [
    "sudo",
    "docker",
    "exec",
    "queryatlas-db-1",
    "psql",
    "-U",
    "qa",
    "-d",
    "queryatlas",
    "-At",
    "-c",
]
rows = subprocess.check_output(
    pg
    + [
        "SELECT (d.version*10000+n.seq)||'|'||n.embedding::text FROM docs d JOIN nodes n ON n.version=d.version WHERE space='demo' ORDER BY 1"
    ],
    text=True,
).splitlines()
items = [(int(line.partition("|")[0]), line.partition("|")[2]) for line in rows]
query = items[0][1]
prev = sql('ADMIN SHOW FRONTEND CONFIG LIKE "enable_experimental_vector"').split("\t")[
    2
]
result = {"node_count": len(rows)}
try:
    sql('ADMIN SET FRONTEND CONFIG ("enable_experimental_vector"="true")')
    base = f"CREATE TABLE {table}(id BIGINT NOT NULL, v ARRAY<FLOAT> NOT NULL"
    suffix = ') DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1")'
    try:
        sql(
            base
            + ',INDEX vix(v) USING VECTOR("index_type"="hnsw","dim"="512","metric_type"="cosine_similarity","is_vector_normed"="true")'
            + suffix,
            db,
        )
        indexed = True
    except ValueError as e:
        result["ddl_error"] = str(e)
        sql(base + suffix, db)
        indexed = False
    sql(
        f"INSERT INTO {table} VALUES " + ",".join(f"({i},{vec})" for i, vec in items),
        db,
    )
    result["exact"] = sql(
        f"SELECT id FROM {table} ORDER BY cosine_similarity(v,{query}) DESC,id LIMIT 5",
        db,
    ).splitlines()
    expected = subprocess.check_output(
        pg
        + [
            f"select d.version*10000+n.seq from docs d join nodes n on n.version=d.version where space='demo' order by n.embedding <=> '{query}'::vector,d.version*10000+n.seq limit 5"
        ],
        text=True,
    ).splitlines()
    result["pg_exact"] = expected
    if indexed:
        probe = f"SELECT id,approx_cosine_similarity({query},v) AS dist FROM {table} ORDER BY dist DESC LIMIT 5"
        try:
            result["explain"] = [
                l
                for l in sql("EXPLAIN " + probe, db).splitlines()
                if "vectorindex" in l.lower()
                or "ivfpq" in l.lower()
                or "hnsw" in l.lower()
            ]
            result["ann"] = sql(probe, db).splitlines()
        except ValueError as e:
            result["ann_error"] = str(e)
finally:
    sql(f'ADMIN SET FRONTEND CONFIG ("enable_experimental_vector"="{prev}")')
(ROOT / "vector.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, ensure_ascii=False)[:2200])
