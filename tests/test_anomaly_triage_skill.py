"""anomaly-triage（异常下钻定位）Skill 测试：单域 allowlist、证据链结构校验、种子异常形态。"""

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
    assert "anomaly-triage" in names
    assert registry.get("anomaly-triage.query_data") is not None
    assert registry.get("anomaly-triage.render_triage") is not None


# ---------- 单域 allowlist（与 weekly-report 同规格，备案④）----------


def test_query_data_allowlist_single_domain():
    tool = _load().get("anomaly-triage.query_data")
    assert tool.table_allowlist() == ["sales"]


def test_query_data_cross_domain_blocked_by_gate(tmp_path):
    """anomaly-triage 引用 user_events 与 weekly-report 引用 user_events 同样被拦（域对称）。"""
    tool = _load().get("anomaly-triage.query_data")
    gate = InputGate()
    verdict = gate.check(
        ToolUseBlock("t1", "anomaly-triage.query_data", {"sql": "SELECT * FROM user_events"}),
        tool,
    )
    assert verdict is not None and "幻觉表" in verdict[0]


def test_query_data_ghost_table_blocked_by_gate():
    tool = _load().get("anomaly-triage.query_data")
    gate = InputGate()
    verdict = gate.check(
        ToolUseBlock("t1", "anomaly-triage.query_data", {"sql": "SELECT * FROM anomaly_log"}),
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

    tool_cls = type(_load().get("anomaly-triage.query_data"))
    tool = tool_cls(str(db))
    ok = tool.execute({"sql": "SELECT city, SUM(amount) FROM sales GROUP BY city"})
    assert not ok.is_error and "杭州" in ok.content
    bad = tool.execute({"sql": 123})
    assert bad.is_error and "sql" in bad.content.lower()


# ---------- render_triage 证据链结构校验 ----------


def _triage_tool():
    return _load().get("anomaly-triage.render_triage")


def test_render_triage_renders_conclusion_and_evidence_chain():
    result = _triage_tool().execute(
        {
            "title": "上周销售额异常下钻",
            "conclusion": "深圳销售额环比 -42.7%，为唯一下跌城市，异常定位到深圳",
            "findings": [
                {"step": "整体", "finding": "上周总额 200241.8，环比 +32.0%", "evidence": "+32.0"},
                {
                    "step": "按城市",
                    "finding": "深圳 -42.7%，其余四城 +28.6%~+81.0%",
                    "evidence": "-42.7",
                },
                {
                    "step": "按日期",
                    "finding": "深圳上周每日均低于上上周同期，持续暴跌非单日抖动",
                    "evidence": "7 天全部低于同期",
                },
            ],
        }
    )
    assert not result.is_error
    text = result.content
    assert "# 上周销售额异常下钻" in text
    assert "## 下钻结论" in text and "## 证据链" in text
    assert "深圳" in text and "| 整体 |" in text


def test_render_triage_finding_without_numeric_evidence_is_error():
    """证据链无数值佐证直接报错打回——结论必须挂数字，纯文字猜测进不了报告（SKILL 判定纪律）。"""
    result = _triage_tool().execute(
        {
            "title": "下钻",
            "conclusion": "可能有异常",
            "findings": [{"step": "整体", "finding": "看起来不太对", "evidence": "凭经验判断"}],
        }
    )
    assert result.is_error and "数值佐证" in result.content


def test_render_triage_empty_findings_is_error():
    result = _triage_tool().execute({"title": "下钻", "conclusion": "c", "findings": []})
    assert result.is_error


def test_render_triage_finding_missing_step_is_error():
    result = _triage_tool().execute(
        {
            "title": "下钻",
            "conclusion": "c",
            "findings": [{"finding": "f", "evidence": "1.0"}],
        }
    )
    assert result.is_error and "step" in result.content


def test_render_triage_missing_conclusion_is_error():
    result = _triage_tool().execute(
        {
            "title": "下钻",
            "findings": [{"step": "s", "finding": "f", "evidence": "1.0"}],
        }
    )
    assert result.is_error


# ---------- 种子异常形态（Skill B 的教学数据契约）----------


def test_seed_contains_city_anomaly_last_week():
    """播种契约：上周整体上量但深圳暴跌、其余城市为正——下钻评测的真值前提。

    用相对窗口（weekday 0 锚定）对真实 data/demo.db 复算（CI 有 seed 步骤保证存在，
    相对播种备案②配套：无论何时播种，形态恒成立）。
    """
    repo_db = SKILLS_DIR.parent / "data" / "demo.db"
    if not repo_db.exists():
        return  # 本地未播种时跳过形态断言
    conn = sqlite3.connect(f"file:{repo_db}?mode=ro", uri=True)
    wk = "BETWEEN date('now','localtime','weekday 0','-13 days') AND date('now','localtime','weekday 0','-7 days')"
    pw = "BETWEEN date('now','localtime','weekday 0','-20 days') AND date('now','localtime','weekday 0','-14 days')"

    def ratio(city):
        a = conn.execute(
            f"SELECT SUM(amount) FROM sales WHERE city=? AND sale_date {wk}", (city,)
        ).fetchone()[0]
        b = conn.execute(
            f"SELECT SUM(amount) FROM sales WHERE city=? AND sale_date {pw}", (city,)
        ).fetchone()[0]
        return (a - b) * 100.0 / b

    overall = conn.execute(f"SELECT SUM(amount) FROM sales WHERE sale_date {wk}").fetchone()[0]
    prev = conn.execute(f"SELECT SUM(amount) FROM sales WHERE sale_date {pw}").fetchone()[0]
    ratios = {c: ratio(c) for c in ("深圳", "北京", "上海", "广州", "杭州")}
    conn.close()
    assert overall > prev  # 整体向好（掩盖效应的前提）
    assert ratios["深圳"] < -20  # 深圳暴跌
    for city in ("北京", "上海", "广州", "杭州"):
        assert ratios[city] > 0, f"{city} 应为正环比"
