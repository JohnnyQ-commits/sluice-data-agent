"""会话长期记忆测试：record 落盘 + 超 N 轮摘要向量化 + recall 注入片段。"""

from sluice.memory.hooks import SessionMemory
from sluice.memory.session import SessionStore
from sluice.memory.store import FaissStore, HashEmbedder


class StubSummarizer:
    def __init__(self):
        self.calls = 0

    def __call__(self, text: str) -> str:
        self.calls += 1
        return f"摘要:{text[:20]}"


def _memory(tmp_path, max_turns=2, summarizer=None):
    summarizer = summarizer or StubSummarizer()
    vectors = FaissStore(embed=HashEmbedder(dim=64), dim=64, path=None)
    return (
        SessionMemory(
            sessions=SessionStore(tmp_path),
            vectors=vectors,
            summarize=summarizer,
            max_turns=max_turns,
        ),
        summarizer,
    )


def test_record_appends_and_summarizes_after_threshold(tmp_path):
    mem, summarizer = _memory(tmp_path, max_turns=2)
    mem.record("u1", "s1", "user", "问题一")
    assert summarizer.calls == 0
    mem.record("u1", "s1", "user", "回答一")
    assert summarizer.calls == 0
    mem.record("u1", "s1", "user", "问题二")
    assert summarizer.calls == 1  # 第 3 轮（超过 max_turns=2）触发摘要
    mem.record("u1", "s1", "assistant", "回答二")


def test_recall_empty_when_no_long_term(tmp_path):
    mem, _ = _memory(tmp_path)
    assert mem.recall("u1", "s1", "任何问题") == ""


def test_recall_returns_summary_after_promotion(tmp_path):
    mem, _ = _memory(tmp_path, max_turns=1)
    mem.record("u1", "s1", "user", "上周销售额环比上升多少")
    mem.record("u1", "s1", "assistant", "上升百分之十五")
    # 查询串与记录文本相同保证 HashEmbedder 整 token 命中；"摘要" 来自 StubSummarizer 前缀
    got = mem.recall("u1", "s1", "上周销售额环比上升多少")
    assert "摘要" in got


def test_promotion_clears_session_turns(tmp_path):
    mem, summarizer = _memory(tmp_path, max_turns=1)
    mem.record("u1", "s1", "user", "问题一")
    mem.record("u1", "s1", "assistant", "回答一")
    assert summarizer.calls == 1
    # 升维后轮次清空：新会话从零计数，不会重复升维
    assert SessionStore(tmp_path).load("u1", "s1") == []
    mem.record("u1", "s1", "user", "问题二")
    assert summarizer.calls == 1


def test_recall_ignores_other_users(tmp_path):
    mem, _ = _memory(tmp_path, max_turns=1)
    mem.record("u1", "s1", "user", "上周销售额环比上升多少")
    mem.record("u1", "s1", "assistant", "上升百分之十五")
    # 向量库是全局的，召回按 meta.user_id 隔离
    assert mem.recall("u2", "s1", "上周销售额环比上升多少") == ""
