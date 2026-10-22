"""记忆落点接线：Skill 语义召回 + 会话长期记忆（默认关闭，settings.memory_enabled 才生效）。

两个落点：
- select_skills：skill 数超过阈值后按用户输入召回 top-k，替换 build_system 的全量注入；
- SessionMemory：轮次落盘（SessionStore），超 max_turns 整段摘要升入向量库，新输入按需召回。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from .session import SessionStore
from .store import VectorStore

if TYPE_CHECKING:
    from ..skills.loader import SkillPackage

logger = logging.getLogger(__name__)


def build_skill_store(store: VectorStore, packages: list[SkillPackage]) -> None:
    """SKILL.md 描述向量化入索引（meta 带 skill 名）。"""
    texts = [f"{p.name} {p.description}" for p in packages]
    store.upsert(texts, metas=[{"skill": p.name} for p in packages])


def select_skills(
    packages: list[SkillPackage],
    user_input: str,
    store: VectorStore,
    threshold: int,
    k: int = 3,
) -> list[SkillPackage] | None:
    """超过 threshold 才召回 top-k；以下返回 None（调用方维持全量，现状行为零变化）。"""
    if len(packages) <= threshold:
        return None
    hits = store.search(user_input, k=k)
    by_name = {p.name: p for p in packages}
    picked = [by_name[h.meta["skill"]] for h in hits if h.meta.get("skill") in by_name]
    return picked or None


class SessionMemory:
    """会话长期记忆：轮次落盘；超 max_turns 后整段摘要升入向量库；新输入按需召回。"""

    def __init__(
        self,
        sessions: SessionStore,
        vectors: VectorStore,
        summarize: Callable[[str], str],
        max_turns: int = 10,
    ):
        self._sessions = sessions
        self._vectors = vectors
        self._summarize = summarize
        self._max_turns = max_turns

    def record(self, user_id: str, session_id: str, role: str, text: str) -> None:
        self._sessions.append(user_id, session_id, role, text)
        turns = self._sessions.load(user_id, session_id)
        if len(turns) > self._max_turns:
            self._promote(user_id, session_id, turns)

    def _promote(self, user_id: str, session_id: str, turns: list[dict]) -> None:
        """超阈值：整段会话摘要 → 向量库；清空该会话轮次（已升维）。"""
        digest = "\n".join(f"{t['role']}: {t['text']}" for t in turns)
        summary = self._summarize(digest)
        self._vectors.upsert(
            [summary], metas=[{"user_id": user_id, "session_id": session_id, "kind": "summary"}]
        )
        self._sessions.clear(user_id, session_id)
        logger.info("会话 %s/%s 升入长期记忆（%d 轮）", user_id, session_id, len(turns))

    def recall(self, user_id: str, session_id: str, user_input: str, k: int = 3) -> str:
        """召回该用户长期记忆中与输入最相关的摘要片段；无命中返回空串（调用方跳过拼接）。

        score <= 0 一并过滤：HashEmbedder 词面零重合时内积恰为 0，不滤会把不相关摘要
        当命中返回（GatewayEmbedder 的负相似度同理不该召回）。
        """
        hits = self._vectors.search(user_input, k=k)
        pieces = [h.text for h in hits if h.meta.get("user_id") == user_id and h.score > 0]
        return "\n---\n".join(pieces)
