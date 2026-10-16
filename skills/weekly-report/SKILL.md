---
name: weekly-report
description: 数据周报生成。当用户要求生成周报、数据周报、weekly report、本周经营汇总时使用。流程：用 query_data 查询本周与上周的聚合数据，计算环比，再用 weekly-report.render_report 渲染成周报。
---

# 数据周报 Skill

## 使用流程

1. 用 `query_data` 查询数据（demo 库表 `sales`：sale_date/city/category/amount）。
2. 至少查询：本周总销售额、上周总销售额、环比；分品类汇总；Top 城市。
3. 把算好的指标交给 `weekly-report.render_report` 渲染。
4. 最终回答 = 渲染出的 markdown 周报全文。
