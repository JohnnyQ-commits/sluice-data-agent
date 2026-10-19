"""评测执行器测试：③ guardrail 层（确定性，零 LLM 成本）。"""

from typing import ClassVar

from sluice.config.settings import Settings
from sluice.evals.model import EvalCase
from sluice.evals.runner import GuardrailRunner
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
    """剧本与运行时行为不符（缺 final）→ RoundsExhausted → error，不崩评测引擎。"""
    case = _case(
        script=[{"tool": "ghost_tool", "args": {}}],
        expect_guardrails=[{"gate": "input", "action": "blocked"}],
    )
    result = GuardrailRunner(Settings(audit_dir=str(tmp_path / "a"))).run_case(case, _registry())
    assert result.status == "error"
    assert "ReAct 循环超过" in result.detail["error"]
