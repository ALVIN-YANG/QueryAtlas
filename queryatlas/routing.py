"""Choose among the data sources already authorized for this user."""

import re


BENCHMARK_SIGNALS = {
    "tpch": re.compile(
        r"(?<![a-z0-9_])(?:c_mktsegment|lineitem|orders|partsupp)(?![a-z0-9_])|市场分段|市场细分|"
        r"订单优先级|按优先级|延期(?:订单)?(?:率|占比)"
    ),
    "ssb": re.compile(r"(?<![a-z0-9_])lineorder(?:_flat)?(?![a-z0-9_])|星型|星形"),
    "tpcds": re.compile(r"(?<![a-z0-9_])(?:store|web|catalog)_sales(?![a-z0-9_])|门店销售|零售"),
}
BENCHMARK_ALIASES = {"tpch": "tpc-h", "tpcds": "tpc-ds"}
DOCUMENT_SIGNALS = re.compile(r"合同|协议|手册|制度|文档|政策")
FOLLOW_UP = re.compile(r"^(?:这些|刚才|其中|那(?:就|再)|再看|改成|继续按|同样)")


def dataset_for(question, source, available, previous=None, template_matches=()):
    """Return the selected dataset and whether multiple sources matched."""
    if source != "auto":
        return (source if source in available and source != "docs" else None), False
    matched_templates = [name for name in available if name in template_matches]
    if matched_templates:
        return (
            matched_templates[0] if len(matched_templates) == 1 else None,
            len(matched_templates) > 1,
        )
    text = question.strip().lower()
    named = [
        name
        for name in available
        if any(
            re.search(r"(?<![a-z0-9_])" + re.escape(alias) + r"(?![a-z0-9_])", text)
            for alias in {name.lower(), BENCHMARK_ALIASES.get(name, name.lower())}
        )
    ]
    if named:
        return (named[0] if len(named) == 1 else None), len(named) > 1
    if DOCUMENT_SIGNALS.search(text):
        return None, False
    matched = [
        name
        for name, pattern in BENCHMARK_SIGNALS.items()
        if name in available and pattern.search(text)
    ]
    if matched:
        return (matched[0] if len(matched) == 1 else None), len(matched) > 1
    return (previous if previous in available and FOLLOW_UP.match(text) else None), False
