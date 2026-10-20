"""行为门禁（拦截点②，循环运行中）：连续重复调用打断 + token 预算。"""

from __future__ import annotations

import json
import threading

from ..llm.types import ToolUseBlock


def call_key(tu: ToolUseBlock) -> str:
    """调用指纹：工具名 + 规范化参数（键序无关）。"""
    return f"{tu.name}:{json.dumps(tu.input, sort_keys=True, ensure_ascii=False)}"


class BehaviorGate:
    """run 级状态（每次 run 新建实例）——跨 run 不共享记忆。

    重复判定两档：
    - 同轮内（同一条 assistant 消息）完全相同调用 → 打断（P3 补，堵同轮双发漏拦）
    - 与上一轮完全相同调用 → 打断；A→B→A 隔轮重复不算，留给 P3 evals 观察是否收紧
    check_call 放行即登记当轮指纹（claim-on-pass）：被拦调用不算已执行，
    同轮重试仍由输入门禁/重复判定接住，不会因 claim 缺失而漏拦。
    """

    def __init__(self, token_budget: int):
        self._budget = token_budget
        self._tokens_used = 0
        self._prev_keys: set[str] = set()
        self._round_keys: set[str] = set()
        self._lock = threading.Lock()

    def check_call(self, tu: ToolUseBlock) -> str | None:
        """工具执行前调用。返回 None 放行（并登记当轮指纹），返回打断原因（回喂模型）。"""
        with self._lock:
            key = call_key(tu)
            if key in self._round_keys:
                return (
                    f"检测到同一轮内完全相同的重复调用 {tu.name}，已强制打断："
                    "一次工具调用即可，请不要在同一条消息里重复发起"
                )
            if key in self._prev_keys:
                return (
                    f"检测到与上一轮完全相同的重复调用 {tu.name}，已强制打断："
                    "请换一种方法推进，或直接给出最终回答"
                )
            self._round_keys.add(key)
            return None

    def commit_round(self, tool_uses: list[ToolUseBlock]) -> None:
        """每轮末轮转比对基准：本轮全部指纹（含被拦的）成为下一轮基准，当轮登记清空。

        M-2：锁只保证单次调用原子，调用方须保证 commit_round 不与同轮 check_call
        并发（当前 loop.py 顺序 for 循环保证了这点，P4 并发执行器设计须遵守）。
        """
        with self._lock:
            self._prev_keys = {call_key(tu) for tu in tool_uses}
            self._round_keys = set()

    def record_usage(self, usage: dict) -> str | None:
        """每轮末累计 token；超预算返回终止原因（loop 据此抛 BudgetExceededError）。"""
        self._tokens_used += int(usage.get("input_tokens") or 0) + int(
            usage.get("output_tokens") or 0
        )
        if self._budget and self._tokens_used > self._budget:
            return f"token 预算超限：已用 {self._tokens_used} > 预算 {self._budget}"
        return None
