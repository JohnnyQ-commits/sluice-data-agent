"""评测执行器测试：③ guardrail 层（确定性，零 LLM 成本）。"""

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
    """末条回放限 1 次：出 → 回放 1 次 → 再次耗尽抛 ScriptExhaustedError，不无限回放伪造事件。"""
    client = ScriptedClient([_response_from_spec({"tool": "ghost_tool", "args": {}})])
    assert client.chat([], system=None, tools=None) is not None  # 正常出
    assert client.chat([], system=None, tools=None) is not None  # 末条回放 1 次
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
    """EVAL_MAX_ROUNDS 是模块级命名常量，且 runner 构造 runtime 用它（防手改魔数漂移）。"""
    assert EVAL_MAX_ROUNDS == 8
