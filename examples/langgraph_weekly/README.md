# LangGraph 对比移植（练习）

同一职责（数据周报三步）的两种实现：
- Sluice：`runtime/loop.py` ReAct 循环 + guardrails 四拦截点 + evals 三层
- LangGraph：StateGraph 显式编排（本目录 graph.py）

## 对比笔记（执行时填实测结论）

| 维度 | Sluice 自研 | LangGraph |
|---|---|---|
| 控制流 | 模型自主 + 门禁兜底 | 开发者预编排 |
| 工具选择 | 模型 + 行为门禁 | 节点内自定 |
| 错误恢复 | 拦截→自修→降级（内建） | 自行搭（checkpoint/retry 原语） |
| 评测 | evals 三层内建 | 无（外接） |
| 什么时候不该用自研 | —— | 图编排复杂多角色工作流时 |

## 实测笔记（装环境跑通后填）

实测版本：langgraph 1.2.16（连带拉入 langchain-core 1.6.13 / langchain-protocol 0.0.20 等依赖，装包面明显比 Sluice 零依赖 harness 重）。

- **TypedDict 状态合并**：节点只返回增量 dict（`{"sql": ...}`），StateGraph 自动 merge 进全量 state——比 Sluice 的 WorkflowState 全量透传省事；但 merge 是按 key last-write-wins，没有类型校验，列表字段（rows）替换而非追加，复杂状态要自己上 reducer（Annotated + operator.add）才有合并语义。
- **API 面**：add_node/add_edge/set_entry_point/compile 五件套是纯显式接线，三节点八行——可读性极好、流向一目了然（Sluice 的 ReAct 循环流向藏在 loop 的 while 里）；代价是线性管道写起来反而比 Sluice 一个 for 循环啰嗦，价值要到 conditional edges（分支/回跳）才体现。
- **StubClient 暴露的范式差异**：静态图里模型不决定控制流，测试里"按前缀出招"的 StubClient（SQL prompt→SQL 文本，render prompt→周报文本）本质是把模型自主性替换成了 if 分支——这正是对比锚点：Sluice 的行为门禁管的是"模型选了什么工具"，图范式下这个问题不存在，但"节点失败怎么自修"也没有现成答案。
- **类型对接零摩擦**：Sluice 的 `Message("user", [TextBlock(text)])` 与 `resp.text` 属性在图节点里直接可用，client 同构注入（build_graph 参数）比全局 LLM 单例干净。
- **import 位置**：`from langgraph.graph import ...` 放 build_graph 内部，模块级导入零 langgraph 依赖——与测试的 importorskip 双保险，未装 langgraph 时 `import examples.langgraph_weekly.graph` 本身也不炸。
