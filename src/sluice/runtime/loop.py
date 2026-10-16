"""ReAct 主循环：Thought → Action(工具调用) → Observation → … → Final Answer。

这是整个平台的发动机。guardrails 在 P2 接入本循环的三个拦截点
（工具调用前 / 循环运行中 / 产出交付前），P0 只保留轮次上限和未知工具回喂。
"""

from __future__ import annotations

from ..exceptions import RoundsExhaustedError
from ..llm.client import LLMClient
from ..llm.types import LLMResponse, Message, TextBlock, ToolResultBlock, ToolUseBlock
from ..tools.registry import ToolRegistry

DEFAULT_SYSTEM = "你是 Sluice 平台的助手。可以调用提供的工具完成任务，完成后直接给出最终回答。"


class ReActRuntime:
    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        system: str = DEFAULT_SYSTEM,
        max_rounds: int = 10,
    ):
        self._client = client
        self._registry = registry
        self._system = system
        self._max_rounds = max_rounds
        self.trace: list[dict] = []  # 每轮轨迹，P1 经 SSE 推给前端

    def run(self, user_input: str) -> str:
        self.trace = []
        messages: list[Message] = [Message("user", [TextBlock(user_input)])]

        for round_no in range(1, self._max_rounds + 1):
            response = self._client.chat(
                messages, system=self._system, tools=self._registry.to_defs()
            )
            tool_uses = response.tool_uses

            if not tool_uses:  # Final Answer
                self._record(round_no, response, [])
                return response.text

            messages.append(Message("assistant", response.content))
            observations = []
            for tu in tool_uses:
                observations.append(self._execute_tool(tu))
            messages.append(Message("user", observations))
            self._record(round_no, response, observations)

        raise RoundsExhaustedError(f"ReAct 循环超过 {self._max_rounds} 轮未产出最终回答")

    def _execute_tool(self, tu: ToolUseBlock) -> ToolResultBlock:
        tool = self._registry.get(tu.name)
        if tool is None:
            return ToolResultBlock(tu.id, f"未知工具: {tu.name}", is_error=True)
        try:
            result = tool.execute(tu.input)
            return ToolResultBlock(tu.id, result.content, is_error=result.is_error)
        except Exception as exc:  # noqa: BLE001 — 工具异常回喂模型自纠
            return ToolResultBlock(tu.id, f"工具执行异常: {exc}", is_error=True)

    def _record(self, round_no: int, response: LLMResponse, observations: list) -> None:
        self.trace.append(
            {
                "round": round_no,
                "thought": response.text,
                "tool_calls": [
                    {
                        "name": tu.name,
                        "args": tu.input,
                        "observation": obs.content,
                        "is_error": obs.is_error,
                    }
                    for tu, obs in zip(response.tool_uses, observations)
                ],
            }
        )
