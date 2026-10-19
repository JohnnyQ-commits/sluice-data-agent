"""GuardrailChain——四个拦截点的编排入口。

None 即关闭（kill switch）：ReActRuntime 接受 guardrails=None，行为回到 P1。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..config.settings import Settings
from .base import AuditLog
from .behavior_gate import BehaviorGate
from .input_gate import InputGate
from .output_gate import check_output

if TYPE_CHECKING:
    from ..skills.loader import SkillPackage


class GuardrailChain:
    def __init__(self, settings: Settings, packages: Iterable[SkillPackage] = ()):
        self.input_gate = InputGate()
        self.token_budget = settings.react_token_budget
        self.audit = AuditLog(settings.audit_dir)
        # Skill 产出契约：{skill 名: [必需小节]}，来自 SKILL.md frontmatter output_contract
        self._contracts = {p.name: list(getattr(p, "output_contract", [])) for p in packages}

    def new_behavior(self) -> BehaviorGate:
        """行为门禁是 run 级有状态对象——每次 run 新建，跨 run 不共享。"""
        return BehaviorGate(self.token_budget)

    def contracts_for(self, tool_names: list[str]) -> list[str]:
        """本次 run 触达过的 Skill 的产出契约并集（工具名 {skill}.{tool} 前缀归因）。"""
        sections: list[str] = []
        for name in tool_names:
            skill = name.split(".", 1)[0] if "." in name else ""
            sections.extend(self._contracts.get(skill, []))
        return sorted(set(sections))

    def check_output(self, answer: str, required_sections: list[str]) -> str | None:
        return check_output(answer, required_sections)
