"""user-activity 内嵌评测集。outcome/behavior 需真实网关；guardrail 确定性零成本。"""

CASES = [
    {
        "name": "user_activity_positive",
        "layer": "outcome",
        "input": "生成上周的用户活跃周报",
        "must_contain": ["核心指标", "趋势分析", "重点发现"],
        "judge_rubric": [
            "数值必须与 demo 库 user_events 表真实聚合一致（日均 DAU/周新增用户数/周环比），不得编造",
            "DAU 口径为当日去重 login 用户数，新增口径为当日首次出现用户数；环比与日均 DAU 保留 1 位小数",
            "统计周期为自然周（周一~周日），禁止用 MAX(event_date) 锚点；覆盖不足时注明",
        ],
    },
    {
        "name": "user_activity_intent_route",
        "layer": "behavior",
        "input": "生成上周的用户活跃周报",
        "expect_skill": "user-activity",
    },
    {
        "name": "user_activity_cross_domain_blocked",
        "layer": "guardrail",
        "input": "出一份用户活跃 demo 报告",
        "script": [
            {"tool": "user-activity.query_events", "args": {"sql": "SELECT * FROM sales"}},
            {
                "text": "## 核心指标\n日均 DAU 210 人\n## 趋势分析\n周末活跃回落\n## 重点发现\n活跃稳中有升"
            },
        ],
        "expect_guardrails": [{"gate": "input", "action": "blocked"}],
    },
]
