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

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def to_defs(self) -> list[ToolDef]:
        return [t.to_def() for t in self._tools.values()]
