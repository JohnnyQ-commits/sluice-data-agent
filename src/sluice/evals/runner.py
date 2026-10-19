"""三层评测执行器：guardrail（确定性）/ behavior（单轮）/ outcome（完整循环）。

评测引擎自身不依赖真实 LLM：runner 接受 client 参数，测试注入桩。
pytest 套件保持零 API key 可跑（P0 以来的红线）。
"""

from __future__ import annotations

from ..config.settings import Settings
from ..exceptions import SluiceError
from ..guardrails.chain import GuardrailChain
from ..llm.types import LLMResponse, TextBlock, ToolUseBlock
from ..runtime.loop import ReActRuntime
from ..tools.registry import ToolRegistry
from .model import CaseResult, EvalCase


def _response_from_spec(spec: dict) -> LLMResponse:
    """script 条目 → LLMResponse：{"tool","args"} → 工具调用；{"text"} → 最终回答。"""
    if "tool" in spec:
        return LLMResponse(
            content=[ToolUseBlock("t1", spec["tool"], spec.get("args", {}))],
            stop_reason="tool_use",
            usage={},
        )
    return LLMResponse(content=[TextBlock(spec.get("text", ""))], stop_reason="end_turn", usage={})


class ScriptedClient:
    """评测剧本回放（与 tests 同款模式，guardrail 层负向用例专用）。

    剧本耗尽后回放末条：缺 final 的剧本不在断言处炸掉评测引擎，
    而是让循环自然走到轮次耗尽（RoundsExhaustedError），由 run_case 归为 error。
    """

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)

    def chat(self, messages, system=None, tools=None):
        assert self._responses, "评测剧本为空：script 与运行时行为不符"
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


class GuardrailRunner:
    """③ 负向门禁评测：剧本驱动完整 ReAct 循环，断言门禁事件真的发生。确定性、零 LLM 成本。"""

    def __init__(self, settings: Settings):
        self._settings = settings

    def run_case(self, case: EvalCase, registry: ToolRegistry) -> CaseResult:
        chain = GuardrailChain(self._settings, [])  # 无契约：此层只测拦截，不测产出
        client = ScriptedClient([_response_from_spec(s) for s in case.script])
        rt = ReActRuntime(client, registry, max_rounds=8, guardrails=chain)
        events: list[dict] = []
        try:
            rt.run(case.input, on_event=events.append)
        except SluiceError as exc:  # 轮次耗尽/超预算 = 剧本与行为不符或门禁终止
            return CaseResult(case.skill, case.name, case.layer, "error", {"error": str(exc)})
        got = [
            {"gate": e["gate"], "action": e["action"]} for e in events if e["type"] == "guardrail"
        ]
        missing = [x for x in case.expect_guardrails if x not in got]
        return CaseResult(
            case.skill,
            case.name,
            case.layer,
            "pass" if not missing else "fail",
            {"expected": case.expect_guardrails, "got": got},
        )
