import pytest

from sluice.exceptions import RoundsExhaustedError
from sluice.llm.types import LLMResponse, Message, TextBlock, ToolResultBlock, ToolUseBlock
from sluice.runtime.loop import ReActRuntime
from sluice.tools.echo import EchoTool
from sluice.tools.registry import ToolRegistry


def make_registry():
    reg = ToolRegistry()
    reg.register(EchoTool())
    return reg


def resp_text(text):
    return LLMResponse(content=[TextBlock(text)], stop_reason="end_turn", usage={})


def resp_tool(text, tool_id, name, args):
    return LLMResponse(
        content=[TextBlock(text), ToolUseBlock(tool_id, name, args)],
        stop_reason="tool_use",
        usage={},
    )


class ScriptedClient:
    """按脚本依次返回响应，并记录收到的消息序列供断言。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[list[Message]] = []

    def chat(self, messages, system=None, tools=None):
        self.calls.append(messages)
        return self._responses.pop(0)


def test_loop_executes_tool_then_finishes():
    client = ScriptedClient(
        [
            resp_tool("我调用 echo 工具", "tu_1", "echo", {"text": "hello sluice"}),
            resp_text("echo 返回了 hello sluice"),
        ]
    )
    runtime = ReActRuntime(client, make_registry())
    final = runtime.run("帮我 echo 一下")

    assert final == "echo 返回了 hello sluice"
    assert len(client.calls) == 2
    # 第二轮请求里应含 assistant 的 tool_use 和 user 的 tool_result
    second = client.calls[1]
    roles = [m.role for m in second]
    assert roles == ["user", "assistant", "user"]
    tool_result = second[2].content[0]
    assert isinstance(tool_result, ToolResultBlock)
    assert tool_result.content == "hello sluice" and tool_result.is_error is False


def test_loop_unknown_tool_feeds_error_back():
    client = ScriptedClient(
        [
            resp_tool("调一个不存在的工具", "tu_1", "no_such_tool", {}),
            resp_text("好的我换个说法"),
        ]
    )
    runtime = ReActRuntime(client, make_registry())
    final = runtime.run("随便")

    assert final == "好的我换个说法"
    error_result = client.calls[1][2].content[0]
    assert error_result.is_error is True
    assert "未知工具" in error_result.content


def test_loop_records_trace():
    client = ScriptedClient(
        [
            resp_tool("调用中", "tu_1", "echo", {"text": "x"}),
            resp_text("完成"),
        ]
    )
    runtime = ReActRuntime(client, make_registry())
    runtime.run("trace 检查")

    assert len(runtime.trace) == 2
    assert runtime.trace[0]["tool_calls"][0]["name"] == "echo"
    assert runtime.trace[0]["tool_calls"][0]["observation"] == "x"


def test_loop_raises_when_rounds_exhausted():
    responses = [resp_tool("继续调", f"tu_{i}", "echo", {"text": "x"}) for i in range(5)]
    client = ScriptedClient(responses)
    runtime = ReActRuntime(client, make_registry(), max_rounds=3)
    with pytest.raises(RoundsExhaustedError):
        runtime.run("死循环")
