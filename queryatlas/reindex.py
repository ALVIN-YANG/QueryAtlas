"""Backfill the selected model from the published node text, without changing citations."""

from .db import db
from .embedding import LOCAL_MODEL, MODEL, active_embedding

# Shared with the ingestion worker to keep the set of published versions stable.
INGEST_LOCK = 75288512


def store_vectors(conn, vectors):
    if MODEL == LOCAL_MODEL:
        statement = "UPDATE nodes SET embedding=%s::vector WHERE version=%s AND seq=%s"
        params = [(str(vector), version, seq) for version, seq, vector in vectors]
    else:
        statement = """INSERT INTO node_embeddings(version,seq,model,embedding) VALUES(%s,%s,%s,%s::vector)
                       ON CONFLICT(version,seq,model) DO NOTHING"""
        params = [
            (version, seq, MODEL, str(vector)) for version, seq, vector in vectors
        ]
    with conn.cursor() as cur:
        cur.executemany(statement, params)


def backfill():
    embedding = active_embedding()
    with db() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (INGEST_LOCK,))
        if MODEL == LOCAL_MODEL:
            missing = "n.embedding IS NULL"
            params = ()
        else:
            missing = """NOT EXISTS (SELECT 1 FROM node_embeddings e
                         WHERE e.version=n.version AND e.seq=n.seq AND e.model=%s)"""
            params = (MODEL,)
        total = 0
        while True:
            rows = conn.execute(
                f"""SELECT n.version, n.seq, n.body FROM docs d JOIN nodes n ON n.version=d.version
                    WHERE {missing} ORDER BY n.version,n.seq LIMIT 128""",
                params,
            ).fetchall()
            if not rows:
                break
            vectors = embedding.get_text_embedding_batch([row["body"] for row in rows])
            store_vectors(
                conn,
                [
                    (row["version"], row["seq"], vector)
                    for row, vector in zip(rows, vectors, strict=True)
                ],
            )
            total += len(rows)
    print(f"{MODEL}: backfilled {total} published vectors", flush=True)
