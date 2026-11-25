-- Transparent rewrite for the sealed TPC-H SF1 snapshot.
USE qa_tpch_sf1;

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_lineitem_monthly_discount
DISTRIBUTED BY HASH(ship_month) BUCKETS 4
REFRESH DEFERRED MANUAL
PROPERTIES ("replication_num" = "1")
AS SELECT DATE_TRUNC('month', l_shipdate) AS ship_month,
          SUM(l_extendedprice * l_discount) AS discount_amount,
          COUNT(*) AS line_count
   FROM lineitem
   GROUP BY DATE_TRUNC('month', l_shipdate);

REFRESH MATERIALIZED VIEW mv_lineitem_monthly_discount WITH SYNC MODE;
