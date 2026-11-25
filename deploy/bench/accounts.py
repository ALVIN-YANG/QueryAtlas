"""Run on host with its protected .env and local Docker permission."""

from pathlib import Path
import re
from load import sql

values = dict(
    x.split("=", 1)
    for x in (Path.home() / "queryatlas/.env").read_text().splitlines()
    if "=" in x
)
for user, key in [("qa_read", "QA_SR_READ"), ("qa_load", "QA_SR_LOAD")]:
    password = values[key]
    assert re.fullmatch(r"[0-9a-f]{48}", password)
    sql(f"CREATE USER IF NOT EXISTS '{user}' IDENTIFIED BY '{password}'")
sql("CREATE DATABASE IF NOT EXISTS qa_imports")
for db in ["qa_tpch_sf1", "qa_ssb_sf1", "qa_tpcds_sf1", "qa_imports"]:
    sql(f"GRANT SELECT ON ALL TABLES IN DATABASE {db} TO USER qa_read")
sql("GRANT CREATE TABLE ON DATABASE qa_imports TO USER qa_load")
sql("GRANT INSERT,SELECT ON ALL TABLES IN DATABASE qa_imports TO USER qa_load")

sql("GRANT SELECT ON ALL VIEWS IN DATABASE qa_tpch_sf1 TO USER qa_read")
