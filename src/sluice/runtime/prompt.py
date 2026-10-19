"""平台 system prompt 组装——web 壳与评测层共用（评测层的路由评测要用同款提示）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..skills.loader import SkillPackage


def build_system(packages: list[SkillPackage]) -> str:
    base = "你是 Sluice 平台的助手。可以调用提供的工具完成任务，完成后直接给出最终回答。"
    if packages:
        lines = "\n".join(p.prompt_line() for p in packages)
        base += "\n\n可用的 Skill（通过其带前缀的工具使用）：\n" + lines
    return base
