"""输入门禁（拦截点①，工具调用前）：幻觉工具拦截 / 参数 schema 校验 / SQL 表名在册校验。"""

from __future__ import annotations

import re

from ..llm.types import ToolUseBlock
from ..tools.base import Tool

_SQL_TABLE_RE = re.compile(r"\b(?:from|join)\s+[`\"']?([a-zA-Z_][\w.`\"']*)", re.IGNORECASE)
_CTE_RE = re.compile(r"\b(\w+)\s+as\s*\(", re.IGNORECASE)

_TYPE_CHECKS = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def validate_schema(args: dict, parameters: dict) -> str | None:
    """极简 JSON Schema 校验（平台用到的子集：type/required/properties/enum）。

    返回 None 放行；否则返回可直接回喂模型的自纠提示。
    未声明的参数放过，由工具自行处理——门禁只拦确定性错误。
    """
    if not isinstance(args, dict):
        return f"参数必须是对象，收到 {type(args).__name__}"
    props = parameters.get("properties", {})
    for req in parameters.get("required", []):
        if req not in args:
            return f"缺少必填参数: {req}"
    for key, value in args.items():
        spec = props.get(key)
        if spec is None:
            continue
        expected = spec.get("type")
        if expected:
            checker = _TYPE_CHECKS.get(expected)
            if checker is not None:
                # bool 是 int 子类，数字类型须显式排除
                ok = isinstance(value, checker) and not (
                    isinstance(value, bool) and expected in ("integer", "number")
                )
                if not ok:
                    return f"参数 {key} 类型应为 {expected}，收到 {type(value).__name__}"
            # 未知 type 视为放行——门禁只拦确定性错误，不崩自己
        if "enum" in spec and value not in spec["enum"]:
            return f"参数 {key} 取值 {value!r} 不在允许范围 {spec['enum']}"
    return None


def extract_sql_tables(sql: str) -> set[str]:
    """提取 SQL 引用的表名（FROM/JOIN 后标识符，去引号与 schema/db 前缀）。

    已知局限：FROM a, b 逗号多表只捕首表（宁放方向，不为此加正则复杂度）。
    返回集含 CTE 别名并集，仅供独立使用；InputGate 比对前会扣除 CTE 别名。
    CTE 别名（``x as (``）一并计入——WITH 自引用是合法读法，不能误报幻觉表。
    方向性设计：宁可多放（别名误入白名单），不可错拦合法查询。
    """
    sql = re.sub(r"'[^']*'", "''", sql)  # 剥字符串字面量，防 LIKE '%from x%' 误拦
    tables = set()
    for raw in _SQL_TABLE_RE.findall(sql):
        name = raw.replace("`", "").replace('"', "").replace("'", "")
        tables.add(name.split(".")[-1].lower())
    aliases = cte_aliases(sql)
    return tables | aliases


def cte_aliases(sql: str) -> set[str]:
    """SQL 体内定义的 CTE 别名（``WITH x as (``）——合法自引用，幻觉校验时应扣除。"""
    return {m.lower() for m in _CTE_RE.findall(sql)}


class InputGate:
    """check 返回 None 放行；(reason, detail) 表示拦截，reason 可直接回喂模型。"""

    def check(self, tu: ToolUseBlock, tool: Tool | None) -> tuple[str, dict] | None:
        if tool is None:
            return (f"工具 {tu.name} 不在注册表中（幻觉工具），调用被拦截", {"tool": tu.name})
        reason = validate_schema(tu.input, tool.parameters)
        if reason:
            return (f"参数校验未通过：{reason}", {"tool": tu.name, "args": tu.input})
        allowlist = getattr(tool, "table_allowlist", None)
        if callable(allowlist):
            sql = tu.input.get("sql", "") if isinstance(tu.input, dict) else ""
            referenced = extract_sql_tables(sql) - cte_aliases(sql)
            unknown = referenced - {t.lower() for t in allowlist()}
            if unknown:
                return (
                    f"SQL 引用了不在册的表 {sorted(unknown)}（幻觉表），调用被拦截",
                    {"tool": tu.name, "unknown_tables": sorted(unknown)},
                )
        return None
