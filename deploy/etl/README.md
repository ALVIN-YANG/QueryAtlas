# MySQL → StarRocks 批量同步

`python -m queryatlas.mysql_sync` 将 MySQL InnoDB 表按配置同步到 StarRocks。全量从同一个 MySQL 快照读取各表。增量从上次发布的位置读取变更主键，再查询这些主键在当前快照里的最终状态，支持新增、更新和删除。

同步先写新的 StarRocks 数据库。行数校验通过后，PostgreSQL 在同一事务里更新目录、当前批次和增量位置，并记录审计。问答始终查询已发布的版本。校验失败会保留上一版。对于没有新变更的增量，命令返回 `unchanged`。

## 从控制台运行

平台管理员在部署目录的 `mysql-profiles/profiles.json` 登记连接。每个连接只授权给一个工作空间，网页只收到连接名。给文件设置仅部署用户可读的权限。下面的文件内容用于说明字段，连接名可以自定。

```json
{
  "orders_readonly": {
    "space": "demo",
    "label": "订单只读库",
    "host": "mysql.internal",
    "port": 3306,
    "database": "orders",
    "user": "queryatlas_read",
    "password": "填写源库只读账号的密码"
  }
}
```

在 `.env` 配置工作进程的 `QA_ETL_SR_USER`、`QA_ETL_SR_PASSWORD`，再启动同步工作进程。

```sh
docker compose --profile mysql-sync up -d mysql-sync
```

打开「管理控制台 → 数据导入 → MySQL 同步」。添加数据源时选择连接、填写数据集名称和说明，将 [TPC-H 示例配置](tpch-sf1.json) 中的 `tables` 对象填入表映射。先点「全量同步」。源库产生变更批次后点「增量同步」。同步记录显示任务状态、操作人、发布批次、耗时和失败原因。展开成功记录可看每张表发布、读取和删除的行数。

| 工作空间角色 | 可执行操作 |
| --- | --- |
| 同步查看者 | 查看数据源、发布批次、任务记录和失败原因 |
| 同步操作员 | 查看记录并手动触发同步 |
| 同步管理员 | 配置数据源、查看记录并手动触发同步 |

同步权限独立于问数权限。需要在问答中查询结果时，再授予数据集的分析权限。

## 接入源库

用 [TPC-H 配置](tpch-sf1.json) 作为参考，设置目标工作空间、数据集名、源表、字段类型及整数主键。配置的主键须与 MySQL 的主键一致。源库需要 InnoDB 变更表。

```sql
CREATE TABLE qa_etl_changes (
    batch_id BIGINT UNSIGNED NOT NULL,
    table_name VARCHAR(64) NOT NULL,
    key_json JSON NOT NULL,
    KEY (table_name, batch_id),
    KEY (batch_id)
) ENGINE=InnoDB;
```

源端每次写入都在同一事务里写入数据和变更主键。复合主键使用配置顺序，例如 `[16, 3]`。更改主键时同时记旧键和新键。写入方须串行分配和提交严格递增的正整数批次，同一事务的多条记录可共用批次。确认所有消费者已越过某个批次前，保留它的日志。计数无法发现漏记的更新，因此完整记录变更是源端接口的要求。

在已执行 PostgreSQL 初始化迁移的 QueryAtlas 环境运行。MySQL 数据账户只需已配置表及变更表的 SELECT 权限。StarRocks 同步账户需要创建候选库、写入和删除数据、授权现有查询账户。应用查询使用独立只读身份。Stream Load 指向受信的 BE HTTP 地址，可配置 HTTPS。凭据由运行环境注入。

| 环境变量 | 用途 |
| --- | --- |
| `DATABASE_URL` | QueryAtlas 元数据库 |
| `QA_ETL_MYSQL_HOST`、`QA_ETL_MYSQL_PORT`、`QA_ETL_MYSQL_DB` | 源地址、端口（默认 3306）和库 |
| `QA_ETL_MYSQL_USER`、`QA_ETL_MYSQL_PASSWORD`、`QA_ETL_MYSQL_CA` | 源账户、密码及可选 TLS CA 文件 |
| `QA_ETL_SR_HOST`、`QA_ETL_SR_PORT`、`QA_ETL_SR_LOAD_URL` | StarRocks SQL 地址、端口（默认 9030）和 BE Stream Load 地址 |
| `QA_ETL_SR_USER`、`QA_ETL_SR_PASSWORD`、`QA_ETL_SR_READER` | 同步账户、密码和已有只读账户（默认 `qa_read`） |

```sh
python -m queryatlas.mysql_sync full deploy/etl/tpch-sf1.json --operator admin
python -m queryatlas.mysql_sync incremental deploy/etl/tpch-sf1.json --operator admin
```

增量只从 MySQL 传输变化的行。StarRocks 在库内复制上一版并合并增量，耗时和磁盘占用也受全量数据规模影响。每次发布后保留旧版供核查。问答历史仍保留回答文本，查看换批后的数据明细和引用时需重新提问。同名数据集只接受同一源和结构。同步按批次执行，控制台和命令行共用同一套校验与发布逻辑。MySQL binlog CDC、调度和自动清理历史版本可单独建设。

## 复现 TPC-H SF1

本实验以 [StarRocks BenchmarkTool](https://github.com/StarRocks/BenchmarkTool/tree/0f9ce82ea978318e2830767ad27028659c7434fa) 固定版本为准。先生成 SF1 `orders.tbl`、`lineitem.tbl`，再在独立目录用同版本 DBGEN 生成 [TPC-H RF1/RF2](https://www.tpc.org/TPC_Documents_Current_Versions/pdf/TPC-H_v3.0.1.pdf) 刷新文件。

```sh
DSS_CONFIG=/path/to/tpch-dbgen /path/to/tpch-dbgen/dbgen -s 1 -U 1
```

用单独的空测试库播种。此步骤使用具备建表、写入权限的实验账户，且 MySQL 实例允许 `LOAD DATA LOCAL INFILE`。

```sh
python -m deploy.etl.tpch_source seed /path/to/sf1
python -m queryatlas.mysql_sync full deploy/etl/tpch-sf1.json
python -m deploy.etl.verify_tpch /path/to/BenchmarkTool --full

python -m deploy.etl.tpch_source refresh /path/to/refresh --batch 1
python -m queryatlas.mysql_sync incremental deploy/etl/tpch-sf1.json
python -m deploy.etl.verify_tpch /path/to/BenchmarkTool
python -m queryatlas.mysql_sync incremental deploy/etl/tpch-sf1.json
```

全量核对官方 Q4 答案与两个表的 SF1 行数。增量后两库分别执行 Q4 并对账，同时核对订单总额、明细总额及商品数量。增量后的 Q4 与同一刷新版本的 MySQL 比较。网页从「知识资产 → 分析数据」查看已发布的 `tpch_mysql`，审计记录可按 `data.mysql.sync` 核对发布。

[网页触发、权限和问答验收](results/tpch-sf1-console-2026-11-25.json) · [命令行全量、增量及故障验证](results/tpch-sf1-2026-11-25.json)
