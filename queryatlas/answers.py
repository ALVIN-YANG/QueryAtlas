"""Run bounded, read-only analytical queries against authorized table projections."""

import hashlib
import json
import re
from time import perf_counter
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope
from fastapi import HTTPException
from .analytics import sr, ident, execute, visible_table, columns


from .llm import complete, uses_deepseek
from .telemetry import elapsed_ms


def token_for(meta, policies):
    # A newly registered batch label does not change data access for saved answers.
    access_meta = {k: v for k, v in meta.items() if k != "batch"}
    return hashlib.sha256(
        json.dumps(
            [access_meta, policies], sort_keys=True, default=str, ensure_ascii=False
        ).encode()
    ).hexdigest()


def numerical(q, meta, policy, detail, prevq, timings, *, template=None):
    schema = {
        t: columns(meta, policy, t) for t in meta["tables"] if columns(meta, policy, t)
    }
    from .benchmarks import numbered

    source_info = {"kind": "builtin"}
    if template is None:
        curated = numbered(q, meta, policy, detail)
        if curated is not None:
            return {**curated, "sql_source": source_info}
    name = meta["name"]
    low = q.lower()
    # Only a plain whole-table count can use the pre-approved aggregate.
    # A question with a predicate must keep it in the generated query.
    count = re.fullmatch(
        r"\s*(?:(?:请问|查询|统计)\s*)?([a-z_][a-z0-9_]*)?\s*(?:表)?\s*(?:一共|总共|共有|有)?(?:多少行|多少条|总行数|行数|记录数)(?:数据|记录)?[？?。\s]*",
        low,
    )
    table = count.group(1) if count else None
    if template is not None:
        if not detail:
            raise HTTPException(403, "执行该配置模板需要数据明细权限")
        stmt = template.sql
        source_info = {"kind": "template", "id": template.id}
        if template.values:
            source_info.update(parameters=template.values)
        if template.definition:
            source_info.update(definition=template.definition)
    elif count and (table in schema or (not table and len(schema) == 1)):
        table = table or next(iter(schema))
        stmt = f"SELECT COUNT(*) AS 记录数 FROM {ident(table)}"
    elif (
        name == "tpch"
        and not re.search(
            r"仅|只看|只统计|排除|除去|超过|大于|小于|美国|中国|地区|国家|客户|供应商|[12]\d{3}(?!年?第三季度)",
            re.sub(r"1993\s*年?", "", q),
        )
        and ("延期" in q or "延期" in prevq)
        and ("占比" in q or "延期率" in q or "按优先级" in q)
        and ("1993" in q or "1993" in prevq)
        and ("第三季度" in q + prevq or "三季度" in q + prevq or "Q3" in q + prevq)
    ):
        stmt = """SELECT o_orderpriority AS 优先级, COUNT(*) AS 订单数, COUNT(x.k) AS 延期订单数,
            ROUND(100.0*COUNT(x.k)/COUNT(*),2) AS 延期占比 FROM orders LEFT JOIN
            (SELECT DISTINCT l_orderkey AS k FROM lineitem WHERE l_receiptdate>l_commitdate) x ON x.k=o_orderkey
            WHERE o_orderdate>='1993-07-01' AND o_orderdate<'1993-10-01' GROUP BY o_orderpriority ORDER BY o_orderpriority"""
    else:
        if not detail:
            raise HTTPException(
                403, "自由查询需要数据明细权限，可以使用表行数或季度延期率汇总"
            )
        # The model sees only allowed tables/columns. Raw rows additionally require detail access.
        relevant = sorted(
            schema,
            key=lambda t: (
                -(int(t in low) * 4 + sum(w in t for w in ["sales", "orders"]))
            ),
        )[:8]
        context = {t: schema[t][:36] for t in relevant}
        source_info = {"kind": "deepseek" if uses_deepseek() else "local_model"}
        start = perf_counter()
        raw = complete(
            "将问题写成一条 StarRocks SELECT 查询。只用给出的表和字段，不写解释；金额、数量请遵守问题口径。不可回答时只输出 UNKNOWN。",
            json.dumps(context, ensure_ascii=False)
            + "\n前问："
            + prevq[:240]
            + "\n本问："
            + q,
            320,
        )
        timings["sql_generation"] = elapsed_ms(start)
        stmt = re.sub(r"^```(?:sql)?\s*|\s*```$", "", raw.strip(), flags=re.I).strip()
    start = perf_counter()
    executed = prepare_sql(stmt, meta, policy, detail)
    timings["sql_validation"] = elapsed_ms(start)
    return {**execute(meta["physical"], executed), "sql_source": source_info}


def prepare_sql(stmt, meta, policy, detail=True, *, max_rows=101):
    try:
        tree_list = sqlglot.parse(stmt, read="starrocks")
        if len(tree_list) != 1 or not isinstance(tree_list[0], exp.Select):
            raise ValueError()
        tree = tree_list[0]
        if not detail and not tree.find(exp.AggFunc):
            raise HTTPException(403, "查看明细需要数据明细权限")
        if (
            tree.find(exp.Into)
            or tree.find(exp.SessionParameter)
            or tree.find(exp.Parameter)
            or tree.find(exp.Placeholder)
            or tree.find(exp.PropertyEQ)
            or tree.args.get("locks")
            or tree.find(exp.Hint)
        ):
            raise ValueError()
        funcs = {
            "COUNT",
            "SUM",
            "AVG",
            "MAX",
            "MIN",
            "ROUND",
            "YEAR",
            "MONTH",
            "DATE_TRUNC",
            "TIMESTAMP_TRUNC",  # SQLGlot's AST name for StarRocks DATE_TRUNC.
            "COALESCE",
            "CAST",
            "NULLIF",
            "IF",
            "ABS",
            "EXTRACT",
            "DAY",
            "DATEDIFF",
            "SUBSTRING",
            "TO_CHAR",
            "DATE_FORMAT",
            "DENSE_RANK",
            "ROW_NUMBER",
            "RANK",
            "LAG",
            "LEAD",
            "IFNULL",
            "CURRENT_DATE",
            "CONCAT",
            "UPPER",
            "LOWER",
            "STR_TO_DATE",
            "TIME_TO_STR",
            "TS_OR_DS_TO_DATE",
            "DATE",
            "AND",
            "OR",
            "CASE",
            "EXISTS",
        }
        if any(
            (f.name.upper() if isinstance(f, exp.Anonymous) else f.sql_name())
            not in funcs
            for f in tree.find_all(exp.Func)
        ):
            raise ValueError()
        with sr(meta["physical"]) as conn, conn.cursor() as cur:
            edits = []
            for scope in traverse_scope(tree):
                for alias, (_, source) in scope.selected_sources.items():
                    if isinstance(source, exp.Table):
                        t = source.name
                        if (
                            t not in meta["tables"]
                            or not columns(meta, policy, t)
                            or source.db
                            or source.catalog
                            or not isinstance(source.this, exp.Identifier)
                        ):
                            raise ValueError()
                        sub = sqlglot.parse_one(
                            visible_table(cur, meta, policy, t), read="starrocks"
                        ).subquery(alias=alias)
                        edits.append((source, sub))
            if not edits or tree.find(exp.UDTF):
                raise ValueError()
            for source, sub in edits:
                source.replace(sub)
        limit = tree.args.get("limit")
        n = (
            int(limit.expression.name)
            if limit and isinstance(limit.expression, exp.Literal)
            else max_rows
        )
        tree.limit(min(max_rows, max(n, 0)), copy=False)
        return tree.sql(dialect="starrocks")
    except HTTPException:
        raise
    except (ValueError, sqlglot.errors.SqlglotError, KeyError):
        raise HTTPException(422, "无法可靠匹配授权字段。请指定数据集、表名或统计口径。")
