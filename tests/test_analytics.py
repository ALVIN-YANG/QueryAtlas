import unittest
from unittest.mock import MagicMock, patch

from queryatlas.answers import numerical
from queryatlas.sql_templates import TemplateMatch


class AnalyticsTests(unittest.TestCase):
    def test_validates_starrocks_monthly_aggregation(self):
        sql = """SELECT DATE_TRUNC('month', l_shipdate) AS ship_month,
            SUM(l_extendedprice * l_discount) AS discount_amount,
            COUNT(*) AS line_count
            FROM lineitem GROUP BY DATE_TRUNC('month', l_shipdate)
            ORDER BY ship_month"""
        dataset = {
            "name": "tpch",
            "physical": "qa_tpch_sf1",
            "tables": {"lineitem": ["l_shipdate", "l_extendedprice", "l_discount"]},
        }
        with (
            patch("queryatlas.answers.complete", return_value=sql),
            patch("queryatlas.answers.sr", return_value=MagicMock()),
            patch("queryatlas.answers.execute") as execute,
        ):
            numerical("按发货日期所在自然月汇总", dataset, {}, True, "", {})
        database, statement = execute.call_args.args
        self.assertEqual(database, "qa_tpch_sf1")
        self.assertIn("DATE_TRUNC('MONTH', l_shipdate)", statement)
        self.assertIn("FROM `lineitem`", statement)
        self.assertTrue(statement.endswith("LIMIT 101"))

    def test_configured_template_reuses_query_permissions_without_model(self):
        template = TemplateMatch(
            id="regional_sales",
            sql="SELECT SUM(amount) AS total FROM sales",
        )
        dataset = {
            "name": "sales",
            "physical": "qa_imports",
            "description": "published_sales",
            "tables": {"sales": ["amount", "region", "customer"]},
        }
        policy = {"sales": {"fields": ["amount"], "filter_col": "region", "filter_value": "East"}}
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.mogrify.return_value = "'East'"
        timings = {}
        with (
            patch("queryatlas.answers.complete") as model,
            patch("queryatlas.answers.sr", return_value=connection),
            patch("queryatlas.answers.execute", return_value={"rows": [[60]]}) as execute,
        ):
            result = numerical(
                "审核区域销额", dataset, policy, True, "", timings, template=template
            )
        model.assert_not_called()
        database, statement = execute.call_args.args
        self.assertEqual(database, "qa_imports")
        self.assertIn("SELECT `amount` FROM `published_sales` WHERE `region` = 'East'", statement)
        self.assertNotIn("customer", statement)
        self.assertEqual(result["sql_source"], {"kind": "template", "id": "regional_sales"})
        self.assertNotIn("sql_generation", timings)
