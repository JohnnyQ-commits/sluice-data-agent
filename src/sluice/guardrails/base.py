"""Guardrails 基础件：拦截事件契约 + 审计落盘。

设计原则（设计文档 §4）：LLM 输出一律不可信，进执行路径前先过确定性校验；
guardrails 只做确定性判断（在册/合法/齐全），不做语义质量判断——那是 evals 的事。
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def guardrail_event(gate: str, action: str, reason: str, round_no: int, **detail) -> dict:
    """拦截事件：与 ReAct 事件流同构（type=guardrail），SSE 原样透传给前端。

    gate: input | behavior | output；action: blocked | repaired | degraded。
    passed 不产事件（每个工具调用都发会淹没轨迹，拦截才可见）。
    """
    event = {
        "type": "guardrail",
        "gate": gate,
        "action": action,
        "round": round_no,
        "reason": reason,
    }
    if detail:
        event["detail"] = detail
    return event


class AuditLog:
    """被拒/被改产出的审计副本（JSONL 追加写，一行一条）。"""

    def __init__(self, audit_dir: str | Path):
        self._path = Path(audit_dir) / "audit.jsonl"

    def append(self, entry: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        entry = {**entry, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
