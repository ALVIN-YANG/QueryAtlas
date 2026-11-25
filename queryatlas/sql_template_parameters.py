"""Match declared question slots and bind their values to SQL expressions."""

import re
from datetime import date
from typing import Annotated, Literal

import sqlglot
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlglot import exp


NAME = r"[a-z][a-z0-9_]{0,31}"
SLOT = re.compile(r"\{(" + NAME + r")\}")
ENUM = re.compile(r"[\w .-]{1,64}")
COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


class Parameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["date", "integer", "enum"]
    label: str = Field(min_length=1, max_length=60)
    choices: tuple[str, ...] = Field(default=(), max_length=30)

    @model_validator(mode="after")
    def validate_choices(self):
        if self.type == "enum":
            if (
                not self.choices
                or any(not ENUM.fullmatch(v) or v != v.strip() for v in self.choices)
                or len({v.casefold() for v in self.choices}) != len(self.choices)
            ):
                raise ValueError("枚举参数须填写不重复的选项，支持文字、数字和常用分隔符")
        elif self.choices:
            raise ValueError("只有枚举参数支持选项")
        return self

    def parse(self, value):
        value = str(value).strip()
        if self.type == "date" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            try:
                return date.fromisoformat(value).isoformat()
            except ValueError:
                pass
        if self.type == "integer" and re.fullmatch(r"-?[0-9]{1,18}", value):
            return int(value)
        if self.type == "enum":
            for choice in self.choices:
                if value.casefold() == choice.casefold():
                    return choice
        hint = {"date": "YYYY-MM-DD", "integer": "整数", "enum": " / ".join(self.choices)}
        raise ValueError(f"请提供有效的{self.label}（{hint[self.type]}）。")

    def example(self):
        if self.type == "enum":
            return self.choices[0]
        return "2000-01-01" if self.type == "date" else 1


ParamName = Annotated[str, Field(pattern="^" + NAME + "$")]
Parameters = Annotated[dict[ParamName, Parameter], Field(max_length=3)]


def normalize(question):
    return " ".join(question.split()).rstrip("。？！.!?").rstrip().casefold()


def alias_pattern(alias, parameters):
    parts = []
    names = set()
    offset = 0
    for slot in SLOT.finditer(alias):
        literal = alias[offset : slot.start()]
        name = slot[1]
        if name not in parameters or name in names:
            raise ValueError("问句占位符须声明且每个参数只能出现一次")
        if names and not literal.strip():
            raise ValueError("相邻参数之间须有明确分隔文字")
        names.add(name)
        parts.extend((re.escape(literal), f"(?P<{name}>.{{0,80}}?)"))
        offset = slot.end()
    remainder = alias[offset:]
    if "{" in SLOT.sub("", alias) or "}" in SLOT.sub("", alias):
        raise ValueError("问句占位符须使用 {参数编号}")
    if names and not SLOT.sub("", alias).strip():
        raise ValueError("参数化问句须包含固定文字")
    return re.compile("".join(parts) + re.escape(remainder))


def parse_values(raw, parameters):
    if set(raw) - set(parameters):
        raise ValueError("提交了模板未声明的参数")
    missing = [p.label for name, p in parameters.items() if raw.get(name) in ("", None)]
    if missing:
        raise ValueError("请补充" + "、".join(missing) + "。")
    return {name: p.parse(raw[name]) for name, p in parameters.items()}


def bind_sql(sql, parameters, raw):
    values = parse_values(raw, parameters)
    if not values:
        return sql, {}
    try:
        trees = sqlglot.parse(sql, read="starrocks")
    except sqlglot.errors.SqlglotError as exc:
        raise ValueError("参数化 SQL 无法解析") from exc
    if len(trees) != 1 or not isinstance(trees[0], exp.Select):
        raise ValueError("参数化 SQL 必须是一条 SELECT")
    root = trees[0]
    slots = list(root.find_all(exp.Placeholder))
    if {p.name for p in slots} != set(parameters):
        raise ValueError("SQL 的 :参数编号 必须与模板声明一致")
    for slot in slots:
        if (
            not isinstance(slot.parent, COMPARISONS)
            or slot.arg_key != "expression"
            or not isinstance(slot.parent.this, exp.Column)
        ):
            raise ValueError("参数只能作为列比较条件右侧的值")
        slot.replace(exp.convert(values[slot.name]))
    return root.sql(dialect="starrocks"), values
