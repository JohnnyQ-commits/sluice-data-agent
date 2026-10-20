import httpx
import pytest

from sluice.config.settings import Settings
from sluice.exceptions import LLMConnectionError, LLMHTTPError, LLMTimeoutError
from sluice.llm.client import LLMClient
from sluice.llm.types import Message, TextBlock, ToolDef


def make_settings(**kw):
    return Settings(llm_api_key="test-key", llm_max_retries=2, llm_timeout_seconds=5.0, **kw)


class FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


OK_TEXT = {
    "content": [{"type": "text", "text": "你好"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 5},
}
OK_TOOL = {
    "content": [{"type": "tool_use", "id": "tu_1", "name": "echo", "input": {"text": "hi"}}],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


def test_chat_builds_payload_and_parses_text(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return FakeResp(200, OK_TEXT)

    monkeypatch.setattr("sluice.llm.client.httpx.post", fake_post)
    client = LLMClient(make_settings())
    resp = client.chat(
        [Message("user", [TextBlock("打个招呼")])],
        system="你是一个测试助手",
        tools=[ToolDef("echo", "原样返回", {"type": "object", "properties": {}})],
    )

    assert resp.text == "你好" and resp.stop_reason == "end_turn"
    assert captured["url"].endswith("/v1/messages")
    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["headers"]["authorization"] == "Bearer test-key"  # 自建网关只认此头
    assert captured["json"]["system"] == "你是一个测试助手"
    assert captured["json"]["tools"][0]["name"] == "echo"
    assert captured["json"]["messages"][0]["content"] == [{"type": "text", "text": "打个招呼"}]


def test_chat_parses_tool_use(monkeypatch):
    monkeypatch.setattr("sluice.llm.client.httpx.post", lambda *a, **kw: FakeResp(200, OK_TOOL))
    resp = LLMClient(make_settings()).chat([Message("user", [TextBlock("hi")])])
    assert resp.tool_uses[0].id == "tu_1"
    assert resp.tool_uses[0].input == {"text": "hi"}


def test_chat_retries_on_5xx_then_succeeds(monkeypatch):
    calls = []

    def fake_post(*a, **kw):
        calls.append(1)
        return FakeResp(500, text="boom") if len(calls) == 1 else FakeResp(200, OK_TEXT)

    monkeypatch.setattr("sluice.llm.client.httpx.post", fake_post)
    monkeypatch.setattr("sluice.llm.client.time.sleep", lambda s: None)
    resp = LLMClient(make_settings()).chat([Message("user", [TextBlock("hi")])])
    assert resp.text == "你好" and len(calls) == 2


def test_chat_no_retry_on_400(monkeypatch):
    calls = []

    def fake_post(*a, **kw):
        calls.append(1)
        return FakeResp(400, text="bad request")

    monkeypatch.setattr("sluice.llm.client.httpx.post", fake_post)
    with pytest.raises(LLMHTTPError) as exc_info:
        LLMClient(make_settings()).chat([Message("user", [TextBlock("hi")])])
    assert exc_info.value.status_code == 400 and len(calls) == 1


def test_chat_timeout_raises_after_retries(monkeypatch):
    calls = []

    def fake_post(*a, **kw):
        calls.append(1)
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("sluice.llm.client.httpx.post", fake_post)
    monkeypatch.setattr("sluice.llm.client.time.sleep", lambda s: None)
    with pytest.raises(LLMTimeoutError):
        LLMClient(make_settings()).chat([Message("user", [TextBlock("hi")])])
    assert len(calls) == 3  # max_retries=2 → 初次 + 2 次重试


def test_chat_connect_error_raises_after_retries(monkeypatch):
    calls = []

    def fake_post(*a, **kw):
        calls.append(1)
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("sluice.llm.client.httpx.post", fake_post)
    monkeypatch.setattr("sluice.llm.client.time.sleep", lambda s: None)
    with pytest.raises(LLMConnectionError):
        LLMClient(make_settings()).chat([Message("user", [TextBlock("hi")])])
    assert len(calls) == 3


def test_chat_encodes_tool_blocks(monkeypatch):
    from sluice.llm.types import ToolResultBlock, ToolUseBlock

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(json=json)
        return FakeResp(200, OK_TEXT)

    monkeypatch.setattr("sluice.llm.client.httpx.post", fake_post)
    LLMClient(make_settings()).chat(
        [
            Message("user", [TextBlock("hi")]),
            Message(
                "assistant", [TextBlock("调用中"), ToolUseBlock("tu_1", "echo", {"text": "x"})]
            ),
            Message("user", [ToolResultBlock("tu_1", "x", is_error=True)]),
        ]
    )
    msgs = captured["json"]["messages"]
    assert msgs[1]["content"][1] == {
        "type": "tool_use",
        "id": "tu_1",
        "name": "echo",
        "input": {"text": "x"},
    }
    assert msgs[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "tu_1",
        "content": "x",
        "is_error": True,
    }


def test_chat_strips_trailing_slash_in_base_url(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url)
        return FakeResp(200, OK_TEXT)

    monkeypatch.setattr("sluice.llm.client.httpx.post", fake_post)
    LLMClient(make_settings(llm_base_url="https://host.example/")).chat(
        [Message("user", [TextBlock("hi")])]
    )
    assert captured["url"] == "https://host.example/v1/messages"
