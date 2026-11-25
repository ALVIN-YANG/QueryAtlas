import unittest
from unittest.mock import MagicMock

from pydantic import ValidationError

from queryatlas.routing import dataset_for
from queryatlas.sql_template_parameters import Parameter, bind_sql
from queryatlas.sql_templates import NewTemplate, matching_templates


class SqlTemplateTests(unittest.TestCase):
    def test_matches_in_workspace_with_authorized_dataset_candidates(self):
        db = MagicMock()
        db.execute.return_value = [{
            "dataset": "inventory", "id": "total", "sql": "SELECT 1",
            "normalized": "库存 kpi", "parameters": {}, "definition": "",
            "revision": 3, "rule_clauses": [{"version": 18, "seq": 2}],
        }]
        names = ["inventory", "sales"]
        matches = matching_templates(db, "operations", "  库存   KPI？", names)
        sql, values = db.execute.call_args.args
        self.assertEqual(values, ("operations", names, "库存 kpi", "库存 kpi"))
        self.assertIn("t.enabled", sql)
        self.assertEqual(matches["inventory"].id, "total")
        self.assertEqual(matches["inventory"].revision, 3)
        self.assertEqual(matches["inventory"].rule_clauses, ({"version": 18, "seq": 2},))
        self.assertEqual(
            dataset_for("库存 KPI", "auto", names, template_matches=matches),
            ("inventory", False),
        )
        self.assertEqual(
            dataset_for("库存 KPI", "docs", names, template_matches=matches),
            (None, False),
        )
        self.assertEqual(
            dataset_for("库存 KPI", "sales", names, template_matches=matches),
            ("sales", False),
        )
        db.execute.reset_mock()
        self.assertFalse(matching_templates(db, "operations", "库存 KPI", []))
        db.execute.assert_not_called()

    def test_rejects_duplicate_normalized_aliases(self):
        with self.assertRaises(ValidationError):
            NewTemplate(
                id="inventory_total",
                dataset="inventory",
                aliases=("库存 KPI", "库存 kpi？"),
                sql="SELECT SUM(quantity) FROM inventory",
            )

    def test_binds_date_and_clarifies_missing_or_invalid_input(self):
        db = MagicMock()
        db.execute.return_value = [{
            "dataset": "funds", "id": "payout", "sql": "SELECT SUM(amount) FROM funds WHERE paid_on <= :day",
            "normalized": "截至{day}付款",
            "parameters": {"day": {"type": "date", "label": "截止日期"}},
            "definition": "截止日含当天",
            "revision": 1, "rule_clauses": [],
        }]
        result = matching_templates(db, "operations", "截至 2026-10-31 付款？", ["funds"])["funds"]
        self.assertEqual(result.values, {"day": "2026-10-31"})
        self.assertIn("paid_on <= '2026-10-31'", result.sql)
        for question, phrase in [("截至付款", "请补充截止日期"), ("截至2026-02-31付款", "请提供有效的截止日期")]:
            with self.subTest(question=question):
                result = matching_templates(db, "operations", question, ["funds"])["funds"]
                self.assertIn(phrase, result.problem)
                self.assertFalse(result.sql)
        plain = db.execute.return_value[0]
        alternate = {**plain, "normalized": "截至{day}的付款"}
        db.execute.return_value = [plain, alternate]
        match = matching_templates(db, "operations", "截至 2026-10-31 的付款", ["funds"])["funds"]
        self.assertEqual(match.values, {"day": "2026-10-31"})
        db.execute.return_value.append({**alternate, "id": "another_metric"})
        self.assertIsNone(matching_templates(db, "operations", "截至 2026-10-31 的付款", ["funds"])["funds"])

    def test_enum_and_integer_bind_as_values(self):
        params = {
            "district": Parameter(type="enum", label="片区", choices=("East", "West")),
            "min_amount": Parameter(type="integer", label="起始金额"),
        }
        stmt = "SELECT COUNT(*) FROM payments WHERE district = :district AND amount >= :min_amount"
        bound, values = bind_sql(stmt, params, {"district": "east", "min_amount": "100"})
        self.assertEqual(values, {"district": "East", "min_amount": 100})
        self.assertIn("district = 'East'", bound)
        self.assertIn("amount >= 100", bound)
        with self.assertRaisesRegex(ValueError, "比较条件"):
            bind_sql(
                "SELECT COUNT(*) FROM :district WHERE amount >= :min_amount",
                params, values,
            )
