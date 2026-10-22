"""Skill ① 的 LangGraph 重实现（对比练习）——同一职责：查数 → 算环比 → 渲染周报。

对比锚点（写进 README）：
- Sluice：自研 ReActRuntime，模型自由决定调什么工具，guardrails 在四拦截点兜底
- LangGraph：开发者预先编排节点图，模型只在节点内干活——控制流显式、可观测性强，
  但工具选择/错误恢复要自己搭（Sluice 的拦截点在图编排范式里没有现成位置）
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict


class WeeklyState(TypedDict):
    question: str
    sql: str
    rows: list[dict[str, Any]]
    report: str


def build_graph(client: Any, query_fn: Callable[[str], list[dict]]) -> Any:
    """client: 有 .chat(messages, system=None, tools=None) 的对象（Sluice LLMClient 同构）；query_fn: SQL 执行器。"""

    def sql_node(state: WeeklyState) -> dict:
        resp = client.chat(
            [_user(f"为问题生成一条只读 SQLite SQL（表 sales: sale_date/city/category/amount）：\n{state['question']}")]
        )
        return {"sql": resp.text.strip()}

    def query_node(state: WeeklyState) -> dict:
        return {"rows": query_fn(state["sql"])}

    def render_node(state: WeeklyState) -> dict:
        resp = client.chat(
            [_user(f"把以下查询结果渲染成 markdown 数据周报（含 核心指标/重点发现 两节）：\n{state['rows']}")]
        )
        return {"report": resp.text}

    from langgraph.graph import END, StateGraph

    g = StateGraph(WeeklyState)
    g.add_node("sql", sql_node)
    g.add_node("query", query_node)
    g.add_node("render", render_node)
    g.set_entry_point("sql")
    g.add_edge("sql", "query")
    g.add_edge("query", "render")
    g.add_edge("render", END)
    return g.compile()


def _user(text: str):
    from sluice.llm.types import Message, TextBlock

    return Message("user", [TextBlock(text)])
