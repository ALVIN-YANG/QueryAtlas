# StarRocks SF1 调优实测

2026-11-24，在本地测试环境的 StarRocks `4.1.8-b16342f` 单 BE 环境执行。实验库为 `qa_tuning_e6b15b11`。分区和物化视图使用同一批 TPC-H SF1 的 `lineitem` 数据，两个副本均为 6,001,215 行，订单键与扩展价格校验和一致。JOIN 直接读取同一版本的官方基准库。

每个方案预热 3 次，计时 30 次，交替交换执行顺序。固定 `pipeline_dop=3`，关闭 Query Cache。计时包括 SQL 执行与结果读取，使用预先建立的连接。P95 取第 29 个有序样本。每次执行都与基线逐行比对，金额按 DECIMAL 比对。执行计划和 Query Profile 分开采集，计时期间关闭 Profile。

| 对照 | 一致的结果 | P50 前 → 后 | P95 前 → 后 | P95 变化 |
|---|---|---:|---:|---:|
| Q6 发货日期分区 | 1 行，折扣金额 `123141078.2283` | 16.790 → 17.024 ms | 21.805 → 24.175 ms | +10.87% |
| 月度折扣汇总物化视图 | 84 个月的金额和明细数 | 78.125 → 11.977 ms | 87.536 → 16.760 ms | −80.85% |
| 延期订单 JOIN 改写 | 5 个优先级的订单数和延期订单数 | 77.087 → 62.443 ms | 107.785 → 76.432 ms | −29.09% |

## Q6 日期分区

两份表使用相同的列、排序键和每分区 4 桶。优化方案增加按 `l_shipdate` 的年度范围分区。执行计划从 `lineitem_base partitions=1/1` 变为 `lineitem_year partitions=1/7`，命中年度内都读取 4 个 tablet。

Profile 的原始读取行数从 983,040 降到 909,455，读取量从 25.689 MB 降到 24.121 MB。基线依靠 Zone Map 已过滤约 502 万行。本次证实分区裁剪生效，P95 未改善。

## 月度汇总物化视图

两侧使用同一条 SQL，用会话变量控制物化视图改写。计划中的扫描表由 `lineitem_base` 变为 `mv_monthly_discount`。Profile 的原始读取行数从 6,001,215 降到 84，读取量从 114.464 MB 降到 2.539 KB。手动同步刷新调用耗时 0.816 秒。

## 延期订单 JOIN

基线先按订单键去重所有延期明细，再关联季度订单。候选使用 `EXISTS` 筛选季度内至少有一条延期明细的订单，分别汇总分母和分子。优化前的计划包含明细键聚合和 `RIGHT OUTER JOIN (COLOCATE)`。优化后使用 `RIGHT SEMI JOIN (COLOCATE)`，再关联优先级汇总。

| 订单优先级 | 订单数 | 延期订单数 |
|---|---:|---:|
| 1-URGENT | 11,522 | 10,594 |
| 2-HIGH | 11,460 | 10,476 |
| 3-MEDIUM | 11,343 | 10,410 |
| 4-NOT SPECIFIED | 11,495 | 10,556 |
| 5-LOW | 11,398 | 10,487 |

候选少了全局明细去重，但 Profile 里多扫了一次 `orders`。读取量合计从约 79.82 MB 升到 86.91 MB，Profile 中的累计内存分配量从 21.779 MB 升到 29.695 MB。这组单机实测 P95 有收益，P50 降幅约 19%，略低于既定 20% 对照门槛。

## 复现

在已有 TPC-H SF1 数据和 PyMySQL 环境的基准主机运行。从仓库根目录执行，每次生成新的实验库和输出目录。

```sh
~/queryatlas-bench/tools-env/bin/python deploy/bench/tuning.py \
  ~/queryatlas-bench/tuning-20261124-replay --repeats 30
```

输出包含建表和刷新 SQL、逐组原始结果、每次延迟和 Query ID，以及优化前后的完整执行计划和 Profile。[调优脚本](../tuning.py) · [Query Profile 指南](https://docs.starrocks.io/docs/best_practices/query_tuning/query_profile_overview/)
