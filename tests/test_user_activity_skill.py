"""Skill ② user-activity 跨域用户活跃周报：注册/契约/表级权限测试。"""

from pathlib import Path

import pytest

from sluice.config.settings import Settings
from sluice.skills.loader import SkillLoader
from sluice.tools.registry import ToolRegistry

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@pytest.fixture
def activity_tool(tmp_path, monkeypatch):
    """临时 seed 一个只有 user_events 的库，注册 user-activity 包。"""
    import sqlite3

    db = tmp_path / "activity.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "create table user_events (id integer primary key, user_id text,"
        " event_date text, event_type text)"
    )
    conn.execute("insert into user_events values (1, 'u001', '2026-10-13', 'login')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        Settings, "model_config", {**Settings.model_config, "env_file": str(tmp_path / "none.env")}
    )
    registry = ToolRegistry()
    packages = SkillLoader().load(SKILLS_DIR, registry)
    pkg = next(p for p in packages if p.name == "user-activity")
    tool = pkg.tools[0]
    tool._db_path = str(db)  # 指向临时库
    tool._tables = None  # 清缓存，重新惰性加载
    return tool


def test_activity_skill_registered_with_prefix_and_contract(activity_tool):
    assert activity_tool.name == "user-activity.query_events"
    assert activity_tool.owner_skill == "user-activity"


def test_activity_tool_queries_events_table(activity_tool):
    result = activity_tool.execute({"sql": "SELECT COUNT(*) AS n FROM user_events"})
    assert result.is_error is False
    assert result.content.rstrip().endswith("| 1 |")  # 表格末行 = 单行计数 1


def test_activity_tool_errors_on_missing_table(activity_tool):
    """临时库里没有 sales 表 → SQL 报错 is_error=True（工具层兜底，非 allowlist 拦截路径）。"""
    result = activity_tool.execute({"sql": "SELECT * FROM sales"})
    assert result.is_error is True


def test_input_gate_blocks_activity_tool_cross_domain(activity_tool):
    """真正的表级拦截在 InputGate：活跃度 SQL 引用 sales 应被 allowlist 门禁拒绝。"""
    from sluice.guardrails.input_gate import InputGate
    from sluice.llm.types import ToolUseBlock

    gate = InputGate()
    reason_detail = gate.check(
        ToolUseBlock("t1", "user-activity.query_events", {"sql": "SELECT * FROM sales"}),
        activity_tool,
    )
    assert reason_detail is not None
    reason, detail = reason_detail
    assert reason.startswith("SQL 引用了不在册的表")
    assert detail["unknown_tables"] == ["sales"]


def test_activity_tool_allowlist_declared(activity_tool):
    assert activity_tool.table_allowlist() == ["user_events"]


def test_activity_output_contract_declared():
    registry = ToolRegistry()
    packages = SkillLoader().load(SKILLS_DIR, registry)
    activity = next(p for p in packages if p.name == "user-activity")
    assert activity.output_contract == ["核心指标", "趋势分析", "重点发现"]


def test_weekly_report_contract_declared():
    registry = ToolRegistry()
    packages = SkillLoader().load(SKILLS_DIR, registry)
    weekly = next(p for p in packages if p.name == "weekly-report")
    assert weekly.output_contract == ["核心指标", "重点发现"]
