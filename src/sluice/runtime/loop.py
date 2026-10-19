"""ReAct 主循环：Thought → Action(工具调用) → Observation → … → Final Answer。

这是整个平台的发动机。guardrails 在本循环的四个拦截点接入
（工具调用前 / 循环运行中 / 产出交付前 / 恢复策略），guardrails=None 时行为回到 P1。

事件回调（on_event）：dict 形式的 JSON 可序列化事件流，供 Flask SSE 推送。
trace 与事件相互独立——CLI 消费 trace，SSE 消费事件，语义互不影响。
事件类型：
- {"type": "round_start", "round": n}
- {"type": "tool_call", "round": n, "name": ..., "args": {...}}
- {"type": "observation", "round": n, "name": ..., "observation": ..., "is_error": bool}
- {"type": "final", "answer": ...}
- {"type": "error", "message": str(exc)}  # 异常终止路径收尾，emit 后异常仍向上传播
- {"type": "guardrail", "gate": ..., "action": ..., "round": n, "reason": ..., "detail"?: {...}}
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..exceptions import BudgetExceededError, RoundsExhaustedError
from ..guardrails.base import guardrail_event
from ..llm.client import LLMClient
from ..llm.types import LLMResponse, Message, TextBlock, ToolResultBlock, ToolUseBlock
from ..tools.registry import ToolRegistry

if TYPE_CHECKING:
    from ..guardrails.behavior_gate import BehaviorGate
    from ..guardrails.chain import GuardrailChain

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
        guardrails: GuardrailChain | None = None,
    ):
        self._client = client
        self._registry = registry
        self._system = system
        self._max_rounds = max_rounds
        self._on_event = on_event
        self._guardrails = guardrails
        self.trace: list[dict] = []  # 每轮轨迹，P1 经 SSE 推给前端

    def run(self, user_input: str, on_event: Callable[[dict], None] | None = None) -> str:
        # run 级 on_event 非 None 时覆盖构造器值（Flask 每请求独立队列）
        effective = on_event if on_event is not None else self._on_event
        # trace 局部累积、结束原子赋值：并发 run 互不串写，结束后 last-writer-wins
        local_trace: list[dict] = []
        messages: list[Message] = [Message("user", [TextBlock(user_input)])]
        behavior = self._guardrails.new_behavior() if self._guardrails else None
        used_tools: list[str] = []

        try:
            for round_no in range(1, self._max_rounds + 1):
                response = self._client.chat(
                    messages, system=self._system, tools=self._registry.to_defs()
                )
                self._emit(effective, {"type": "round_start", "round": round_no})
                tool_uses = response.tool_uses

                if not tool_uses:  # Final Answer → 拦截点③输出门禁 + 拦截点④自修
                    return self._deliver_output(
                        messages, effective, local_trace, round_no, response, used_tools
                    )

                messages.append(Message("assistant", response.content))
                observations = []
                for tu in tool_uses:
                    self._emit(
                        effective,
                        {"type": "tool_call", "round": round_no, "name": tu.name, "args": tu.input},
                    )
                    used_tools.append(tu.name)
                    obs = self._execute_tool_gated(tu, behavior, effective, round_no)
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
                if behavior:
                    behavior.commit_round(tool_uses)
                    over = behavior.record_usage(response.usage)
                    if over:
                        self._audit({"kind": "budget_exceeded", "round": round_no, "detail": over})
                        raise BudgetExceededError(over)
                messages.append(Message("user", observations))
                local_trace.append(self._record(round_no, response, observations))

            raise RoundsExhaustedError(f"ReAct 循环超过 {self._max_rounds} 轮未产出最终回答")
        except Exception as exc:
            self._emit(effective, {"type": "error", "message": str(exc)})
            raise
        finally:
            self.trace = local_trace  # 原子赋值：CLI 语义不变（run 后 rt.trace 仍可用）

    def _pre_tool_check(
        self, tu: ToolUseBlock, behavior: BehaviorGate | None
    ) -> tuple[str, str, dict] | None:
        """拦截点①输入门禁 + 拦截点②重复调用。返回 (gate, reason, detail) 或 None。"""
        if self._guardrails is None:
            return None
        tool = self._registry.get(tu.name)
        verdict = self._guardrails.input_gate.check(tu, tool)
        if verdict is not None:
            reason, detail = verdict
            return ("input", reason, detail)
        if behavior is not None:
            reason = behavior.check_call(tu)
            if reason is not None:
                return ("behavior", reason, {"tool": tu.name, "args": tu.input})
        return None

    def _execute_tool_gated(
        self,
        tu: ToolUseBlock,
        behavior: BehaviorGate | None,
        effective: Callable[[dict], None] | None,
        round_no: int,
    ) -> ToolResultBlock:
        """先过门禁再执行；被拦截则不执行，把拦截原因作为错误观察回喂模型自纠。"""
        block = self._pre_tool_check(tu, behavior)
        if block is not None:
            gate, reason, detail = block
            self._emit(effective, guardrail_event(gate, "blocked", reason, round_no, **detail))
            self._audit(
                {
                    "kind": "input_blocked",
                    "round": round_no,
                    "tool": tu.name,
                    **detail,
                    "reason": reason,
                }
            )
            return ToolResultBlock(tu.id, f"[门禁拦截] {reason}", is_error=True)
        return self._execute_tool(tu)

    def _deliver_output(
        self,
        messages: list[Message],
        effective: Callable[[dict], None] | None,
        local_trace: list[dict],
        round_no: int,
        response: LLMResponse,
        used_tools: list[str],
    ) -> str:
        """拦截点③输出门禁 + 拦截点④恢复策略：定向自修一轮，复检通过才采用。

        复检仍失败 → 降级交付原稿 + 审计副本——不悬挂用户端，拦截全程可见可审计。
        """
        answer = response.text
        if self._guardrails is None:
            self._emit(effective, {"type": "final", "answer": answer})
            local_trace.append(self._record(round_no, response, []))
            return answer

        sections = self._guardrails.contracts_for(used_tools)
        reason = self._guardrails.check_output(answer, sections)
        if reason is None:
            self._emit(effective, {"type": "final", "answer": answer})
            local_trace.append(self._record(round_no, response, []))
            return answer

        self._emit(effective, guardrail_event("output", "blocked", reason, round_no))
        self._audit(
            {"kind": "output_rejected", "round": round_no, "reason": reason, "answer": answer}
        )
        # 极简 retry prompt：契约置顶、错误点名、不复制文体（见知识库 minimal-retry-prompt 实录）
        retry_lines = [f"原因：{reason}"]
        if sections:
            retry_lines.append(f"必须包含以下小节标题：{'、'.join(sections)}")
        retry_lines.append("请直接重新输出完整最终回答，不要调用工具。")
        messages.append(Message("user", [TextBlock("\n".join(retry_lines))]))
        retry_resp = self._client.chat(
            messages, system=self._system, tools=self._registry.to_defs()
        )
        self._emit(effective, {"type": "round_start", "round": round_no + 1})
        repaired = retry_resp.text

        repair_reason = self._guardrails.check_output(repaired, sections)
        if repair_reason is None:
            self._emit(
                effective,
                guardrail_event(
                    "output", "repaired", f"自修通过（原拦截：{reason}）", round_no + 1
                ),
            )
            self._audit(
                {"kind": "output_repaired", "round": round_no + 1, "original_reason": reason}
            )
            self._emit(effective, {"type": "final", "answer": repaired})
            local_trace.append(self._record(round_no + 1, retry_resp, []))
            return repaired

        self._emit(
            effective,
            guardrail_event("output", "degraded", f"自修复检未通过：{repair_reason}", round_no + 1),
        )
        self._audit(
            {
                "kind": "output_degraded",
                "round": round_no + 1,
                "reason": repair_reason,
                "answer": repaired,
            }
        )
        self._emit(effective, {"type": "final", "answer": answer})
        local_trace.append(self._record(round_no + 1, retry_resp, []))
        return answer

    @staticmethod
    def _emit(callback: Callable[[dict], None] | None, event: dict) -> None:
        if callback is None:
            return
        try:
            callback(event)
        except Exception:
            logger.warning("on_event 回调异常，丢弃事件 %s", event.get("type"), exc_info=True)

    def _audit(self, entry: dict) -> None:
        if self._guardrails is not None:
            self._guardrails.audit.append(entry)

    def _execute_tool(self, tu: ToolUseBlock) -> ToolResultBlock:
        tool = self._registry.get(tu.name)
        if tool is None:
            return ToolResultBlock(tu.id, f"未知工具: {tu.name}", is_error=True)
        try:
            result = tool.execute(tu.input)
            return ToolResultBlock(tu.id, result.content, is_error=result.is_error)
        except Exception as exc:  # noqa: BLE001 — 工具异常回喂模型自纠
            return ToolResultBlock(tu.id, f"工具执行异常: {exc}", is_error=True)

    @staticmethod
    def _record(round_no: int, response: LLMResponse, observations: list) -> dict:
        return {
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
