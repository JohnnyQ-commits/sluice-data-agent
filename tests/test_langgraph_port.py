"""LangGraph 对比移植 smoke：StateGraph 重实现 Skill ① 骨架（可选依赖，未装则跳过）。"""

import pytest

langgraph = pytest.importorskip("langgraph")

from examples.langgraph_weekly.graph import build_graph


class StubClient:
    """chat 按前缀出招：SQL 生成 → 周报渲染。"""

    def chat(self, messages, system=None, tools=None):
        from sluice.llm.types import LLMResponse, TextBlock

        prompt = messages[-1].content[0].text
        if "SQL" in prompt:
            text = "SELECT city, SUM(amount) AS total FROM sales GROUP BY city"
        else:
            text = f"# 数据周报\n\n## 核心指标\n{prompt}\n\n## 重点发现\n杭州领跑。"
        return LLMResponse(content=[TextBlock(text)], stop_reason="end_turn", usage={})


def test_graph_runs_end_to_end():
    def fake_query(sql: str) -> list[dict]:
        return [{"city": "杭州", "total": 100.0}]

    graph = build_graph(client=StubClient(), query_fn=fake_query)
    state = graph.invoke({"question": "上周各城市销量"})
    assert state["sql"].startswith("SELECT")
    assert state["report"] and "杭州" in state["report"]
