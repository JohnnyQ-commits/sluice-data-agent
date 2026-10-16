"""ReAct 主循环：Thought → Action(工具调用) → Observation → … → Final Answer。

这是整个平台的发动机。guardrails 在 P2 接入本循环的三个拦截点
（工具调用前 / 循环运行中 / 产出交付前），P0 只保留轮次上限和未知工具回喂。

事件回调（on_event）：dict 形式的 JSON 可序列化事件流，供 Flask SSE 推送。
trace 与事件相互独立——CLI 消费 trace，SSE 消费事件，语义互不影响。
事件类型：
- {"type": "round_start", "round": n}
- {"type": "tool_call", "round": n, "name": ..., "args": {...}}
- {"type": "observation", "round": n, "name": ..., "observation": ..., "is_error": bool}
- {"type": "final", "answer": ...}
- {"type": "error", "message": str(exc)}  # 异常终止路径收尾，emit 后异常仍向上传播
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..exceptions import RoundsExhaustedError
from ..llm.client import LLMClient
from ..llm.types import LLMResponse, Message, TextBlock, ToolResultBlock, ToolUseBlock
from ..tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM = "你是 Sluice 平台的助手。可以调用提供的工具完成任务，完成后直接给出最终回答。"


class ReActRuntime:
    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        system: str = DEFAULT_SYSTEM,
        max_rounds: int = 10,
        on_event: Callable[[dict], None] | None = None,
    ):
        self._client = client
        self._registry = registry
        self._system = system
        self._max_rounds = max_rounds
        self._on_event = on_event
        self.trace: list[dict] = []  # 每轮轨迹，P1 经 SSE 推给前端

    def run(self, user_input: str, on_event: Callable[[dict], None] | None = None) -> str:
        # run 级 on_event 非 None 时覆盖构造器值（Flask 每请求独立队列）
        effective = on_event if on_event is not None else self._on_event
        self.trace = []
        messages: list[Message] = [Message("user", [TextBlock(user_input)])]

        try:
            for round_no in range(1, self._max_rounds + 1):
                response = self._client.chat(
                    messages, system=self._system, tools=self._registry.to_defs()
                )
                self._emit(effective, {"type": "round_start", "round": round_no})
                tool_uses = response.tool_uses

                if not tool_uses:  # Final Answer
                    self._emit(effective, {"type": "final", "answer": response.text})
                    self._record(round_no, response, [])
                    return response.text

                messages.append(Message("assistant", response.content))
                observations = []
                for tu in tool_uses:
                    self._emit(
                        effective,
                        {
                            "type": "tool_call",
                            "round": round_no,
                            "name": tu.name,
                            "args": tu.input,
                        },
                    )
                    obs = self._execute_tool(tu)
                    self._emit(
                        effective,
                        {
                            "type": "observation",
                            "round": round_no,
                            "name": tu.name,
                            "observation": obs.content,
                            "is_error": obs.is_error,
                        },
                    )
                    observations.append(obs)
                messages.append(Message("user", observations))
                self._record(round_no, response, observations)

            raise RoundsExhaustedError(f"ReAct 循环超过 {self._max_rounds} 轮未产出最终回答")
        except Exception as exc:
            self._emit(effective, {"type": "error", "message": str(exc)})
            raise

    @staticmethod
    def _emit(callback: Callable[[dict], None] | None, event: dict) -> None:
        if callback is None:
            return
        try:
            callback(event)
        except Exception:
            logger.warning("on_event 回调异常，丢弃事件 %s", event.get("type"), exc_info=True)

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
