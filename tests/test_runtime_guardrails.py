"""GuardrailChain + ReAct 循环接线测试：四拦截点 / 定向自修 / 降级交付 / kill switch。"""

import json
from typing import ClassVar

import pytest

from sluice.config.settings import Settings
from sluice.exceptions import BudgetExceededError
from sluice.guardrails.chain import GuardrailChain
from sluice.llm.types import LLMResponse, TextBlock, ToolUseBlock
from sluice.runtime.loop import ReActRuntime
from sluice.tools.base import Tool, ToolResult
from sluice.tools.registry import ToolRegistry


class ReportTool(Tool):
    """带 skill 前缀的桩工具，模拟 Skill 私有工具（声明 text 参数供 schema 校验）。"""

    name = "demo.report"
    description = "demo"
    parameters: ClassVar[dict] = {"type": "object", "properties": {"text": {"type": "string"}}}

    def execute(self, args):
        return ToolResult("done")


class ScriptedClient:
    """按剧本回放 LLMResponse，序列耗尽即 fail（防止测试静默通过）。"""

    def __init__(self, responses):
        self._responses = list(responses)

    def chat(self, messages, system=None, tools=None):
        assert self._responses, "剧本耗尽：测试没预期到这次 chat 调用"
        return self._responses.pop(0)


class RepairExplodesClient:
    """前两轮按剧本出（工具调用 → final），自修轮 chat 抛异常（网关抖动模拟）。"""

    def __init__(self, first, second):
        self._script = [first, second]

    def chat(self, messages, system=None, tools=None):
        if self._script:
            return self._script.pop(0)
        raise RuntimeError("网关抖动")


def _final(text, usage=None):
    return LLMResponse(content=[TextBlock(text)], stop_reason="end_turn", usage=usage or {})


def _call(name, args=None):
    return LLMResponse(
        content=[ToolUseBlock("t1", name, args or {})], stop_reason="tool_use", usage={}
    )


def _runtime(responses, tmp_path, **chain_kwargs):
    registry = ToolRegistry()
    registry.register(ReportTool())
    settings = Settings(audit_dir=str(tmp_path / "audit"), **chain_kwargs)
    chain = GuardrailChain(settings)
    rt = ReActRuntime(ScriptedClient(responses), registry, max_rounds=6, guardrails=chain)
    return rt, settings


def _events_of(rt):
    events = []
    rt.run("go", on_event=events.append)
    return events


def test_hallucinated_tool_blocked_and_audited(tmp_path):
    rt, _ = _runtime([_call("ghost_tool"), _final("完成")], tmp_path)
    events = _events_of(rt)
    g = [e for e in events if e["type"] == "guardrail"]
    assert len(g) == 1
    assert g[0]["gate"] == "input" and g[0]["action"] == "blocked"
    assert "幻觉工具" in g[0]["reason"]
    obs_events = [e for e in events if e["type"] == "observation"]
    assert obs_events[0]["is_error"] is True and "门禁拦截" in obs_events[0]["observation"]
    audit_lines = (tmp_path / "audit" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(audit_lines[0])["kind"] == "input_blocked"


def test_schema_violation_blocked(tmp_path):
    rt, _ = _runtime([_call("demo.report", {"text": 1}), _final("完成")], tmp_path)
    g = [e for e in _events_of(rt) if e["type"] == "guardrail"]
    assert g and "参数校验未通过" in g[0]["reason"]


def test_repeated_call_interrupted(tmp_path):
    rt, _ = _runtime([_call("demo.report"), _call("demo.report"), _final("完成")], tmp_path)
    g = [e for e in _events_of(rt) if e["type"] == "guardrail"]
    assert any(e["gate"] == "behavior" and e["action"] == "blocked" for e in g)
    audit_lines = (tmp_path / "audit" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["kind"] for line in audit_lines] == ["behavior_blocked"]


def test_token_budget_exceeded_raises_with_error_event(tmp_path):
    """token 超预算 → BudgetExceededError 向上传播，error 事件收尾。"""
    registry = ToolRegistry()
    registry.register(ReportTool())
    settings = Settings(audit_dir=str(tmp_path / "a2"), react_token_budget=10)
    client = ScriptedClient(
        [
            LLMResponse(
                content=[ToolUseBlock("t1", "demo.report", {})],
                stop_reason="tool_use",
                usage={"input_tokens": 80, "output_tokens": 50},
            )
        ]
    )
    rt = ReActRuntime(client, registry, max_rounds=5, guardrails=GuardrailChain(settings))
    events = []
    with pytest.raises(BudgetExceededError) as exc_info:
        rt.run("go", on_event=events.append)
    assert "token 预算超限" in str(exc_info.value)
    assert [e["type"] for e in events][-1] == "error"
    audit_lines = (tmp_path / "a2" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["kind"] for line in audit_lines] == ["budget_exceeded"]


def test_output_gate_repair_success(tmp_path):
    """第一轮回答缺小节 → 拦截 + 自修一轮 → 复检通过采用修复稿。"""
    registry = ToolRegistry()
    registry.register(ReportTool())
    settings = Settings(audit_dir=str(tmp_path / "a3"))
    chain = GuardrailChain(settings)
    chain._contracts = {"demo": ["核心指标", "重点发现"]}  # 测试直填契约
    # 契约按 used_tools 归因（contracts_for），先调一次 demo.report 让本轮触达 demo 契约
    client = ScriptedClient(
        [_call("demo.report"), _final("短回答"), _final("## 核心指标\n5\n## 重点发现\n好")]
    )
    rt = ReActRuntime(client, registry, max_rounds=5, guardrails=chain)
    events = []
    answer = rt.run("go", on_event=events.append)
    assert "重点发现" in answer
    g = [e for e in events if e["type"] == "guardrail"]
    assert [e["action"] for e in g] == ["blocked", "repaired"]
    assert events[-1]["type"] == "final" and events[-1]["answer"] == answer
    audit_lines = (tmp_path / "a3" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in audit_lines]
    assert kinds == ["output_rejected", "output_repaired"]


def test_output_gate_degraded_delivery(tmp_path):
    """自修复检仍未通过 → 降级交付原稿 + 审计副本，用户端不悬挂。"""
    registry = ToolRegistry()
    registry.register(ReportTool())
    settings = Settings(audit_dir=str(tmp_path / "a4"))
    chain = GuardrailChain(settings)
    chain._contracts = {"demo": ["核心指标"]}  # 测试直填契约
    # 同上：先调一次 demo.report 让本轮触达 demo 契约，输出门禁才有 sections 可查
    client = ScriptedClient([_call("demo.report"), _final("原稿"), _final("还是不行")])
    rt = ReActRuntime(client, registry, max_rounds=5, guardrails=chain)
    events = []
    answer = rt.run("go", on_event=events.append)
    assert answer == "原稿"
    g = [e for e in events if e["type"] == "guardrail"]
    assert [e["action"] for e in g] == ["blocked", "degraded"]
    assert events[-1]["type"] == "final" and events[-1]["answer"] == "原稿"


def test_guardrails_disabled_is_kill_switch(tmp_path):
    """guardrails_enabled=False → 行为回到 P1：未知工具旧路径，无 guardrail 事件。"""
    registry = ToolRegistry()
    registry.register(ReportTool())
    settings = Settings(audit_dir=str(tmp_path / "a5"), guardrails_enabled=False)
    # 生产组装同款模式：开关关闭 → guardrails=None（kill switch 回到 P1 行为）
    guardrails = GuardrailChain(settings) if settings.guardrails_enabled else None
    client = ScriptedClient([_call("ghost_tool"), _final("完成")])
    rt = ReActRuntime(client, registry, max_rounds=5, guardrails=guardrails)
    events = []
    assert rt.run("go", on_event=events.append) == "完成"
    assert not [e for e in events if e["type"] == "guardrail"]
    obs_events = [e for e in events if e["type"] == "observation"]
    assert "未知工具" in obs_events[0]["observation"]


def test_repair_chat_exception_degrades_to_original(tmp_path):
    """自修轮 chat 网关异常 → 降级交付原稿 + 审计副本，用户端不悬挂。"""
    registry = ToolRegistry()
    registry.register(ReportTool())
    settings = Settings(audit_dir=str(tmp_path / "a6"))
    chain = GuardrailChain(settings)
    chain._contracts = {"demo": ["核心指标"]}  # 测试直填契约
    client = RepairExplodesClient(_call("demo.report"), _final("缺小节的原稿"))
    rt = ReActRuntime(client, registry, max_rounds=5, guardrails=chain)
    events = []
    answer = rt.run("go", on_event=events.append)
    assert answer == "缺小节的原稿"
    g = [e for e in events if e["type"] == "guardrail"]
    assert [e["action"] for e in g] == ["blocked", "degraded"]
    assert "自修调用失败" in g[1]["reason"]
    assert events[-1]["type"] == "final" and events[-1]["answer"] == "缺小节的原稿"
    audit_lines = (tmp_path / "a6" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in audit_lines]
    assert kinds == ["output_rejected", "output_degraded"]


def test_repair_round_tool_use_dropped_then_degraded(tmp_path):
    """自修轮违规返回工具调用 → 丢弃留痕，只取文本复检；空文本复检不过 → 降级交付原稿。"""
    registry = ToolRegistry()
    registry.register(ReportTool())
    settings = Settings(audit_dir=str(tmp_path / "a7"))
    chain = GuardrailChain(settings)
    chain._contracts = {"demo": ["核心指标"]}  # 测试直填契约
    client = ScriptedClient([_call("demo.report"), _final("缺小节"), _call("demo.report")])
    rt = ReActRuntime(client, registry, max_rounds=5, guardrails=chain)
    events = []
    answer = rt.run("go", on_event=events.append)
    assert answer == "缺小节"  # 降级交付原稿
    g = [e for e in events if e["type"] == "guardrail"]
    assert [e["action"] for e in g] == ["blocked", "blocked", "degraded"]
    assert "工具调用，已忽略" in g[1]["reason"]
    assert events[-1]["type"] == "final" and events[-1]["answer"] == "缺小节"
    audit_lines = (tmp_path / "a7" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in audit_lines]
    assert kinds == ["output_rejected", "repair_tool_use_dropped", "output_degraded"]


def test_resolve_audit_dir_keeps_explicit(tmp_path, monkeypatch):
    """显式 SLUICE_AUDIT_DIR 配置不被 repo 根锚定覆盖（守卫只对默认值生效）。"""
    from sluice.config.settings import resolve_audit_dir

    custom = tmp_path / "custom_audit"
    monkeypatch.setenv("SLUICE_AUDIT_DIR", str(custom))
    s = resolve_audit_dir(Settings())
    assert s.audit_dir == str(custom)


def test_resolve_audit_dir_anchors_default(tmp_path, monkeypatch):
    """audit_dir 仍为默认值时锚定 repo 根，防 cwd 漂移写散审计目录。"""
    from sluice.config.settings import resolve_audit_dir

    monkeypatch.delenv("SLUICE_AUDIT_DIR", raising=False)
    s = resolve_audit_dir(Settings())
    assert s.audit_dir.endswith("audit")
