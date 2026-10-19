"""评测执行器测试：② behavior 层（单轮行为）+ ③ guardrail 层（确定性，零 LLM 成本）。"""

from typing import ClassVar

import pytest

from sluice.config.settings import Settings
from sluice.evals.model import EvalCase
from sluice.evals.runner import (
    EVAL_MAX_ROUNDS,
    GuardrailRunner,
    ScriptedClient,
    ScriptExhaustedError,
    _response_from_spec,
)
from sluice.guardrails.chain import GuardrailChain
from sluice.tools.base import Tool, ToolResult
from sluice.tools.registry import ToolRegistry


class SqlStubTool(Tool):
    name = "alpha.query"
    description = "demo"
    parameters: ClassVar[dict] = {"type": "object", "properties": {"sql": {"type": "string"}}}

    def execute(self, args):
        return ToolResult("ok")


def _registry():
    r = ToolRegistry()
    r.register(SqlStubTool())
    return r


def _case(**kw):
    base = {"name": "c", "layer": "guardrail", "input": "go"}
    return EvalCase.from_dict("alpha", {**base, **kw})


def test_guardrail_runner_pass_on_blocked(tmp_path):
    case = _case(
        script=[
            {"tool": "ghost_tool", "args": {}},
            {"tool": "alpha.query", "args": {"sql": "SELECT 1"}},
            {"text": "done"},
        ],
        expect_guardrails=[{"gate": "input", "action": "blocked"}],
    )
    result = GuardrailRunner(Settings(audit_dir=str(tmp_path / "a"))).run_case(case, _registry())
    assert result.status == "pass"
    assert result.detail["got"] == [{"gate": "input", "action": "blocked"}]


def test_guardrail_runner_fail_when_not_blocked(tmp_path):
    case = _case(
        script=[{"tool": "alpha.query", "args": {"sql": "SELECT 1"}}, {"text": "done"}],
        expect_guardrails=[{"gate": "input", "action": "blocked"}],
    )
    result = GuardrailRunner(Settings(audit_dir=str(tmp_path / "a"))).run_case(case, _registry())
    assert result.status == "fail"
    assert result.detail["got"] == []  # 无拦截事件


def test_guardrail_runner_script_exhausted_is_error(tmp_path):
    """剧本与运行时行为不符（缺 final）→ 末条回放 1 次后 ScriptExhaustedError → error，不崩评测引擎。"""
    case = _case(
        script=[{"tool": "ghost_tool", "args": {}}],
        expect_guardrails=[{"gate": "input", "action": "blocked"}],
    )
    result = GuardrailRunner(Settings(audit_dir=str(tmp_path / "a"))).run_case(case, _registry())
    assert result.status == "error"
    assert "评测剧本耗尽" in result.detail["error"]


def test_scripted_client_replay_capped_at_one():
    """末条回放限 1 次：出 → 回放 1 次（同一对象）→ 再次耗尽抛 ScriptExhaustedError，不无限回放伪造事件。"""
    client = ScriptedClient([_response_from_spec({"tool": "ghost_tool", "args": {}})])
    r1 = client.chat([], system=None, tools=None)  # 正常出
    assert client.chat([], system=None, tools=None) is r1  # 末条回放=同一对象（模型复读语义）
    assert len(client.calls) == 2
    with pytest.raises(ScriptExhaustedError, match="评测剧本耗尽"):
        client.chat([], system=None, tools=None)  # 第二次耗尽


def test_guardrail_runner_empty_script_is_error(tmp_path):
    """script=[]（from_dict 不校验非空）→ ScriptExhaustedError 归 error，不炸整批评测。"""
    case = _case(script=[], expect_guardrails=[])
    result = GuardrailRunner(Settings(audit_dir=str(tmp_path / "a"))).run_case(case, _registry())
    assert result.status == "error"
    assert "评测剧本为空" in result.detail["error"]


def test_guardrail_runner_non_dict_spec_is_error(tmp_path):
    """script 条目非 dict → 形状校验 TypeError → 兜底归 error（带异常类型前缀），不炸整批评测。"""
    case = _case(script=["oops"], expect_guardrails=[])
    result = GuardrailRunner(Settings(audit_dir=str(tmp_path / "a"))).run_case(case, _registry())
    assert result.status == "error"
    assert result.detail["error"].startswith("TypeError: ")


def test_eval_max_rounds_constant():
    """EVAL_MAX_ROUNDS 是模块级命名常量（门禁轮次上限，防剧本行为失控），取值 8。"""
    assert EVAL_MAX_ROUNDS == 8


def test_guardrail_runner_replay_is_model_parrot_degrades(tmp_path, monkeypatch):
    """钉死：回放语义=模型复读——缺 final 的剧本在输出自修轮回放末条，复检仍不过只产 degraded。

    回放产不出 repaired：若有人把回放改成伪造修复稿之类（语义漂移），期望 repaired
    就会假性翻绿——本测试断言 fail（实际 got 是 blocked→degraded）把这个假阳性钉住。
    """
    orig_init = GuardrailChain.__init__

    def _init_with_alpha_contract(self, settings, packages=()):
        orig_init(self, settings, packages)
        self._contracts = {"alpha": ["核心指标"]}  # 测试直填契约

    monkeypatch.setattr(GuardrailChain, "__init__", _init_with_alpha_contract)
    case = _case(
        # 先调 alpha.query 触达 alpha 契约；缺 final → 自修轮回放末条"短稿"（模型复读）
        script=[{"tool": "alpha.query", "args": {"sql": "SELECT 1"}}, {"text": "短稿"}],
        expect_guardrails=[{"gate": "output", "action": "repaired"}],
    )
    result = GuardrailRunner(Settings(audit_dir=str(tmp_path / "a"))).run_case(case, _registry())
    assert result.status == "fail"
    assert result.detail["got"] == [
        {"gate": "output", "action": "blocked"},
        {"gate": "output", "action": "degraded"},
    ]


def test_behavior_runner_routes_to_expected_skill():
    from sluice.evals.runner import BehaviorRunner
    from sluice.llm.types import LLMResponse, ToolUseBlock

    class RouteClient:
        def chat(self, messages, system=None, tools=None):
            return LLMResponse(
                content=[ToolUseBlock("t1", "alpha.query", {"sql": "SELECT 1"})],
                stop_reason="tool_use",
                usage={},
            )

    case = _case(layer="behavior", input="出报表", expect_skill="alpha")
    result = BehaviorRunner(RouteClient()).run_case(case, [], _registry())
    assert result.status == "pass" and result.detail["used_tools"] == ["alpha.query"]


def test_behavior_runner_wrong_skill_fails():
    from sluice.evals.runner import BehaviorRunner
    from sluice.llm.types import LLMResponse, ToolUseBlock

    class RouteClient:
        def chat(self, messages, system=None, tools=None):
            return LLMResponse(
                content=[ToolUseBlock("t1", "beta.other", {})], stop_reason="tool_use", usage={}
            )

    case = _case(layer="behavior", input="出报表", expect_skill="alpha")
    result = BehaviorRunner(RouteClient()).run_case(case, [], _registry())
    assert result.status == "fail" and result.detail["used_tools"] == ["beta.other"]


def test_behavior_runner_greeting_no_tool():
    from sluice.evals.runner import BehaviorRunner
    from sluice.llm.types import LLMResponse, TextBlock

    class ChatClient:
        def chat(self, messages, system=None, tools=None):
            return LLMResponse(content=[TextBlock("你好！")], stop_reason="end_turn", usage={})

    case = _case(layer="behavior", input="你好", expect_skill="")
    result = BehaviorRunner(ChatClient()).run_case(case, [], _registry())
    assert result.status == "pass"
