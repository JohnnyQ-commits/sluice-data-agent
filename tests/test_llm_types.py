from sluice.llm.types import LLMResponse, Message, TextBlock, ToolDef, ToolResultBlock, ToolUseBlock


def test_tooldef_to_api():
    td = ToolDef("echo", "原样返回", {"type": "object", "properties": {}})
    api = td.to_api()
    assert api == {
        "name": "echo",
        "description": "原样返回",
        "input_schema": {"type": "object", "properties": {}},
    }


def test_response_text_joins_text_blocks():
    resp = LLMResponse(
        content=[TextBlock("你好"), ToolUseBlock("id1", "echo", {"text": "x"}), TextBlock("世界")],
        stop_reason="tool_use",
        usage={},
    )
    assert resp.text == "你好世界"
    assert [t.name for t in resp.tool_uses] == ["echo"]


def test_message_holds_blocks():
    m = Message("user", [ToolResultBlock("id1", "结果", is_error=False)])
    assert m.content[0].tool_use_id == "id1"
