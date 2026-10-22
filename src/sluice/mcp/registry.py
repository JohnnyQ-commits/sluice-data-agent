"""MCP 注册表：.mcp.json 发现 → 子进程启动/initialize/tools/list → McpTool 适配进 Tool 协议。

命名约定：mcp__<server>__<tool>（双下划线不撞 Skill 的点分前缀约定，行为门禁的
skill 归因 split(".") 天然不会把 MCP 工具误归到某 Skill）。
单个 server 启动失败只警告跳过（kill switch：MCP 挂了不拖垮平台）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..exceptions import MCPError, MCPTimeoutError
from ..tools.base import Tool, ToolResult
from .protocol import PROTOCOL_VERSION, extract_result_text
from .transport import StdioTransport

logger = logging.getLogger(__name__)


class McpTool(Tool):
    def __init__(self, server_name: str, meta: dict[str, Any], transport: StdioTransport):
        self.name = f"mcp__{server_name}__{meta['name']}"
        self.description = meta.get("description", "")
        self.parameters = meta.get("inputSchema", {"type": "object", "properties": {}})
        self._raw_name = meta["name"]
        self._transport = transport

    def execute(self, args: dict[str, Any]) -> ToolResult:
        try:
            resp = self._transport.request("tools/call", {"name": self._raw_name, "arguments": args})
            result = resp.get("result") or {}
            return ToolResult(extract_result_text(resp), is_error=bool(result.get("isError")))
        except MCPTimeoutError as exc:
            return ToolResult(f"MCP 工具 {self.name} 超时: {exc}", is_error=True)
        except MCPError as exc:
            return ToolResult(str(exc), is_error=True)


class McpServer:
    def __init__(self, name: str, command: list[str], timeout: float = 30.0):
        self.name = name
        self._command = command
        self._timeout = timeout
        self._transport: StdioTransport | None = None
        self.tools: list[McpTool] = []

    def start(self) -> list[McpTool]:
        self._transport = StdioTransport(self._command, timeout=self._timeout)
        self._transport.start()
        self._transport.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "sluice", "version": "0.1.0"},
        })
        self._transport.notify("notifications/initialized", {})  # 通知无响应，必须 notify 不能 request
        listing = self._transport.request("tools/list", {})
        self.tools = [McpTool(self.name, meta, self._transport) for meta in listing.get("result", {}).get("tools", [])]
        return self.tools

    def close(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None


def discover(config_path: str | Path) -> list[McpServer]:
    """解析 .mcp.json（mcpServers: {name: {command, args, timeout?}}）。

    配置错误走 MCPError 带路径/原因/服务名，便于 CLI 打友好错误而非裸 traceback。
    """
    path = Path(config_path)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MCPError(f"配置文件无法解析: {path}: {exc}") from exc
    servers = []
    for name, spec in (cfg.get("mcpServers") or {}).items():
        if "command" not in spec:
            raise MCPError(f"MCP server '{name}' 缺少 command 配置: {path}")
        command = [spec["command"], *spec.get("args", [])]
        servers.append(McpServer(name, command, timeout=float(spec.get("timeout", 30.0))))
    return servers


def load_mcp_tools(config_path: str | Path) -> list[McpServer]:
    """发现 + 启动全部 server；单个失败警告跳过，不拖垮启动。"""
    started: list[McpServer] = []
    for server in discover(config_path):
        try:
            server.start()
            started.append(server)
            logger.info("MCP server %s 就绪: %d 工具", server.name, len(server.tools))
        except MCPError as exc:
            server.close()  # Popen 已成功但握手/列举失败：收掉子进程防孤儿泄漏（Popen 失败时 close 是无害 no-op）
            logger.warning("MCP server %s 启动失败，已跳过: %s", server.name, exc)
    return started
