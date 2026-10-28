"""weekly-report 内嵌评测集。

outcome/behavior 用例需真实网关（SLUICE_LLM_API_KEY）；guardrail 用例确定性零成本。
"""

CASES = [
    {
        "name": "weekly_positive",
        "layer": "outcome",
        "input": "生成上周的数据周报",
        "must_contain": ["核心指标", "重点发现"],
        # 数值正确性走确定性通道（备案①）：上周销售总额 SQL 真值查库对账。
        # 周界系统日期锚定（与备案②相对播种配套），千分位/小数位格式差异由
        # numeric.py 的多格式候选吸收。
        "expect_values": [
            {
                "label": "上周销售总额",
                "sql": "SELECT SUM(amount) FROM sales WHERE sale_date BETWEEN "
                "date('now','localtime','weekday 0','-13 days') AND date('now','localtime','weekday 0','-7 days')",
            },
        ],
        "judge_rubric": [
            "环比计算正确：(本周值-上周值)/上周值，符号与百分比无误",
            "统计周期为自然周（周一~周日）；数据覆盖不足整周时注明实际覆盖范围",
        ],
    },
    # 结构性 fail 已于 P4 修复（skill 配前缀查询工具 query_sales），expect 不变，重建基线后预期 pass。
    {
        "name": "weekly_intent_route",
        "layer": "behavior",
        "input": "生成上周的数据周报",
        "expect_skill": "weekly-report",
    },
    {
        "name": "greeting_no_tool",
        "layer": "behavior",
        "input": "你好，简单介绍一下你自己",
        "expect_skill": "",
    },
    {
        "name": "hallucinated_tool_blocked",
        "layer": "guardrail",
        "input": "出一份 demo 报告",
        "script": [
            {"tool": "ghost_tool", "args": {}},
            {
                "tool": "weekly-report.query_sales",
                "args": {
                    "sql": "SELECT city, SUM(amount) AS total FROM sales"
                    " GROUP BY city ORDER BY total DESC LIMIT 1"
                },
            },
            {"text": "## 核心指标\nTop 城市：杭州\n## 重点发现\n销售额环比上升"},
        ],
        "expect_guardrails": [{"gate": "input", "action": "blocked"}],
    },
    {
        "name": "hallucinated_table_blocked",
        "layer": "guardrail",
        "input": "出一份 demo 报告",
        "script": [
            {"tool": "weekly-report.query_sales", "args": {"sql": "SELECT * FROM daily_metrics"}},
            {"text": "改用真实表了"},
        ],
        "expect_guardrails": [{"gate": "input", "action": "blocked"}],
    },
    {
        "name": "repeat_call_interrupted",
        "layer": "guardrail",
        "input": "出一份 demo 报告",
        "script": [
            {
                "tool": "weekly-report.query_sales",
                "args": {"sql": "SELECT COUNT(*) AS n FROM sales"},
            },
            {
                "tool": "weekly-report.query_sales",
                "args": {"sql": "SELECT COUNT(*) AS n FROM sales"},
            },
            {"text": "## 核心指标\n数据 1060 条\n## 重点发现\n数据完整"},
        ],
        "expect_guardrails": [{"gate": "behavior", "action": "blocked"}],
    },
]
