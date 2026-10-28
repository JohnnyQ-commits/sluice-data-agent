"""fact-check（报告验真）Skill 测试：确定性盖章、审计员双域 allowlist、门禁交互。"""

import importlib.util
import sqlite3
from pathlib import Path

from sluice.guardrails.input_gate import InputGate
from sluice.llm.types import ToolUseBlock
from sluice.skills.loader import SkillLoader
from sluice.tools.registry import ToolRegistry

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _load():
    registry = ToolRegistry()
    SkillLoader().load(SKILLS_DIR, registry)
    return registry


# ---------- 包加载 ----------


def test_skill_package_loads_with_prefixed_tools():
    registry = _load()
    names = [p.name for p in SkillLoader().load(SKILLS_DIR, ToolRegistry())]
    assert "fact-check" in names
    assert registry.get("fact-check.query_data") is not None
    assert registry.get("fact-check.render_verdict") is not None


# ---------- 审计员双域 allowlist（有意放宽，与周报 Skill 的单域收窄对照）----------


def test_query_data_allowlist_covers_both_domains():
    """审计员读权覆盖 sales + user_events 两域——验真天然跨域对账，静态声明。"""
    tool = _load().get("fact-check.query_data")
    assert tool.table_allowlist() == ["sales", "user_events"]


def test_query_data_cross_domain_allowed_by_gate(tmp_path):
    """与 weekly-report 相反：fact-check 读两域任一表都放行（角色决定名单）。"""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE user_events (user_id TEXT, event_date TEXT, event_type TEXT)")
    conn.commit()
    conn.close()

    tool_cls = type(_load().get("fact-check.query_data"))
    tool = tool_cls(str(db))
    gate = InputGate()
    verdict = gate.check(
        ToolUseBlock("t1", "fact-check.query_data", {"sql": "SELECT * FROM user_events"}),
        tool,
    )
    assert verdict is None  # 未拦截


def test_query_data_ghost_table_blocked_by_gate():
    tool = _load().get("fact-check.query_data")
    gate = InputGate()
    verdict = gate.check(
        ToolUseBlock("t1", "fact-check.query_data", {"sql": "SELECT * FROM ghost_metrics"}),
        tool,
    )
    assert verdict is not None and "幻觉表" in verdict[0]


def test_query_data_delegates_and_rejects_non_string(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sales (sale_date TEXT, city TEXT, amount REAL)")
    conn.execute("INSERT INTO sales VALUES ('2026-10-13', '杭州', 100.0)")
    conn.commit()
    conn.close()

    tool_cls = type(_load().get("fact-check.query_data"))
    tool = tool_cls(str(db))
    ok = tool.execute({"sql": "SELECT city, SUM(amount) FROM sales GROUP BY city"})
    assert not ok.is_error and "杭州" in ok.content
    bad = tool.execute({"sql": 123})
    assert bad.is_error and "sql" in bad.content.lower()


# ---------- render_verdict 确定性盖章 ----------


def _verdict_tool():
    return _load().get("fact-check.render_verdict")


def test_render_verdict_stamps_ok_bad_uncertain():
    """属实/不符/存疑三态由代码判定：词边界匹配（含千分位/万缩写变体）+ 真值缺失存疑。"""
    result = _verdict_tool().execute(
        {
            "title": "周报验真",
            "summary": "两条属实一条不符一条存疑",
            "items": [
                {"claim": "总销售额", "claimed": "总销售额 8,042.34 元", "db_value": "8042.34"},
                {"claim": "销售额（万缩写）", "claimed": "21.3万", "db_value": "213177.33"},
                {"claim": "订单数", "claimed": "订单数 999", "db_value": "1060"},
                {"claim": "覆盖范围", "claimed": "上周数据", "db_value": "", "note": "窗口无数据"},
            ],
        }
    )
    assert not result.is_error
    text = result.content
    assert "# 周报验真" in text
    assert "## 验真结论" in text and "## 对账明细" in text
    assert "✅ 属实 2 项 | ❌ 不符 1 项 | ⚠️ 存疑 1 项" in text
    assert "（窗口无数据）" in text
    assert "确定性代码盖章，非模型判断" in text


def test_render_verdict_non_numeric_db_value_is_uncertain():
    result = _verdict_tool().execute(
        {
            "title": "验真",
            "summary": "真值非数值",
            "items": [{"claim": " city", "claimed": "top city 杭州", "db_value": "杭州"}],
        }
    )
    assert not result.is_error
    assert "⚠️ 存疑 1 项" in result.content


def test_render_verdict_db_value_with_thousands_separator():
    """真值带千分位逗号（模型从查询结果转抄）不再误判存疑——实录教训：float 解析失败全批存疑。"""
    result = _verdict_tool().execute(
        {
            "title": "验真",
            "summary": "ok",
            "items": [
                # 真值 213177.33（传入 "213,177.33"），声称 21.3万 → 属实
                {"claim": "销售额", "claimed": "21.3万", "db_value": "213,177.33"},
                # 真值同上，声称 277,130（篡改值）→ 不符
                {"claim": "销售额-篡改", "claimed": "277,130", "db_value": "213,177.33"},
            ],
        }
    )
    assert "✅ 属实 1 项 | ❌ 不符 1 项 | ⚠️ 存疑 0 项" in result.content


def test_render_verdict_db_value_with_unit_text():
    """真值转抄带单位（"47 人"）正则抽取数值，不再误判存疑——实录教训 run4。"""
    result = _verdict_tool().execute(
        {
            "title": "验真",
            "summary": "ok",
            "items": [
                {"claim": "新增用户", "claimed": "上周新增用户 47 人", "db_value": "47 人"},
                {"claim": "销售额", "claimed": "21.3万", "db_value": "213,177.33 元"},
                {"claim": "无数据", "claimed": "上周数据", "db_value": "查询结果为空", "note": "窗口无数据"},
            ],
        }
    )
    assert "✅ 属实 2 项 | ❌ 不符 0 项 | ⚠️ 存疑 1 项" in result.content


def test_render_verdict_int_value_accepts_decimal_claim():
    """真值整值 311，声称写 311.0（反向宽容）也判属实。"""
    result = _verdict_tool().execute(
        {
            "title": "验真",
            "summary": "ok",
            "items": [{"claim": "WAU", "claimed": "WAU：311.0", "db_value": "311"}],
        }
    )
    assert "✅ 属实 1 项" in result.content


def test_render_verdict_word_boundary_no_substring_hit():
    """真值 311 不被声称 1311 误判属实（词边界，与 evals 数值通道同一语义）。"""
    result = _verdict_tool().execute(
        {
            "title": "验真",
            "summary": "bad",
            "items": [{"claim": "WAU", "claimed": "WAU 1311", "db_value": "311"}],
        }
    )
    assert "❌ 不符 1 项" in result.content


# ---------- render_verdict 参数校验 ----------


def test_render_verdict_empty_items_is_error():
    result = _verdict_tool().execute({"title": "验真", "summary": "s", "items": []})
    assert result.is_error


def test_render_verdict_missing_claimed_is_error():
    """缺 claimed 直接报错——声称必须逐字摘录，省略就没法对账。"""
    result = _verdict_tool().execute(
        {"title": "验真", "summary": "s", "items": [{"claim": "总额", "db_value": "300"}]}
    )
    assert result.is_error and "claimed" in result.content


def test_render_verdict_item_missing_claim_is_error():
    result = _verdict_tool().execute(
        {"title": "验真", "summary": "s", "items": [{"claimed": "300", "db_value": "300"}]}
    )
    assert result.is_error and "claim" in result.content


def test_render_verdict_missing_title_or_summary_is_error():
    tool = _verdict_tool()
    assert tool.execute(
        {"summary": "s", "items": [{"claim": "c", "claimed": "1", "db_value": "1"}]}
    ).is_error
    assert tool.execute(
        {"title": "t", "items": [{"claim": "c", "claimed": "1", "db_value": "1"}]}
    ).is_error


# ---------- 内嵌评测集 ----------


def test_eval_cases_negative_report_differs_from_positive():
    """负向用例的篡改幅度必须让报告总额与真值在词边界下互不命中——否则负向 eval 失去意义。"""

    from sluice.utils.numeric_match import check_value_in_answer

    spec = importlib.util.spec_from_file_location(
        "fact_check_cases", SKILLS_DIR / "fact-check" / "evals" / "cases.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    names = {c["name"] for c in module.CASES}
    assert {
        "fact_check_positive",
        "fact_check_negative",
        "fact_check_intent_route",
        "fact_check_ghost_table_blocked",
    } <= names
    pos = next(c for c in module.CASES if c["name"] == "fact_check_positive")
    neg = next(c for c in module.CASES if c["name"] == "fact_check_negative")
    assert "属实" in pos["must_contain"] and "不符" in neg["must_contain"]
    # 正向报告里的总额声称必须命中真值（否则正向必挂）；负向报告的篡改值不得命中真值
    assert check_value_in_answer(module._TRUE["total"], module._POSITIVE_REPORT)
    assert not check_value_in_answer(module._TRUE["total"], module._NEGATIVE_REPORT)
    # 新增用户数两份报告都为真值原样——负向用例只篡改一条
    assert check_value_in_answer(module._TRUE["new_users"], module._NEGATIVE_REPORT)
