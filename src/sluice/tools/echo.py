from typing import Any, ClassVar

from .base import Tool, ToolResult


class EchoTool(Tool):
    """P0 出口验收工具：原样返回输入，验证模型→工具→模型的调用链路。"""

    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "原样返回输入文本。用于验证工具调用链路是否正常。"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要原样返回的文本"},
        },
        "required": ["text"],
    }

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(content=str(args.get("text", "")))
