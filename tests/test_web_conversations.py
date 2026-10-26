"""Web 会话持久化：ConversationStore 原子写 + 四端点（列表/新建/读回/删除）+ chat 接线。"""

import json
import threading
from typing import ClassVar

import pytest

from sluice.llm.types import LLMResponse, TextBlock
from sluice.runtime.loop import ReActRuntime
from sluice.tools.base import Tool, ToolResult
from sluice.tools.registry import ToolRegistry
from sluice.web.app import create_app
from sluice.web.conversations import ConversationStore


class EchoClient:
    """零轮工具调用：直接给最终回答，走通 chat → 写盘链路。"""

    def chat(self, messages, system=None, tools=None):
        return LLMResponse(content=[TextBlock("完成")], stop_reason="end_turn", usage={})


@pytest.fixture
def conv_app(tmp_path):
    app = create_app(
        runtime=ReActRuntime(EchoClient(), ToolRegistry()),
        skills_dir=None,
        conversations_dir=tmp_path / "convs",
    )
    app.config["TESTING"] = True
    return app


def _finish(client, run_id):
    body = client.get(f"/api/stream/{run_id}").get_data(as_text=True)
    assert "event: end" in body  # 流读完（final 已落）


def test_store_create_load_list_delete(tmp_path):
    store = ConversationStore(tmp_path)
    conv_id = store.create()
    assert conv_id
    conv = store.load(conv_id)
    assert conv["id"] == conv_id
    assert conv["messages"] == []
    assert store.list()[0]["id"] == conv_id
    store.delete(conv_id)
    assert store.load(conv_id) is None
    assert store.list() == []


def test_store_rejects_bad_id(tmp_path):
    """id 只认 32 位 hex——挡路径穿越（../ 等）。"""
    store = ConversationStore(tmp_path)
    assert store.load("../etc/passwd") is None
    assert store.load("a" * 31) is None
    assert store.delete("../x") is False


def test_store_append_turn_writes_title_and_timestamp(tmp_path):
    store = ConversationStore(tmp_path)
    conv_id = store.create()
    store.append_turn(conv_id, "帮我生成一个超过二十四个字的用户提问用于验证标题截断效果", [], "答")
    conv = store.load(conv_id)
    assert len(conv["messages"]) == 1
    assert conv["messages"][0]["user"].startswith("帮我生成")
    assert conv["messages"][0]["answer"] == "答"
    assert len(conv["title"]) == 24  # 首条用户消息截断 24 字
    assert conv["updated_at"]


def test_store_append_turn_second_turn_keeps_first_title(tmp_path):
    store = ConversationStore(tmp_path)
    conv_id = store.create()
    store.append_turn(conv_id, "第一问", [], "答一")
    store.append_turn(conv_id, "第二问", [], "答二")
    conv = store.load(conv_id)
    assert [t["user"] for t in conv["messages"]] == ["第一问", "第二问"]
    assert conv["title"] == "第一问"


def test_store_concurrent_appends_atomic(tmp_path):
    """两线程并发 append：锁 + 临时文件 os.replace，文件始终是合法 JSON。"""
    store = ConversationStore(tmp_path)
    conv_id = store.create()

    def worker():
        for _ in range(5):
            store.append_turn(conv_id, "问", [], "答")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    conv = store.load(conv_id)
    assert conv is not None
    assert len(conv["messages"]) == 10  # 无丢更新
    leftovers = [p for p in tmp_path.iterdir() if p.name != f"{conv_id}.json"]
    assert leftovers == []  # 无临时文件残留


def test_store_list_sorted_by_updated_at_desc(tmp_path):
    import time

    store = ConversationStore(tmp_path)
    a = store.create()
    store.append_turn(a, "甲", [], "答")
    time.sleep(0.02)
    b = store.create()
    store.append_turn(b, "乙", [], "答")
    ids = [c["id"] for c in store.list()]
    assert ids.index(b) < ids.index(a)  # 新更新的在前


# ---------------- 端点接线 ----------------


def test_endpoints_crud_roundtrip(conv_app):
    client = conv_app.test_client()
    conv_id = client.post("/api/conversations").get_json()["id"]
    resp = client.get("/api/conversations")
    assert resp.status_code == 200
    assert [c["id"] for c in resp.get_json()["conversations"]] == [conv_id]
    assert set(resp.get_json()["conversations"][0]) == {"id", "title", "updated_at"}
    detail = client.get(f"/api/conversations/{conv_id}").get_json()
    assert detail["id"] == conv_id and detail["messages"] == []
    assert client.delete(f"/api/conversations/{conv_id}").status_code == 200
    assert client.get(f"/api/conversations/{conv_id}").status_code == 404
    assert client.delete(f"/api/conversations/{conv_id}").status_code == 404


def test_get_unknown_conversation_404(conv_app):
    assert conv_app.test_client().get("/api/conversations/" + "0" * 32).status_code == 404


def test_chat_without_conversation_id_creates_and_persists(conv_app, tmp_path):
    client = conv_app.test_client()
    resp = client.post("/api/chat", json={"message": "你好"})
    assert resp.status_code == 200
    conv_id = resp.get_json()["conversation_id"]
    assert conv_id
    _finish(client, resp.get_json()["run_id"])
    conv = client.get(f"/api/conversations/{conv_id}").get_json()
    assert conv["title"] == "你好"
    turns = conv["messages"]
    assert len(turns) == 1
    assert turns[0]["user"] == "你好"
    assert turns[0]["answer"] == "完成"
    assert turns[0]["events"][0]["type"] == "session_init"
    assert turns[0]["events"][1]["type"] == "round_start"
    assert turns[0]["events"][-1]["type"] != "final"  # final 不进轨迹（answer 单独存）
    # 落盘文件可独立解析（原子写产物）
    on_disk = json.loads((tmp_path / "convs" / f"{conv_id}.json").read_text(encoding="utf-8"))
    assert on_disk["messages"][0]["answer"] == "完成"


def test_chat_with_conversation_id_appends_turn(conv_app):
    client = conv_app.test_client()
    conv_id = client.post("/api/conversations").get_json()["id"]
    resp = client.post("/api/chat", json={"message": "第一问", "conversation_id": conv_id})
    assert resp.get_json()["conversation_id"] == conv_id
    _finish(client, resp.get_json()["run_id"])
    run2 = client.post(
        "/api/chat", json={"message": "第二问", "conversation_id": conv_id}
    ).get_json()["run_id"]
    _finish(client, run2)
    run3 = client.post(
        "/api/chat", json={"message": "第三问", "conversation_id": conv_id}
    ).get_json()["run_id"]
    _finish(client, run3)
    conv = client.get(f"/api/conversations/{conv_id}").get_json()
    assert [t["user"] for t in conv["messages"]] == ["第一问", "第二问", "第三问"]


def test_chat_with_unknown_conversation_id_creates_new(conv_app):
    """传入已删除/未知 id：不 404 不复用，新建并返回新 id。"""
    client = conv_app.test_client()
    resp = client.post("/api/chat", json={"message": "你好", "conversation_id": "0" * 32})
    new_id = resp.get_json()["conversation_id"]
    assert new_id != "0" * 32
    _finish(client, resp.get_json()["run_id"])
    assert client.get(f"/api/conversations/{new_id}").status_code == 200


def test_chat_error_event_still_persists_turn(tmp_path):
    """error 收尾（无 answer）也留痕：轨迹 + answer=None 落盘，列表标题仍生成。"""

    class BoomClient:
        def chat(self, messages, system=None, tools=None):
            raise RuntimeError("网关炸了")

    app = create_app(
        runtime=ReActRuntime(BoomClient(), ToolRegistry()),
        skills_dir=None,
        conversations_dir=tmp_path / "convs",
    )
    app.config["TESTING"] = True
    client = app.test_client()
    resp = client.post("/api/chat", json={"message": "触发报错"})
    conv_id = resp.get_json()["conversation_id"]
    _finish(client, resp.get_json()["run_id"])
    conv = client.get(f"/api/conversations/{conv_id}").get_json()
    assert conv["messages"][0]["user"] == "触发报错"
    assert conv["messages"][0]["answer"] is None
    assert any(e["type"] == "error" for e in conv["messages"][0]["events"])
    assert client.get("/api/conversations").get_json()["conversations"][0]["title"] == "触发报错"


# ---------------- session_init 开场事件（可观测性） ----------------


class DummyTool(Tool):
    name = "dummy"
    description = "测试工具"
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}

    def execute(self, args):
        return ToolResult("ok")


def test_stream_first_event_is_session_init():
    """stream 首个事件是 session_init：字段完整，tools 与 registry 全量一致。"""
    reg = ToolRegistry()
    reg.register(DummyTool())
    app = create_app(
        runtime=ReActRuntime(EchoClient(), reg),
        skills_dir=None,
        conversations_dir=None,
    )
    app.config["TESTING"] = True
    client = app.test_client()
    resp = client.post("/api/chat", json={"message": "你好"})
    body = client.get(f"/api/stream/{resp.get_json()['run_id']}").get_data(as_text=True)
    first = json.loads(body.split("data: ", 2)[1].split("\n")[0])
    assert first["type"] == "session_init"
    assert first["model"]  # settings 提供的模型名非空
    assert first["skills"] == []  # skills_dir=None → 无 Skill
    assert [t["name"] for t in first["tools"]] == ["dummy"]
    assert first["tools"][0]["description"] == "测试工具"
    assert first["guardrails"] is False  # 裸 runtime 未挂门禁链
    assert first["max_rounds"] == 10  # ReActRuntime 默认循环上限


def test_session_init_persisted_in_turn(conv_app):
    """session_init 进持久化轮次：回放历史会话时可见。"""
    client = conv_app.test_client()
    resp = client.post("/api/chat", json={"message": "开场快照"})
    conv_id = resp.get_json()["conversation_id"]
    _finish(client, resp.get_json()["run_id"])
    conv = client.get(f"/api/conversations/{conv_id}").get_json()
    events = conv["messages"][0]["events"]
    assert events[0]["type"] == "session_init"
    assert events[0]["max_rounds"] == 10
    assert isinstance(events[0]["tools"], list)
