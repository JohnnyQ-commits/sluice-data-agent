"""行为门禁（拦截点②，循环运行中）：连续重复调用打断 + token 预算。"""

from __future__ import annotations

import json

from ..llm.types import ToolUseBlock


def call_key(tu: ToolUseBlock) -> str:
    """调用指纹：工具名 + 规范化参数（键序无关）。"""
    return f"{tu.name}:{json.dumps(tu.input, sort_keys=True, ensure_ascii=False)}"


class BehaviorGate:
    """run 级状态（每次 run 新建实例）——跨 run 不共享记忆。

    重复判定：与**上一轮**完全相同的调用（工具名+参数一致）→ 打断。
    A→B→A 隔轮重复不算，留给 P3 evals 观察是否需要收紧。
    """

    def __init__(self, token_budget: int):
        self._budget = token_budget
        self._tokens_used = 0
        self._prev_keys: set[str] = set()

    def check_call(self, tu: ToolUseBlock) -> str | None:
        """工具执行前调用。返回 None 放行，返回打断原因（回喂模型）。"""
        if call_key(tu) in self._prev_keys:
            return (
                f"检测到与上一轮完全相同的重复调用 {tu.name}，已强制打断："
                "请换一种方法推进，或直接给出最终回答"
            )
        return None

    def commit_round(self, tool_uses: list[ToolUseBlock]) -> None:
        """每轮工具执行完后登记本轮调用指纹，作为下一轮的比对基准。"""
        self._prev_keys = {call_key(tu) for tu in tool_uses}

    def record_usage(self, usage: dict) -> str | None:
        """每轮末累计 token；超预算返回终止原因（loop 据此抛 BudgetExceededError）。"""
        self._tokens_used += int(usage.get("input_tokens") or 0) + int(
            usage.get("output_tokens") or 0
        )
        if self._budget and self._tokens_used > self._budget:
            return f"token 预算超限：已用 {self._tokens_used} > 预算 {self._budget}"
        return None
