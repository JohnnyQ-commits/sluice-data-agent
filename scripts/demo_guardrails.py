"""Guardrails 现场演示（确定性，不依赖网关）：幻觉工具拦截 + 重复调用打断 + 产出自修复。

用法: .venv/Scripts/python scripts/demo_guardrails.py
"""

from typing import Any, ClassVar

from sluice.config.settings import Settings
from sluice.guardrails.chain import GuardrailChain
from sluice.llm.types import LLMResponse, TextBlock, ToolUseBlock
from sluice.runtime.loop import ReActRuntime
from sluice.skills.loader import SkillPackage
from sluice.tools.base import Tool, ToolResult
from sluice.tools.registry import ToolRegistry


class DemoReportTool(Tool):
    name: ClassVar[str] = "demo.report"
    description: ClassVar[str] = "演示用产出工具"
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def execute(self, args):
        return ToolResult("done")


def resp_text(text):
    return LLMResponse(content=[TextBlock(text)], stop_reason="end_turn", usage={})


def resp_call(name, args=None):
    return LLMResponse(
        content=[ToolUseBlock("t1", name, args or {})], stop_reason="tool_use", usage={}
    )


class ScriptedClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def chat(self, messages, system=None, tools=None):
        return self._responses.pop(0)


def main():
    registry = ToolRegistry()
    registry.register(DemoReportTool())
    packages = [
        SkillPackage(
            name="demo",
            description="演示 Skill",
            path=None,
            output_contract=["核心指标", "重点发现"],
        )
    ]
    chain = GuardrailChain(Settings(audit_dir="audit"), packages)
    client = ScriptedClient(
        [
            resp_call("ghost_tool"),  # 幻觉工具 → 输入门禁拦截
            resp_call("demo.report"),  # 合法调用
            resp_call("demo.report"),  # 重复调用 → 行为门禁打断
            resp_text("太短了"),  # 缺小节 → 输出门禁拦截 → 自修
            resp_text("## 核心指标\n总申请量 1,234 件\n## 重点发现\n通过率环比上升"),
        ]
    )
    rt = ReActRuntime(client, registry, max_rounds=8, guardrails=chain)
    print("--- 事件流 ---")
    rt.run("出一份 demo 报告", on_event=lambda e: print(e))
    print("--- 审计落盘 ---")
    print(Settings(audit_dir="audit").audit_dir + "/audit.jsonl")


if __name__ == "__main__":
    main()
