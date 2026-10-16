"""Anthropic Messages API 形状的消息/工具类型（自实现 function calling 的协议载体）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass
class ToolDef:
    """工具定义——JSON Schema 进 API 的 input_schema 字段。"""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_api(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


@dataclass
class Message:
    """一条消息。content 是块列表（user 可含 tool_result，assistant 可含 tool_use）。"""

    role: str  # "user" | "assistant"
    content: list  # list[TextBlock | ToolUseBlock | ToolResultBlock]


@dataclass
class LLMResponse:
    content: list  # list[TextBlock | ToolUseBlock]
    stop_reason: str | None  # "tool_use" | "end_turn" | ...
    usage: dict[str, Any]

    @property
    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]
