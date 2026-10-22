"""MCP JSON-RPC 2.0 编解码——自实现 client 的协议层（不用官方 SDK）。

消息形态：单行 JSON（stdio 传输以行为帧）。本层只管编码请求/解析消息/提取结果文本，
不含 IO——那是 transport 的职责。
"""

from __future__ import annotations

import json
from typing import Any

from ..exceptions import MCPError

PROTOCOL_VERSION = "2024-11-05"


def initialize_request(req_id: int) -> str:
    return encode_request(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "sluice", "version": "0.1.0"},
        },
        req_id,
    )


def initialized_notification() -> str:
    """initialize 完成的通知（无 id，不期望响应）。"""
    return encode_request("notifications/initialized", {}, None)


def tools_list_request(req_id: int) -> str:
    return encode_request("tools/list", {}, req_id)


def tools_call_request(name: str, arguments: dict, req_id: int) -> str:
    return encode_request("tools/call", {"name": name, "arguments": arguments}, req_id)


def parse_message(line: str) -> dict[str, Any]:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as exc:
        raise MCPError(f"MCP 消息非 JSON: {line[:120]} ({exc})") from exc
    if not isinstance(msg, dict):
        raise MCPError(f"MCP 消息应为 JSON 对象: {line[:120]}")
    return msg


def extract_result_text(response: dict[str, Any]) -> str:
    """tools/call 响应 → 文本内容拼接（text 块以换行连接，其他类型块忽略）。

    error 响应显式抛 MCPError——不静默吞成空串（防御纵深：T7 transport 也会先检 error）。
    """
    if "error" in response:
        raise MCPError(f"MCP 响应错误: {json.dumps(response['error'], ensure_ascii=False)[:200]}")
    content = (response.get("result") or {}).get("content") or []
    return "\n".join(b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text")


def encode_request(method: str, params: dict[str, Any], req_id: int | None) -> str:
    """单行 JSON-RPC 2.0 消息；req_id=None 为通知（无 id 字段）。公开给 transport 复用。

    返回裸 UTF-8 文本；transport 必须在 Popen 两端显式 encoding='utf-8'
    （Windows 默认 locale 编码会炸中文）。
    """
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
    if req_id is not None:
        msg["id"] = req_id
    return json.dumps(msg, ensure_ascii=False)
