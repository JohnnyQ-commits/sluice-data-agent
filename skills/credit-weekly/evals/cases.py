"""credit-weekly 内嵌评测集。outcome/behavior 需真实网关；guardrail 确定性零成本。"""

CASES = [
    {
        "name": "credit_positive",
        "layer": "outcome",
        "input": "生成上周的信贷周报",
        "must_contain": ["核心指标", "重点发现"],
        "judge_rubric": [
            "数值必须与 demo 库 credit_applications 表真实聚合一致（申请量/通过率/件均），不得编造",
            "通过率保留 1 位小数百分比；件均金额保留 2 位小数",
            "统计周期为自然周（周一~周日），禁止用 MAX(app_date) 锚点；覆盖不足时注明",
        ],
    },
    {
        "name": "credit_intent_route",
        "layer": "behavior",
        "input": "生成上周的信贷周报",
        "expect_skill": "credit-weekly",
    },
    {
        "name": "credit_cross_domain_blocked",
        "layer": "guardrail",
        "input": "出一份信贷 demo 报告",
        "script": [
            {"tool": "credit-weekly.query_credit", "args": {"sql": "SELECT * FROM sales"}},
            {"text": "## 核心指标\n申请量 153 件\n## 重点发现\n通过率 66%"},
        ],
        "expect_guardrails": [{"gate": "input", "action": "blocked"}],
    },
]
