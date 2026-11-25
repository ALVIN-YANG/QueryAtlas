import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymysql

from queryatlas.mysql_profiles import profiles
from queryatlas.mysql_source import MysqlConnection, SyncRejected, SyncSpec
from queryatlas.mysql_worker import failure


class MysqlWorkerTest(unittest.TestCase):
    def test_credential_rotation_keeps_existing_cli_checkpoint(self):
        tables = {"orders": {"key": ["id"], "columns": {"id": "BIGINT"}}}
        spec = SyncSpec(space="demo", dataset="sales", description="Orders", tables=tables)
        original_hash = hashlib.sha256(json.dumps({
            "host": "mysql", "port": "3306", "database": "orders", "tables": tables,
        }, sort_keys=True).encode()).hexdigest()
        profile = MysqlConnection(host="mysql", database="orders", user="reader", password="rotated")
        self.assertEqual(spec.fingerprint(profile), original_hash)
        self.assertNotEqual(spec.fingerprint(profile.model_copy(update={"database": "other"})), original_hash)

    def test_malformed_profile_does_not_reveal_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profiles.json"
            path.write_text(json.dumps({"my-db": {"password": "sensitive-test-value"}}))
            with patch.dict("os.environ", {"QA_MYSQL_PROFILES": str(path)}):
                with self.assertRaises(SyncRejected) as result:
                    profiles("demo")
            self.assertNotIn("sensitive-test-value", str(result.exception))
            self.assertIsNone(result.exception.__cause__)

    def test_database_failure_reports_code_without_raw_details(self):
        error = pymysql.OperationalError(1045, "raw database diagnostics")
        self.assertEqual(failure(error), "数据库连接认证失败")
        self.assertNotIn("raw database diagnostics", failure(pymysql.OperationalError(1234, str(error))))
