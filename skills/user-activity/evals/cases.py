"""user-activity 内嵌评测集。outcome/behavior 需真实网关；guardrail 确定性零成本。"""

CASES = [
    {
        "name": "user_activity_positive",
        "layer": "outcome",
        "input": "生成上周的用户活跃周报",
        "must_contain": ["核心指标", "趋势分析", "重点发现"],
        # 数值正确性走确定性通道（备案①）：SQL 真值查库对账，judge 骗不过查库。
        # 周界用系统日期锚定（weekday 0 = 本周日，-7/-13 = 上周日/上周一），
        # 与 demo 库相对播种窗口（备案②）配套，用例无保质期。
        "expect_values": [
            {
                "label": "上周周活跃用户数(WAU)",
                "sql": "SELECT COUNT(DISTINCT user_id) FROM user_events "
                "WHERE event_type='login' AND event_date BETWEEN "
                "date('now','localtime','weekday 0','-13 days') AND date('now','localtime','weekday 0','-7 days')",
            },
            {
                "label": "上周新增用户数",
                "sql": "SELECT COUNT(*) FROM (SELECT user_id FROM user_events "
                "GROUP BY user_id HAVING MIN(event_date) BETWEEN "
                "date('now','localtime','weekday 0','-13 days') AND date('now','localtime','weekday 0','-7 days'))",
            },
        ],
        "judge_rubric": [
            "DAU 口径为当日去重 login 用户数，新增口径为当日首次出现用户数；环比与日均 DAU 保留 1 位小数",
            "统计周期为自然周（周一~周日）；覆盖不足时注明",
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
