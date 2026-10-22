"""MCP stdio 传输测试：真实子进程端到端（mini server），超时/协议错误/进程死亡分类。"""

import sys
from pathlib import Path

import pytest

from sluice.exceptions import MCPError, MCPTimeoutError, MCPTransportError
from sluice.mcp.transport import StdioTransport

MINI_SERVER = str(Path(__file__).parent / "fixtures" / "mini_mcp_server.py")


def _transport(timeout=10.0):
    t = StdioTransport([sys.executable, MINI_SERVER], timeout=timeout)
    t.start()
    return t


def test_initialize_and_tools_list_roundtrip():
    t = _transport()
    try:
        resp = t.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
        assert resp["result"]["serverInfo"]["name"] == "mini"
        tools = t.request("tools/list", {})
        assert [x["name"] for x in tools["result"]["tools"]] == ["echo", "add"]
    finally:
        t.close()


def test_tools_call_add():
    t = _transport()
    try:
        resp = t.request("tools/call", {"name": "add", "arguments": {"a": 2, "b": 40}})
        assert resp["result"]["content"][0]["text"] == "42"
    finally:
        t.close()


def test_request_with_chinese_params_roundtrip():
    """中文参数端到端（编码契约：Popen 两端显式 utf-8）。"""
    t = _transport()
    try:
        resp = t.request("tools/call", {"name": "echo", "arguments": {"text": "你好世界"}})
        assert resp["result"]["content"][0]["text"] == "你好世界"
    finally:
        t.close()


def test_unknown_tool_raises_protocol_error():
    t = _transport()
    try:
        with pytest.raises(MCPError, match="-32602"):
            t.request("tools/call", {"name": "nope", "arguments": {}})
    finally:
        t.close()


def test_dead_process_raises_transport_error():
    t = _transport()
    t.close()
    with pytest.raises(MCPTransportError):
        t.request("tools/list", {})


def test_notify_does_not_block():
    """通知无响应——notify 只写不读，立即返回。"""
    t = _transport()
    try:
        t.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
        t.notify("notifications/initialized", {})  # 不应阻塞/超时
        tools = t.request("tools/list", {})  # 通知后管道仍正常
        assert tools["result"]["tools"]
    finally:
        t.close()


def test_concurrent_requests_no_response_stealing():
    """两线程并发 request——内部互斥串行化，响应不互偷（I-1 线程契约）。"""
    import concurrent.futures

    t = _transport()
    try:
        t.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(t.request, "tools/call", {"name": "add", "arguments": {"a": 1, "b": 2}})
            f2 = ex.submit(t.request, "tools/call", {"name": "add", "arguments": {"a": 10, "b": 20}})
            assert f1.result(timeout=10)["result"]["content"][0]["text"] == "3"
            assert f2.result(timeout=10)["result"]["content"][0]["text"] == "30"
    finally:
        t.close()


def test_timeout_raises_timeout_error():
    """对永不响应的进程请求 → 超时分类。"""
    sleeper = StdioTransport([sys.executable, "-c", "import sys,time\nfor l in sys.stdin: time.sleep(3600)"], timeout=1.0)
    sleeper.start()
    try:
        with pytest.raises(MCPTimeoutError):
            sleeper.request("tools/list", {})
    finally:
        sleeper.close()


def test_ensure_alive_restarts_and_reports():
    t = _transport()
    try:
        t.close()  # 杀进程
        assert t.ensure_alive() is True  # 已重启，需重新 initialize
        resp = t.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
        assert resp["result"]["serverInfo"]["name"] == "mini"
    finally:
        t.close()


def test_ensure_alive_max_restarts_exhausted():
    t = _transport()
    t._max_restarts = 0  # 收紧上限（白盒，仅本测试）
    try:
        t.close()
        with pytest.raises(MCPTransportError, match="重启"):
            t.ensure_alive()
    finally:
        t.close()
