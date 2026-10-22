"""MCP registry 测试：发现 → 启动 → Tool 适配 → 注册进 ToolRegistry（真实 mini server 子进程）。"""

import json
import sys
from pathlib import Path

from sluice.mcp.registry import discover, load_mcp_tools
from sluice.tools.registry import ToolRegistry

MINI_SERVER = str(Path(__file__).parent / "fixtures" / "mini_mcp_server.py")


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
