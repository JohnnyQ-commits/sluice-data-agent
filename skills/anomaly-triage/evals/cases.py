"""anomaly-triage 内嵌评测集。outcome/behavior 需真实网关；guardrail 确定性零成本。

教学数据：播种在 demo 库上周埋了已知渠道异常（整体上量但深圳暴跌，见
scripts/seed_demo_db.py）。expect_values 对异常切片的真值做确定性对账——下钻
定位结论必须含异常城市上周销售额与其跌幅，定位错了数字就对不上（备案①同款）。
"""

CASES = [
    {
        "name": "anomaly_triage_positive",
        "layer": "outcome",
        "input": "上周销售额整体环比涨了不少，但我总觉得哪里不对劲，帮我下钻定位一下异常",
        "must_contain": ["下钻结论", "证据链"],
        # 数值正确性走确定性通道（备案①）：异常切片的真值查库对账——定位错了
        # （没找到深圳暴跌或写错幅度）数字就对不上。
        "expect_values": [
            {
                "label": "异常城市上周销售额",
                "sql": "SELECT SUM(amount) FROM sales WHERE city='深圳' AND sale_date BETWEEN "
                "date('now','localtime','weekday 0','-13 days') AND date('now','localtime','weekday 0','-7 days')",
            },
            {
                # 跌幅不 ROUND：匹配器的多格式候选（1/2 位小数）吸收模型任一精度写法
                # （run 实教训：真值 ROUND(,1)=194.6 而模型写未取整 194.57，词边界不命中）。
                # 子查询两窗口各取真值再相除——SUM 套 SUM 是语法错误（实录教训）。
                "label": "异常城市销售额跌幅",
                "sql": "SELECT (p.v - c.v) * 100.0 / p.v FROM "
                "(SELECT SUM(amount) AS v FROM sales WHERE city='深圳' AND sale_date BETWEEN "
                "date('now','localtime','weekday 0','-20 days') AND date('now','localtime','weekday 0','-14 days')) p, "
                "(SELECT SUM(amount) AS v FROM sales WHERE city='深圳' AND sale_date BETWEEN "
                "date('now','localtime','weekday 0','-13 days') AND date('now','localtime','weekday 0','-7 days')) c",
            },
        ],
        "judge_rubric": [
            # 周界锚定与整体口径由 expect_values 确定性通道兜底，judge 评定位质量。
            "定位到深圳销售额反常下跌（其余城市环比为正），而非只复述整体上涨",
            "证据链从整体到嫌疑切片逐层收窄，每层附查库数字；未把偶发单日抖动误判为持续异常",
        ],
    },
    {
        "name": "anomaly_triage_intent_route",
        "layer": "behavior",
        "input": "销售额好像有点不对劲，帮我下钻看看异常出在哪",
        "expect_skill": "anomaly-triage",
    },
    {
        "name": "anomaly_triage_ghost_table_blocked",
        "layer": "guardrail",
        "input": "下钻查一下上周的异常",
        "script": [
            {"tool": "anomaly-triage.query_data", "args": {"sql": "SELECT * FROM anomaly_log"}},
            {"text": "## 下钻结论\n未能定位\n## 证据链\n| 下钻层级 | 发现 | 数值佐证 |"},
        ],
        "expect_guardrails": [{"gate": "input", "action": "blocked"}],
    },
]
