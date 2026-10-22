"""会话记忆测试：user_id+session_id 隔离的 JSONL 轮次存取。"""

from sluice.memory.session import SessionStore


def test_append_and_load_roundtrip(tmp_path):
    store = SessionStore(tmp_path)
    store.append("u1", "s1", "user", "查一下上周销量")
    store.append("u1", "s1", "assistant", "上周销量 1060")
    turns = store.load("u1", "s1")
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["text"] == "查一下上周销量"
    assert "ts" in turns[0]  # ISO 时间戳


def test_sessions_are_isolated(tmp_path):
    store = SessionStore(tmp_path)
    store.append("u1", "s1", "user", "a")
    store.append("u1", "s2", "user", "b")
    store.append("u2", "s1", "user", "c")
    assert len(store.load("u1", "s1")) == 1
    assert store.load("u1", "s1")[0]["text"] == "a"
    assert store.load("u2", "s1")[0]["text"] == "c"


def test_list_sessions_newest_first(tmp_path):
    import time

    store = SessionStore(tmp_path)
    store.append("u1", "old", "user", "x")
    time.sleep(0.05)
    store.append("u1", "new", "user", "y")
    assert store.list_sessions("u1") == ["new", "old"]


def test_load_missing_session_returns_empty(tmp_path):
    assert SessionStore(tmp_path).load("u1", "none") == []
