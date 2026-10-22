"""MCP protocol 层测试：JSON-RPC 2.0 编解码与结果提取。"""

import json

import pytest

from sluice.exceptions import MCPError
from sluice.mcp.protocol import (
    encode_request,
    extract_result_text,
    initialize_request,
    initialized_notification,
    parse_message,
    tools_call_request,
    tools_list_request,
)


def test_initialize_request_shape():
    line = initialize_request(1)
    msg = json.loads(line)
    assert msg == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "sluice", "version": "0.1.0"},
        },
    }


def test_tools_list_and_call_requests():
    assert json.loads(tools_list_request(2))["method"] == "tools/list"
    msg = json.loads(tools_call_request("echo", {"text": "hi"}, 3))
    assert msg["method"] == "tools/call" and msg["params"] == {"name": "echo", "arguments": {"text": "hi"}}


def test_parse_message_roundtrip():
    line = tools_list_request(7)
    assert parse_message(line)["id"] == 7


def test_parse_message_rejects_non_json():
    with pytest.raises(MCPError, match="非 JSON"):
        parse_message("not json")


def test_extract_result_text_joins_text_blocks():
    resp = {"result": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}
    assert extract_result_text(resp) == "a\nb"


def test_extract_result_text_empty_content():
    assert extract_result_text({"result": {"content": []}}) == ""


def test_extract_result_text_none_text_treated_as_empty():
    resp = {"result": {"content": [{"type": "text", "text": None}, {"type": "text", "text": "b"}]}}
    assert extract_result_text(resp) == "\nb"


def test_extract_result_text_raises_on_error_response():
    resp = {"error": {"code": -32601, "message": "Method not found"}}
    with pytest.raises(MCPError, match="MCP 响应错误"):
        extract_result_text(resp)


def test_initialized_notification_has_no_id():
    msg = json.loads(initialized_notification())
    assert msg["method"] == "notifications/initialized" and "id" not in msg


def test_encode_request_roundtrip_unicode_and_newline():
    line = encode_request("tools/call", {"name": "echo", "arguments": {"text": "中文\n第二行"}}, 9)
    msg = parse_message(line)
    assert msg["id"] == 9 and msg["params"]["arguments"]["text"] == "中文\n第二行"
