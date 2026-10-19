"""tests/test_input_gate.py"""

import sqlite3
from typing import Any, ClassVar

from sluice.guardrails.input_gate import InputGate, extract_sql_tables, validate_schema
from sluice.llm.types import ToolUseBlock
from sluice.tools.base import Tool, ToolResult


class SqlTool(Tool):
    name = "stub"
    description = "stub"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"sql": {"type": "string"}},
        "required": ["sql"],
    }

    def execute(self, args):
        return ToolResult("ok")


def test_validate_schema_missing_required():
    assert "缺少必填参数: sql" in validate_schema({}, SqlTool.parameters)


def test_validate_schema_wrong_type():
    assert "类型应为 string" in validate_schema({"sql": 123}, SqlTool.parameters)


def test_validate_schema_bool_not_integer():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    assert "类型应为 integer" in validate_schema({"n": True}, schema)


def test_validate_schema_enum_violation():
    schema = {"type": "object", "properties": {"c": {"type": "string", "enum": ["a", "b"]}}}
    assert "不在允许范围" in validate_schema({"c": "z"}, schema)


def test_validate_schema_pass():
    assert validate_schema({"sql": "select 1"}, SqlTool.parameters) is None


def test_validate_schema_unknown_type_passes():
    """未声明/未知的 schema type 放行——门禁只拦确定性错误，不崩自己。"""
    schema = {"type": "object", "properties": {"x": {"type": "null"}}}
    assert validate_schema({"x": None}, schema) is None


def test_extract_sql_tables_basic():
    assert extract_sql_tables("SELECT * FROM sales s JOIN cities c ON s.c = c.id") == {
        "sales",
        "cities",
    }


def test_extract_sql_tables_cte_and_quoting():
    sql = 'WITH t AS (SELECT 1) SELECT * FROM `main`.`t` JOIN "dim_date"'
    assert extract_sql_tables(sql) == {"t", "dim_date"}


def test_extract_sql_tables_ignores_string_literals():
    assert extract_sql_tables("SELECT * FROM sales WHERE note LIKE '%from users%'") == {"sales"}


def _ghost_db(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("create table sales (id integer)")
    conn.commit()
    conn.close()
    return str(db)


def test_input_gate_blocks_hallucinated_tool():
    verdict = InputGate().check(ToolUseBlock("t1", "no_such_tool", {}), None)
    assert verdict is not None and "幻觉工具" in verdict[0] and verdict[1]["tool"] == "no_such_tool"


def test_input_gate_blocks_schema_violation():
    verdict = InputGate().check(ToolUseBlock("t1", "stub", {"sql": 1}), SqlTool())
    assert verdict is not None and "参数校验未通过" in verdict[0]


def test_input_gate_blocks_hallucinated_table(tmp_path):
    tool = SqlTool()
    tool.table_allowlist = lambda: ["sales"]  # 测试桩，模拟 QueryDataTool 的在册名单
    verdict = InputGate().check(ToolUseBlock("t1", "stub", {"sql": "select * from ghost"}), tool)
    assert verdict is not None and "幻觉表" in verdict[0]
    assert verdict[1]["unknown_tables"] == ["ghost"]


def test_input_gate_allows_cte_self_reference():
    tool = SqlTool()
    tool.table_allowlist = lambda: ["sales"]
    sql = "WITH t AS (SELECT id FROM sales) SELECT * FROM t"
    assert InputGate().check(ToolUseBlock("t1", "stub", {"sql": sql}), tool) is None


def test_input_gate_passes_valid_call():
    assert InputGate().check(ToolUseBlock("t1", "stub", {"sql": "select 1"}), SqlTool()) is None
