"""会话持久化——一个会话一个 JSON 文件，临时文件 + os.replace 原子写，进程内锁防并发。

文件结构（data/conversations/<id>.json）：
    {"id": str, "title": str, "created_at": str, "updated_at": str,
     "messages": [{"user": str, "events": [ev...], "answer": str|None, "ts": str}, ...]}
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

_ID_RE = re.compile(r"[0-9a-f]{32}\Z")  # uuid4().hex——同时挡路径穿越
_TITLE_LEN = 24
_SNIPPET_WIN = 30  # 命中词前后各取的字符数（侧栏单行摘要）


def _now() -> str:
    return datetime.now().isoformat()  # 微秒精度：同秒内多会话排序不并列


def _match_span(text, query: str):
    """换行折叠成单行后忽略大小写检索，命中返回（展平文本, (起, 止)），未命中 None。

    在展平文本上检索保证摘要窗口切出来就是单行可直接展示的片段。
    """
    flat = " ".join(str(text).split())
    m = re.search(re.escape(query), flat, re.IGNORECASE)
    if m is None:
        return None
    return flat, m.span()


def _excerpt(flat: str, span) -> str:
    start, end = span
    left = max(0, start - _SNIPPET_WIN)
    right = min(len(flat), end + _SNIPPET_WIN)
    return ("…" if left else "") + flat[left:right] + ("…" if right < len(flat) else "")


class ConversationStore:
    def __init__(self, base_dir):
        self._base = Path(base_dir)
        self._lock = threading.Lock()

    def _path(self, conv_id: str) -> Path | None:
        if not _ID_RE.fullmatch(conv_id or ""):
            return None
        return self._base / f"{conv_id}.json"

    def _read(self, path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None  # 半写/损坏文件按不存在处理

    def _write(self, conv: dict) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        path = self._base / f"{conv['id']}.json"
        tmp = self._base / f"{conv['id']}.json.tmp"
        tmp.write_text(json.dumps(conv, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)  # 同目录原子替换，读侧永远看到完整文件

    def create(self) -> str:
        conv_id = uuid.uuid4().hex
        with self._lock:
            self._write(
                {
                    "id": conv_id,
                    "title": "",
                    "created_at": _now(),
                    "updated_at": _now(),
                    "messages": [],
                }
            )
        return conv_id

    def load(self, conv_id: str) -> dict | None:
        path = self._path(conv_id)
        if path is None:
            return None
        conv = self._read(path)
        return conv if conv and conv.get("id") == conv_id else None

    def delete(self, conv_id: str) -> bool:
        path = self._path(conv_id)
        if path is None:
            return False
        with self._lock:
            try:
                path.unlink()
            except OSError:
                return False
        return True

    def list(self) -> list[dict]:
        """按 updated_at 倒序的元数据列表（损坏文件跳过）。"""
        with self._lock:
            files = list(self._base.glob("*.json")) if self._base.is_dir() else []
        items = []
        for path in files:
            conv = self._read(path)
            if conv and conv.get("id"):
                items.append(
                    {
                        "id": conv["id"],
                        "title": conv.get("title") or "(未命名对话)",
                        "updated_at": conv.get("updated_at") or "",
                    }
                )
        items.sort(key=lambda c: c["updated_at"], reverse=True)
        return items

    def search(self, query: str) -> list[dict]:
        """标题 + 消息正文（用户问/最终答）子串检索，命中项附单行摘要。

        list() 的超集形状（多一个 snippet 字段），按 updated_at 倒序与 list 一致。
        """
        q = (query or "").strip()
        if not q:
            return []
        with self._lock:
            files = list(self._base.glob("*.json")) if self._base.is_dir() else []
        items = []
        for path in files:
            conv = self._read(path)
            if not (conv and conv.get("id")):
                continue
            title = conv.get("title") or "(未命名对话)"
            snippet = None
            hit = _match_span(title, q)
            if hit is not None:
                snippet = _excerpt(*hit)
            else:
                for msg in conv.get("messages") or []:
                    for field in ("user", "answer"):
                        hit = _match_span(msg.get(field) or "", q)
                        if hit is not None:
                            snippet = _excerpt(*hit)
                            break
                    if snippet is not None:
                        break
            if snippet is not None:
                items.append(
                    {
                        "id": conv["id"],
                        "title": title,
                        "updated_at": conv.get("updated_at") or "",
                        "snippet": snippet,
                    }
                )
        items.sort(key=lambda c: c["updated_at"], reverse=True)
        return items

    def append_turn(self, conv_id: str, user: str, events: list, answer: str | None) -> None:
        """每轮收尾增量落盘；id 非法直接忽略（调用方 id 恒来自 create/load，防御性兜底）。"""
        path = self._path(conv_id)
        if path is None:
            return
        with self._lock:
            conv = self._read(path) or {
                "id": conv_id,
                "title": "",
                "created_at": _now(),
                "updated_at": _now(),
                "messages": [],
            }
            conv["messages"].append(
                {"user": user, "events": events, "answer": answer, "ts": _now()}
            )
            if not conv["title"]:
                conv["title"] = user.strip().replace("\n", " ")[:_TITLE_LEN]
            conv["updated_at"] = _now()
            self._write(conv)
