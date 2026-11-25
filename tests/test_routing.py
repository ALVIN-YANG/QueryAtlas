import unittest

from queryatlas.routing import dataset_for


class RoutingTests(unittest.TestCase):
    def test_auto_distinguishes_benchmark_metrics_from_contracts(self):
        names = ["tpch", "ssb", "tpcds"]
        self.assertEqual(
            dataset_for("按客户市场分段统计客户数", "auto", names),
            ("tpch", False),
        )
        self.assertEqual(
            dataset_for("客户合同续签费用是多少？", "auto", names, "tpch"),
            (None, False),
        )

    def test_explicit_scope_and_ambiguity(self):
        names = ["tpch", "ssb", "tpcds"]
        for question, scope, expected in [
            ("TPCH 和 TPC-DS 的数据量", "auto", (None, True)),
            ("TPCH 和 TPC-DS 的数据量", "tpcds", ("tpcds", False)),
            ("同一订单的市场分段", "docs", (None, False)),
            ("1993 年第三季度按优先级统计延期订单占比", "auto", ("tpch", False)),
            ("各门店销售额是多少？", "auto", ("tpcds", False)),
        ]:
            with self.subTest(question=question, scope=scope):
                self.assertEqual(dataset_for(question, scope, names), expected)
        self.assertEqual(dataset_for("按市场分段看客户数", "auto", ["ssb"]), (None, False))
