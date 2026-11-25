import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from queryatlas.answers import token_for
from queryatlas.chat import display
from queryatlas.source_preview import html_preview


class MetricSourceTests(unittest.TestCase):
    def test_historical_metric_keeps_its_batch_without_leaking_revoked_rule(self):
        clauses = [{"version": 23, "seq": 0}, {"version": 23, "seq": 2}]
        dataset = {"name": "funds", "physical": "funds_v1", "tables": {}}
        judge = {"text": "68%", "data": "funds", "guard": token_for(dataset, {}),
                 "metric": {"version": 8, "batch": "batch-12"}, "rule_clauses": clauses,
                 "refs": [{"kind": "sql", "sql": "SELECT 68", "query_ids": ["qid"]}]}
        allowed = {"asset": "rule.html", "version": 23, "seq": 2, "body": "公式", "anchor": "第二条"}
        connection, user = MagicMock(), {"name": "reader"}
        with patch("queryatlas.chat.scope", return_value=["rule.html"]), \
             patch("queryatlas.chat.dataset", return_value=({**dataset, "batch": "batch-13"}, {})), \
             patch("queryatlas.chat.permitted", return_value=False), \
             patch("queryatlas.chat.permitted_doc", return_value=False), \
             patch("queryatlas.chat.resolve_clauses", return_value=[allowed]) as resolve:
            shown = display(connection, user, "space", judge)
            resolve.assert_called_once_with(connection, user, "space", clauses)
        self.assertNotIn("rule_clauses", shown)
        self.assertEqual(shown["metric"], {"version": 8, "batch": "batch-12", "rules_unavailable": True})
        self.assertEqual(shown["refs"], [{"kind": "sql"}, {"kind": "doc", "rule": True, "original": False, **allowed}])

    def test_html_original_preview_preserves_element_number_and_escapes_markup(self):
        with TemporaryDirectory() as folder:
            fixture = Path(folder) / "test.html"
            fixture.write_text('<h2>统计范围</h2><script>alert(1)</script><p>&lt;can&#39;t run&gt;</p>', encoding="utf-8")
            response = html_preview(fixture, "test.html")
        self.assertIn('<h2 id="element-1">统计范围</h2>', response.body.decode())
        self.assertIn('<p id="element-2">&lt;can&#x27;t run&gt;</p>', response.body.decode())
        self.assertNotIn(b"<script", response.body)
