"""fact-check 内嵌评测集。outcome/behavior 需真实网关；guardrail 确定性零成本。

正向/负向用例的报告在模块加载时按 demo 库真值动态构建（备案②相对播种配套，
用例无保质期）：正向全真值，负向把销售总额篡改为真值的 1.3 倍——模型若如实查库
盖章，必须给出「不符」；这就是负向 eval 的意义：验真 Skill 不能只会说「属实」。
"""

import sqlite3


def _load_true_numbers() -> dict | None:
    """加载时查库取真值构建报告；查不到（loader 测试 tmp_path 等）回退静态值。"""
    try:
        from sluice.config.settings import Settings

        conn = sqlite3.connect(f"file:{Settings().demo_db_path}?mode=ro", uri=True)
        try:
            total = conn.execute(
                "SELECT SUM(amount) FROM sales WHERE sale_date BETWEEN "
                "date('now','localtime','weekday 0','-13 days') "
                "AND date('now','localtime','weekday 0','-7 days')"
            ).fetchone()[0]
            new_users = conn.execute(
                "SELECT COUNT(*) FROM (SELECT user_id FROM user_events GROUP BY user_id "
                "HAVING MIN(event_date) BETWEEN "
                "date('now','localtime','weekday 0','-13 days') "
                "AND date('now','localtime','weekday 0','-7 days'))"
            ).fetchone()[0]
        finally:
            conn.close()
        if total is None:
            return None
        return {"total": float(total), "new_users": int(new_users)}
    except (ImportError, sqlite3.Error, TypeError, ValueError):
        return None


_TRUE = _load_true_numbers() or {"total": 300.0, "new_users": 5}


def _report(total_text: str) -> str:
    return (
        "【上周经营周报】\n"
        f"上周销售总额 {total_text} 元，环比上升。\n"
        f"上周新增用户 {_TRUE['new_users']} 人，活跃平稳。\n"
        "整体经营向好。"
    )


_POSITIVE_REPORT = _report(f"{_TRUE['total']:,.2f}")
# 篡改幅度取 1.3 倍整值：与真值的各格式候选（整值/小数/万缩写）在词边界下互不命中
_INFLATED = int(_TRUE["total"] * 1.3) or 999
_NEGATIVE_REPORT = _report(f"{_INFLATED:,}")

CASES = [
    {
        "name": "fact_check_positive",
        "layer": "outcome",
        "input": f"帮我核实下面这份周报的数据是否属实：\n\n{_POSITIVE_REPORT}",
        "must_contain": ["验真结论", "对账明细", "属实"],
        # 数值正确性走确定性通道（备案①）：验真的两条真值 SQL 与报告构建同源。
        "expect_values": [
            {
                "label": "上周销售总额",
                "sql": "SELECT SUM(amount) FROM sales WHERE sale_date BETWEEN "
                "date('now','localtime','weekday 0','-13 days') "
                "AND date('now','localtime','weekday 0','-7 days')",
            },
            {
                "label": "上周新增用户数",
                "sql": "SELECT COUNT(*) FROM (SELECT user_id FROM user_events GROUP BY user_id "
                "HAVING MIN(event_date) BETWEEN "
                "date('now','localtime','weekday 0','-13 days') "
                "AND date('now','localtime','weekday 0','-7 days'))",
            },
        ],
        "judge_rubric": [
            "属实/不符结论由盖章工具依据查库真值给出，模型未自行改写判定结果",
            "对账明细逐条列出报告声称原文（含数字）与数据库真值；真值缺失的条目标存疑而非编造",
        ],
    },
    {
        "name": "fact_check_negative",
        "layer": "outcome",
        "input": f"帮我核实下面这份周报的数据是否属实：\n\n{_NEGATIVE_REPORT}",
        "must_contain": ["验真结论", "对账明细", "不符"],
        "judge_rubric": [
            "销售总额一条必须判「不符」（报告数字与库中真值对不上），不得为迎合报告改判属实",
            "新增用户数一条与真值一致，判「属实」",
        ],
    },
    {
        "name": "fact_check_intent_route",
        "layer": "behavior",
        "input": "帮我核实一下这份周报里的数字对不对",
        "expect_skill": "fact-check",
    },
    {
        "name": "fact_check_ghost_table_blocked",
        "layer": "guardrail",
        "input": "核实这份报告：上周销售额 1 万元",
        "script": [
            {"tool": "fact-check.query_data", "args": {"sql": "SELECT * FROM ghost_metrics"}},
            {"text": "## 验真结论\n存疑\n## 对账明细\n| 核查项 | 报告声称 | 数据库真值 | 结论 |"},
        ],
        "expect_guardrails": [{"gate": "input", "action": "blocked"}],
    },
]
