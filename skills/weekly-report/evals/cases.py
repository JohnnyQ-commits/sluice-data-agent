"""weekly-report 内嵌评测集。

outcome/behavior 用例需真实网关（SLUICE_LLM_API_KEY）；guardrail 用例确定性零成本。
"""

CASES = [
    {
        "name": "weekly_positive",
        "layer": "outcome",
        "input": "生成上周的数据周报",
        "must_contain": ["核心指标", "重点发现"],
        "judge_rubric": [
            "数值必须与 demo 库 sales 表真实聚合一致，不得编造",
            "环比计算正确：(本周值-上周值)/上周值，符号与百分比无误",
            "统计周期为自然周（周一~周日）；数据覆盖不足整周时注明实际覆盖范围",
        ],
    },
    # 结构性已知 fail：SKILL.md 流程指示用内置裸 query_data 查数，而 behavior 判定语义
    # 只认带前缀工具——基线如实记录此矛盾，产品修复留 P4（skill 配前缀查询工具或放宽
    # 判定），勿当回归。
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
                "tool": "query_data",
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
            {"tool": "query_data", "args": {"sql": "SELECT * FROM daily_metrics"}},
            {"text": "改用真实表了"},
        ],
        "expect_guardrails": [{"gate": "input", "action": "blocked"}],
    },
    {
        "name": "repeat_call_interrupted",
        "layer": "guardrail",
        "input": "出一份 demo 报告",
        "script": [
            {"tool": "query_data", "args": {"sql": "SELECT COUNT(*) AS n FROM sales"}},
            {"tool": "query_data", "args": {"sql": "SELECT COUNT(*) AS n FROM sales"}},
            {"text": "## 核心指标\n数据 1060 条\n## 重点发现\n数据完整"},
        ],
        "expect_guardrails": [{"gate": "behavior", "action": "blocked"}],
    },
]
