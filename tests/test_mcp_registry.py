"""MCP registry 测试：发现 → 启动 → Tool 适配 → 注册进 ToolRegistry（真实 mini server 子进程）。"""

import json
import sys
import time
from pathlib import Path
from typing import ClassVar

import pytest

from sluice.exceptions import MCPError
from sluice.mcp.registry import discover, load_mcp_tools
from sluice.tools.base import Tool, ToolResult
from sluice.tools.registry import ToolRegistry

MINI_SERVER = str(Path(__file__).parent / "fixtures" / "mini_mcp_server.py")
HUNG_SERVER = str(Path(__file__).parent / "fixtures" / "hung_mcp_server.py")


def _config(tmp_path):
    cfg = tmp_path / ".mcp.json"
    cfg.write_text(
        json.dumps({"mcpServers": {"mini": {"command": sys.executable, "args": [MINI_SERVER]}}}),
        encoding="utf-8",
    )
    return cfg


def test_discover_reads_config(tmp_path):
    servers = discover(_config(tmp_path))
    assert len(servers) == 1 and servers[0].name == "mini"


def test_load_mcp_tools_end_to_end(tmp_path):
    servers = load_mcp_tools(_config(tmp_path))
    try:
        tools = [t for s in servers for t in s.tools]
        assert {t.name for t in tools} == {"mcp__mini__echo", "mcp__mini__add"}
        echo = next(t for t in tools if t.name.endswith("echo"))
        result = echo.execute({"text": "你好"})
        assert result.content == "你好" and not result.is_error
    finally:
        for s in servers:
            s.close()


def test_tool_error_returns_tool_result_not_raise(tmp_path):
    """服务端拒调（协议 error）→ McpTool 转 ToolResult(is_error) 不炸调用方。"""
    servers = load_mcp_tools(_config(tmp_path))
    try:
        tools = [t for s in servers for t in s.tools]
        bad = next(t for t in tools if t.name.endswith("add"))
        bad._raw_name = "nope"  # 白盒：模拟服务端不认识的工具
        r = bad.execute({"a": 1, "b": 2})
        assert r.is_error and "nope" in r.content
    finally:
        for s in servers:
            s.close()


def test_registry_accepts_mcp_tools(tmp_path):
    servers = load_mcp_tools(_config(tmp_path))
    try:
        registry = ToolRegistry()
        for s in servers:
            for t in s.tools:
                registry.register(t)
        assert registry.get("mcp__mini__echo") is not None
    finally:
        for s in servers:
            s.close()


def test_load_mcp_tools_skips_broken_server(tmp_path, caplog):
    """kill switch：单个 server 起不来只警告跳过，不影响其他 server。"""
    cfg = tmp_path / ".mcp.json"
    cfg.write_text(
        json.dumps({
            "mcpServers": {
                "broken": {"command": sys.executable, "args": ["-c", "import sys; sys.exit(1)"]},
                "mini": {"command": sys.executable, "args": [MINI_SERVER]},
            }
        }),
        encoding="utf-8",
    )
    servers = load_mcp_tools(cfg)
    try:
        assert [s.name for s in servers] == ["mini"]
        assert any("broken" in rec.message or "broken" in str(rec) for rec in caplog.records)
    finally:
        for s in servers:
            s.close()


def test_load_mcp_tools_skips_nonexistent_command(tmp_path):
    """kill switch 漏网修补：命令不存在时 Popen 抛原生 FileNotFoundError（OSError），
    transport 归入 MCPTransportError 后 load_mcp_tools 同样警告跳过，不炸整个 load。"""
    cfg = tmp_path / ".mcp.json"
    cfg.write_text(
        json.dumps({
            "mcpServers": {
                "ghost": {"command": "definitely-not-exist-cmd", "args": []},
                "mini": {"command": sys.executable, "args": [MINI_SERVER]},
            }
        }),
        encoding="utf-8",
    )
    servers = load_mcp_tools(cfg)
    try:
        assert [s.name for s in servers] == ["mini"]
    finally:
        for s in servers:
            s.close()


def test_tool_description_is_real_utf8(tmp_path):
    """编码契约：description 中文必须真解码（mini server 已锁 stdin/stdout UTF-8），
    防 GBK 写端 + GBK 读端字节对消的假绿。"""
    servers = load_mcp_tools(_config(tmp_path))
    try:
        echo = next(t for s in servers for t in s.tools if t.name.endswith("echo"))
        assert echo.description == "回显输入"
    finally:
        for s in servers:
            s.close()


def test_load_mcp_tools_closes_hung_server(tmp_path):
    """孤儿进程泄漏回归：挂起 server 握手超时被跳过后必须被 close——
    hung server 在 stdin EOF 时写 marker，marker 出现即证明 close 已收掉管道（进程随之退出）。"""
    cfg = tmp_path / ".mcp.json"
    cfg.write_text(
        json.dumps({
            "mcpServers": {
                "hung": {
                    "command": sys.executable,
                    "args": [HUNG_SERVER, str(tmp_path / "hung.marker")],
                    "timeout": 1.0,
                },
                "mini": {"command": sys.executable, "args": [MINI_SERVER]},
            }
        }),
        encoding="utf-8",
    )
    servers = load_mcp_tools(cfg)
    try:
        assert [s.name for s in servers] == ["mini"]
        marker = tmp_path / "hung.closed"
        deadline = time.time() + 5
        while time.time() < deadline and not marker.exists():
            time.sleep(0.1)
        assert marker.exists(), "挂起 server 未被 close（stdin 未收 EOF），存在孤儿进程泄漏"
    finally:
        for s in servers:
            s.close()


class _FakeBuiltin(Tool):
    """内建占位工具：名字与 mini server 的 echo 注册名撞车，验证内建优先。"""

    name = "mcp__mini__echo"
    description = "内建占位"
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult("builtin")


def test_registry_builtin_wins_over_mcp_tool(tmp_path):
    """内建优先级：同名冲突时 ValueError 且 registry 保留内建实例（锁住 web 接线
    ValueError 去重的方向性，防未来误改成 MCP 覆盖式注册）。"""
    servers = load_mcp_tools(_config(tmp_path))
    try:
        registry = ToolRegistry()
        builtin = _FakeBuiltin()
        registry.register(builtin)
        mcp_echo = next(t for s in servers for t in s.tools if t.name.endswith("echo"))
        with pytest.raises(ValueError):
            registry.register(mcp_echo)
        assert registry.get("mcp__mini__echo") is builtin
    finally:
        for s in servers:
            s.close()


def test_to_def_shape_for_react_loop(tmp_path):
    """McpTool 能出 ToolDef（ReAct 循环消费的形态）。"""
    servers = load_mcp_tools(_config(tmp_path))
    try:
        echo = next(t for s in servers for t in s.tools if t.name.endswith("echo"))
        d = echo.to_def()
        assert d.name == "mcp__mini__echo" and d.parameters.get("type") == "object"
    finally:
        for s in servers:
            s.close()


def test_discover_malformed_json_raises_mcp_error_with_path(tmp_path):
    """畸形 JSON → MCPError（不再抛裸 json.JSONDecodeError），消息含配置文件路径与原因。"""
    cfg = tmp_path / ".mcp.json"
    cfg.write_text('{"mcpServers": {', encoding="utf-8")
    with pytest.raises(MCPError) as exc_info:
        discover(cfg)
    msg = str(exc_info.value)
    assert str(cfg) in msg and "配置文件无法解析" in msg


def test_discover_missing_command_raises_mcp_error_with_server_name(tmp_path):
    """server 条目缺 command 键 → MCPError 含 server 名（不再 KeyError）。"""
    cfg = tmp_path / ".mcp.json"
    cfg.write_text(
        json.dumps({"mcpServers": {"half": {"args": ["x"]}}}),
        encoding="utf-8",
    )
    with pytest.raises(MCPError) as exc_info:
        discover(cfg)
    assert "half" in str(exc_info.value)


def test_mcp_json_example_parses():
    """公共 server 演示配置（.mcp.json.example）必须能被 discover 正常解析出 ≥1 个 server，
    防 README/示例与 registry schema 漂移。"""
    example = Path(__file__).resolve().parents[1] / ".mcp.json.example"
    servers = discover(example)
    assert len(servers) >= 1
    assert servers[0].name == "filesystem"
    assert servers[0]._command[0] == "cmd"
