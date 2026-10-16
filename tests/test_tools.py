import pytest

from sluice.tools.base import Tool, ToolResult
from sluice.tools.echo import EchoTool
from sluice.tools.registry import ToolRegistry


def test_echo_executes():
    result = EchoTool().execute({"text": "hello sluice"})
    assert result == ToolResult(content="hello sluice")


def test_registry_register_get_and_defs():
    reg = ToolRegistry()
    reg.register(EchoTool())
    assert reg.get("echo") is not None
    assert reg.get("nope") is None
    defs = reg.to_defs()
    assert defs[0].name == "echo"
    assert defs[0].parameters["required"] == ["text"]


def test_registry_rejects_duplicate_name():
    reg = ToolRegistry()
    reg.register(EchoTool())
    with pytest.raises(ValueError, match="重复注册"):
        reg.register(EchoTool())


def test_tool_is_abstract():
    class Incomplete(Tool):
        name = "bad"
        description = "缺 execute"
        parameters = {}

    with pytest.raises(TypeError):
        Incomplete()  # 没实现 execute，应无法实例化
