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
