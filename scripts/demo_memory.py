"""记忆层演示：HashEmbedder + FaissStore 语义召回 + 会话摘要升维（零 API key 可跑）。

用法: .venv/Scripts/python -X utf8 scripts/demo_memory.py
"""

from pathlib import Path

from sluice.memory.hooks import SessionMemory
from sluice.memory.session import SessionStore
from sluice.memory.store import FaissStore, HashEmbedder


def main() -> None:
    base = Path("data/memory-demo")
    vectors = FaissStore(embed=HashEmbedder(dim=64), dim=64, path=str(base / "vectors"))
    # 演示用假摘要（零 API key）；真网关时换 client.chat 包一层
    fake_summarize = lambda text: f"对话摘要：{text[:60]}…"
    memory = SessionMemory(SessionStore(base / "sessions"), vectors, fake_summarize, max_turns=2)

    print("== 会话 1：三轮对话触发摘要升维 ==")
    for role, text in [
        ("user", "查一下上周销售额环比"),
        ("assistant", "上周销售额环比上升 15%"),
        ("user", "好的，再看信贷通过率"),
    ]:
        memory.record("demo", "s1", role, text)

    print("== 会话 2：召回会话 1 的长期记忆 ==")
    # 第一条查询与会话 1 文本整 token 重合（HashEmbedder 词面匹配）→ 命中；
    # 第二条零重合 → score<=0 被 recall 过滤 → 返回（无）
    for text in ["查一下上周销售额环比", "完全不相关的问题"]:
        got = memory.recall("demo", "s2", text)
        print(f"  召回[{text}] →", got.replace("\n", " / ") or "（无）")


if __name__ == "__main__":
    main()
