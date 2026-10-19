"""Skill ② credit-weekly 跨域信贷周报：注册/契约/表级权限测试（Task 7）。"""

from pathlib import Path

import pytest

from sluice.config.settings import Settings
from sluice.skills.loader import SkillLoader
from sluice.tools.registry import ToolRegistry

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@pytest.fixture
def credit_tool(tmp_path, monkeypatch):
    """临时 seed 一个只有 credit_applications 的库，注册 credit-weekly 包。"""
    import sqlite3

    db = tmp_path / "credit.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "create table credit_applications (id integer primary key, app_date text,"
        " city text, product text, apply_amount real, approved integer)"
    )
    conn.execute(
        "insert into credit_applications values (1, '2026-10-13', '杭州', '消费贷', 100000.0, 1)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        Settings, "model_config", {**Settings.model_config, "env_file": str(tmp_path / "none.env")}
    )
    registry = ToolRegistry()
    packages = SkillLoader().load(SKILLS_DIR, registry)
    pkg = next(p for p in packages if p.name == "credit-weekly")
    tool = pkg.tools[0]
    tool._db_path = str(db)  # 指向临时库
    tool._tables = None  # 清缓存，重新惰性加载
    return tool


def test_credit_skill_registered_with_prefix_and_contract(credit_tool):
    assert credit_tool.name == "credit-weekly.query_credit"
    assert credit_tool.owner_skill == "credit-weekly"


def test_credit_tool_queries_credit_table(credit_tool):
    result = credit_tool.execute({"sql": "SELECT COUNT(*) AS n FROM credit_applications"})
    assert result.is_error is False
    assert result.content.rstrip().endswith("| 1 |")  # 表格末行 = 单行计数 1


def test_credit_tool_errors_on_missing_table(credit_tool):
    """临时库里没有 sales 表 → SQL 报错 is_error=True（工具层兜底，非 allowlist 拦截路径）。"""
    result = credit_tool.execute({"sql": "SELECT * FROM sales"})
    assert result.is_error is True


def test_input_gate_blocks_credit_tool_cross_domain(credit_tool):
    """真正的表级拦截在 InputGate：credit SQL 引用 sales 应被 allowlist 门禁拒绝。"""
    from sluice.guardrails.input_gate import InputGate
    from sluice.llm.types import ToolUseBlock

    gate = InputGate()
    reason_detail = gate.check(
        ToolUseBlock("t1", "credit-weekly.query_credit", {"sql": "SELECT * FROM sales"}),
        credit_tool,
    )
    assert reason_detail is not None
    reason, detail = reason_detail
    assert reason.startswith("SQL 引用了不在册的表")
    assert detail["unknown_tables"] == ["sales"]


def test_credit_tool_allowlist_declared(credit_tool):
    assert credit_tool.table_allowlist() == ["credit_applications"]


def test_weekly_report_contract_declared():
    registry = ToolRegistry()
    packages = SkillLoader().load(SKILLS_DIR, registry)
    weekly = next(p for p in packages if p.name == "weekly-report")
    assert weekly.output_contract == ["核心指标", "重点发现"]
