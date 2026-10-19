"""统一工具协议——内置工具、Skill 私有工具、MCP 外部工具都归一到这里。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..llm.types import ToolDef


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


class Tool(ABC):
    """工具基类。子类必须定义 name/description/parameters 并实现 execute。"""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    owner_skill: str | None = None  # Skill 私有工具由 loader 打标，内置/MCP 工具为 None

    @abstractmethod
    def execute(self, args: dict[str, Any]) -> ToolResult: ...

    def to_def(self) -> ToolDef:
        return ToolDef(self.name, self.description, self.parameters)
