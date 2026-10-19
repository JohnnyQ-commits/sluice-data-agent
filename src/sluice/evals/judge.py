"""LLM-as-judge：按 rubric 逐项打分，引用产出原文佐证——引用不到 = 判分无效（防作弊）。

judge 只看产出与 rubric，不给被评对象的 prompt/轨迹（设计文档 §3 防作弊约束）。
judge 挂掉/输出不合法 → 该项判分无效（score=None），用例退回纯确定性断言，不误伤。
"""

from __future__ import annotations

import json
import re

from ..llm.types import Message, TextBlock

JUDGE_SYSTEM = (
    "你是评测裁判。对给定产出按评分维度逐项打 1-5 分，"
    "每一项必须引用产出原文作为佐证。只输出 JSON，不要输出其他内容。"
)

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub("", text)


def judge_output(client, answer: str, rubric: list[str]) -> list[dict]:
    """返回 [{item, score, quote, valid}]，顺序与 rubric 一致；valid=False 时 score=None。"""
    dims = "\n".join(f"{i}. {item}" for i, item in enumerate(rubric, 1))
    prompt = (
        f"待评产出：\n{answer}\n\n评分维度（每项 1-5 分）：\n{dims}\n\n"
        '输出 JSON：{"items": [{"item": "维度原文", "score": 4, "quote": "产出原文引用"}]}'
    )
    try:
        response = client.chat([Message("user", [TextBlock(prompt)])], system=JUDGE_SYSTEM)
    except Exception:  # noqa: BLE001 — judge 挂了降级为无语义分，不算用例失败
        return [{"item": item, "score": None, "quote": "", "valid": False} for item in rubric]

    items_raw: list = []
    m = re.search(r"\{.*\}", response.text, re.DOTALL)
    if m:
        try:
            items_raw = json.loads(m.group(0)).get("items", [])
        except json.JSONDecodeError:
            items_raw = []
    by_item = {
        it["item"]: it for it in items_raw if isinstance(it, dict) and it.get("item") in rubric
    }
    answer_norm = _normalize(answer)
    results = []
    for item in rubric:
        it = by_item.get(item, {})
        quote = str(it.get("quote", ""))
        score = it.get("score")
        valid = (
            isinstance(score, int)
            and not isinstance(score, bool)
            and 1 <= score <= 5
            and bool(quote)
            and _normalize(quote) in answer_norm
        )
        results.append(
            {"item": item, "score": score if valid else None, "quote": quote, "valid": valid}
        )
    return results
