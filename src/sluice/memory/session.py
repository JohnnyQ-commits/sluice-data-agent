"""会话记忆（时间维度）：user_id+session_id 隔离的轮次 JSONL 落盘。

路径布局：<base>/<user_id>/<session_id>.jsonl；每行 {"ts", "role", "text"}。

契约：调用方须保证 user_id/session_id 不含路径分隔符与 ".."（SessionStore 不做路径校验）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class SessionStore:
    def __init__(self, base_dir: str | Path):
        self._base = Path(base_dir)

    def _session_file(self, user_id: str, session_id: str) -> Path:
        d = self._base / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{session_id}.jsonl"

    def append(self, user_id: str, session_id: str, role: str, text: str) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "role": role, "text": text}
        with self._session_file(user_id, session_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def load(self, user_id: str, session_id: str) -> list[dict]:
        path = self._base / user_id / f"{session_id}.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def list_sessions(self, user_id: str) -> list[str]:
        d = self._base / user_id
        if not d.is_dir():
            return []
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.stem for p in files]
