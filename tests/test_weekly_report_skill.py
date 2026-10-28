from pathlib import Path

from sluice.skills.loader import SkillLoader
from sluice.tools.registry import ToolRegistry

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _load():
    registry = ToolRegistry()
    SkillLoader().load(SKILLS_DIR, registry)
    return registry


def test_skill_package_loads_with_prefixed_tools():
    registry = _load()
    names = [p.name for p in SkillLoader().load(SKILLS_DIR, ToolRegistry())]
    assert "weekly-report" in names
    assert registry.get("weekly-report.render_report") is not None
    assert registry.get("weekly-report.query_sales") is not None


def test_query_sales_delegates_and_rejects_non_string(tmp_path):
    """前缀查询工具：SQL 执行委托 query_data 内核，非字符串 SQL 报错不执行（结构性 fail 修复件）。"""
    import sqlite3

    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sales (sale_date TEXT, city TEXT, category TEXT, amount REAL)")
    conn.execute("INSERT INTO sales VALUES ('2026-10-13', '杭州', '食品', 100.0)")
    conn.commit()
    conn.close()

    tool_cls = type(_load().get("weekly-report.query_sales"))
    tool = tool_cls(str(db))
    ok = tool.execute({"sql": "SELECT city, SUM(amount) FROM sales GROUP BY city"})
    assert not ok.is_error and "杭州" in ok.content
    bad = tool.execute({"sql": 123})
    assert bad.is_error and "sql" in bad.content.lower()


def test_query_sales_table_allowlist_static(tmp_path):
    """幻觉表门禁依赖 table_allowlist：前缀工具静态声明域内表，不随库内容漂移。

    备案④：早前转发内核 sqlite_master 全量名单，库里有 user_events 就能跨域读——
    表级隔离只对单侧（user-activity 侧）成立。收窄为静态 ["sales"] 后双向对称。
    """
    import sqlite3

    db = tmp_path / "allow.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sales (sale_date TEXT, city TEXT, category TEXT, amount REAL)")
    conn.execute("CREATE TABLE user_events (user_id TEXT, event_date TEXT, event_type TEXT)")
    conn.commit()
    conn.close()

    tool_cls = type(_load().get("weekly-report.query_sales"))
    tool = tool_cls(str(db))
    # 库里明明有 user_events，allowlist 仍只声明本域 sales——收窄与库内容无关
    assert tool.table_allowlist() == ["sales"]


def test_query_sales_cross_domain_blocked_by_gate(tmp_path):
    """表级隔离双向对称：weekly-report 引用 user_events 与 user-activity 引用 sales 同样被拦。"""
    from sluice.guardrails.input_gate import InputGate
    from sluice.llm.types import ToolUseBlock

    tool = _load().get("weekly-report.query_sales")
    gate = InputGate()
    verdict = gate.check(
        ToolUseBlock("t1", "weekly-report.query_sales", {"sql": "SELECT * FROM user_events"}),
        tool,
    )
    assert verdict is not None and "幻觉表" in verdict[0]


def test_render_report_produces_markdown():
    registry = _load()
    result = registry.get("weekly-report.render_report").execute(
        {
            "title": "电商销售周报",
            "period": "2026-10-13 ~ 2026-10-19",
            "metrics": [
                {"name": "总销售额", "value": "12.3万", "delta": "+15%"},
                {"name": "订单数", "value": "1,234", "delta": "-3%"},
            ],
            "highlights": ["数码品类环比大涨"],
        }
    )
    assert not result.is_error
    assert "# 电商销售周报" in result.content
    assert "总销售额" in result.content and "+15%" in result.content


def test_render_report_missing_required_field_is_error():
    registry = _load()
    result = registry.get("weekly-report.render_report").execute({"title": "只有标题"})
    assert result.is_error


def test_render_report_empty_metrics_goes_required_error():
    registry = _load()
    result = registry.get("weekly-report.render_report").execute(
        {"title": "周报", "period": "2026-10-12 ~ 2026-10-18", "metrics": []}
    )
    assert result.is_error


def test_render_report_metric_element_missing_key_is_error():
    registry = _load()
    result = registry.get("weekly-report.render_report").execute(
        {
            "title": "周报",
            "period": "2026-10-12 ~ 2026-10-18",
            "metrics": [{"name": "客单价"}],
        }
    )
    assert result.is_error
    assert "metrics 元素缺少 name/value" in result.content


def test_render_report_metrics_not_list_is_error():
    registry = _load()
    for bad in ("总销售额", 123, {"name": "总销售额", "value": "12.3万"}):
        result = registry.get("weekly-report.render_report").execute(
            {"title": "周报", "period": "2026-10-12 ~ 2026-10-18", "metrics": bad}
        )
        assert result.is_error, f"metrics={bad!r} 应报错"


def test_render_report_highlights_not_list_is_error():
    registry = _load()
    result = registry.get("weekly-report.render_report").execute(
        {
            "title": "周报",
            "period": "2026-10-12 ~ 2026-10-18",
            "metrics": [{"name": "总销售额", "value": "12.3万"}],
            "highlights": "数码品类环比大涨",
        }
    )
    assert result.is_error


def test_render_report_zero_delta_not_swallowed():
    registry = _load()
    result = registry.get("weekly-report.render_report").execute(
        {
            "title": "周报",
            "period": "2026-10-12 ~ 2026-10-18",
            "metrics": [{"name": "总销售额", "value": "12.3万", "delta": "0%"}],
        }
    )
    assert not result.is_error
    assert "（环比 0%）" in result.content
