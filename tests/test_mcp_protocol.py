"""MCP protocol 层测试：JSON-RPC 2.0 编解码与结果提取。"""

import pytest

from sluice.exceptions import MCPError
from sluice.mcp.protocol import encode_request, extract_result_text, parse_message


def test_parse_message_roundtrip():
    line = encode_request("tools/list", {}, 7)
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


def test_encode_request_roundtrip_unicode_and_newline():
    line = encode_request("tools/call", {"name": "echo", "arguments": {"text": "中文\n第二行"}}, 9)
    msg = parse_message(line)
    assert msg["id"] == 9 and msg["params"]["arguments"]["text"] == "中文\n第二行"
