---
name: credit-weekly
description: 信贷业务周报：统计上周（自然周）贷款申请量、审批通过率、件均申请金额及环比变化，渲染为结构化周报
output_contract: 核心指标;重点发现
---

# 信贷周报 Skill

数据表：`credit_applications`（app_date / city / product / apply_amount / approved）。

## 流程

1. 用 `credit-weekly.query_credit` 探查表结构与日期范围（不要假设字段，先 SELECT LIMIT 3 看样例）
2. 两条 SQL 分别聚合上周与上上周（自然周，周一为一周开始）：申请量 COUNT(*)、审批通过率 SUM(approved)*1.0/COUNT(*)、件均申请金额（仅统计 approved=1）
3. 数据齐了直接给出最终回答，包含「核心指标」「重点发现」两个小节，环比口径：(本周-上周)/上周，上周为 0 时注明"不可比"

## 统计口径

- 自然周：Monday–Sunday，禁止用 MAX(app_date) 当"今天"锚点
- 通过率保留 1 位小数百分比；金额保留 2 位小数
- 日期覆盖不足 7 天时在报告中注明
