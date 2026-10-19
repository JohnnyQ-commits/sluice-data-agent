import json
import threading
from typing import ClassVar

import pytest

from sluice.config.settings import Settings
from sluice.llm.types import LLMResponse, TextBlock, ToolResultBlock, ToolUseBlock
from sluice.runtime.loop import ReActRuntime
from sluice.tools.base import Tool, ToolResult
from sluice.tools.registry import ToolRegistry
from sluice.web.app import _stream_idle_timeout, create_app


class OkTool(Tool):
    name = "ok"
    description = "ok"
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}

    def execute(self, args):
        return ToolResult("done")


class SlowFakeClient:
    """第一轮阻塞等 release，放行后调工具；第二轮出最终回答。"""

    def __init__(self):
        self.release = threading.Event()

    def chat(self, messages, system=None, tools=None):
        if (
            messages[-1].role == "user"
            and messages[-1].content
            and isinstance(messages[-1].content[0], ToolResultBlock)
        ):
            return LLMResponse(content=[TextBlock("完成")], stop_reason="end_turn", usage={})
        self.release.wait(timeout=5)
        return LLMResponse(
            content=[ToolUseBlock("tu_1", "ok", {})], stop_reason="tool_use", usage={}
        )


@pytest.fixture
def app():
    fake = SlowFakeClient()
    registry = ToolRegistry()
    registry.register(OkTool())
    app = create_app(runtime=ReActRuntime(fake, registry), skills_dir=None)
    app.config["TESTING"] = True
    app.config["RELEASE"] = fake.release
    return app


def test_chat_returns_run_id(app):
    client = app.test_client()
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 200
    assert "run_id" in resp.get_json()


def test_chat_requires_message(app):
    assert app.test_client().post("/api/chat", json={}).status_code == 400


def test_stream_unknown_run_id_404(app):
    assert app.test_client().get("/api/stream/unknown").status_code == 404


def test_stream_delivers_events_then_end(app):
    client = app.test_client()
    run_id = client.post("/api/chat", json={"message": "hi"}).get_json()["run_id"]
    app.config["RELEASE"].set()  # 放行第一轮（worker 线程此刻阻塞在 chat）
    resp = client.get(f"/api/stream/{run_id}")  # 同步阻塞读流，直到 final+end
    body = resp.get_data(as_text=True)
    events = [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ") and line[len("data: ") :] != "{}"
    ]
    assert [e["type"] for e in events] == [
        "round_start",
        "tool_call",
        "observation",
        "round_start",
        "final",
    ]
    assert events[-1]["answer"] == "完成"
    assert events[2]["observation"] == "done"
    assert events[2]["is_error"] is False
    assert "event: end" in body
    assert resp.headers["Cache-Control"] == "no-cache"
    assert resp.headers["X-Accel-Buffering"] == "no-cache"


def test_stream_idle_timeout_derives_from_settings():
    """空闲超时必须盖过 LLM 重试链的最坏静默（(重试数+1)×超时 + 余量），不许魔法数裸放。"""
    assert _stream_idle_timeout(Settings(llm_timeout_seconds=30)) == 180.0
    defaults = Settings()
    expected = (defaults.llm_max_retries + 1) * defaults.llm_timeout_seconds + 60
    assert _stream_idle_timeout(defaults) == expected


def test_stream_emits_timeout_end_when_run_silent(monkeypatch):
    """长静默超时路径：q.get 超时后发 end 终结事件，流不悬挂。"""
    monkeypatch.setattr("sluice.web.app._stream_idle_timeout", lambda settings: 0.05)
    app = create_app(runtime=ReActRuntime(SlowFakeClient(), ToolRegistry()), skills_dir=None)
    app.config["TESTING"] = True
    run_id = app.test_client().post("/api/chat", json={"message": "hi"}).get_json()["run_id"]
    resp = app.test_client().get(f"/api/stream/{run_id}")
    body = resp.get_data(as_text=True)
    assert 'event: end\ndata: {"type": "timeout"}' in body


def test_skills_endpoint_lists_packages():
    app = create_app(runtime=ReActRuntime(SlowFakeClient(), ToolRegistry()), skills_dir=None)
    resp = app.test_client().get("/api/skills")
    skills = resp.get_json()["skills"]
    assert isinstance(skills, list)
