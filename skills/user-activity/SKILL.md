---
name: user-activity
description: 用户活跃周报：统计上周（自然周）日活跃用户数（DAU）、新增用户数、周环比变化及活跃趋势，渲染为结构化周报
output_contract: 核心指标;趋势分析;重点发现
---

# 用户活跃周报 Skill

数据表：`user_events`（user_id / event_date / event_type，event_type 取值 'login'/'view'/'action'）。

## 流程

1. 用 `user-activity.query_events` 探查表结构与日期范围（不要假设字段，先 SELECT LIMIT 3 看样例）
2. 两条 SQL 分别聚合上周与上上周（自然周，周一为一周开始）：日均 DAU、周新增用户数、周环比
3. 数据齐了直接给出最终回答，包含「核心指标」「趋势分析」「重点发现」三个小节，环比口径：(本周-上周)/上周，上周为 0 时注明"不可比"
4. **禁止**查询 `sqlite_master` / `PRAGMA` 等内省入口（表名门禁会拦截）——表结构以本文件声明为准：`user_events`（user_id / event_date / event_type）。

## 统计口径

- **DAU**：当日去重 login 用户数（`COUNT(DISTINCT user_id) WHERE event_type='login'`）；周报报"日均 DAU"（上周 7 天 DAU 平均，保留 1 位小数）
- **新增用户**：当日首次出现的用户数（当天是其 `MIN(event_date)`）；周报报"周新增"（上周 7 天新增求和）
- **自然周**：Monday–Sunday，禁止用 MAX(event_date) 当"今天"锚点
- **周期锚点（正向规则）**：以上周 = 当前系统日期所在自然周的前一个完整自然周（周一~周日）推算周界。demo 库日期随播种窗口滚动（数据末端≈昨天），不要凭对话猜测具体日期——周界由系统日期推算，数据覆盖在探查步核实
- 周环比保留 1 位小数百分比
- 日期覆盖不足 7 天时在报告中注明
