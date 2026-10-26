from __future__ import annotations

from ..llm.types import ToolDef
from .base import Tool


class ToolRegistry:
    """工具池。模型视角只有一个扁平工具池，来源（内置/Skill/MCP）无关。"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重复注册: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """注销单个工具（Skill 卸载用）；不存在返回 False。"""
        return self._tools.pop(name, None) is not None

    def unregister_owner(self, owner_skill: str) -> int:
        """注销某 Skill 名下的全部私有工具，返回注销数。"""
        names = [n for n, t in self._tools.items() if t.owner_skill == owner_skill]
        for n in names:
            del self._tools[n]
        return len(names)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def to_defs(self) -> list[ToolDef]:
        return [t.to_def() for t in self._tools.values()]
