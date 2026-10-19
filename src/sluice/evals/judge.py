"""LLM-as-judge：按 rubric 逐项打分，引用产出原文佐证——引用不到 = 判分无效（防作弊）。

judge 只看产出与 rubric，不给被评对象的 prompt/轨迹（设计文档 §3 防作弊约束）。
judge 挂掉/输出不合法 → 该项判分无效（score=None），用例退回纯确定性断言，不误伤。
"""

from __future__ import annotations

import json
import logging
import re

from ..llm.types import Message, TextBlock

logger = logging.getLogger(__name__)

JUDGE_SYSTEM = (
    "你是评测裁判。对给定产出按评分维度逐项打 1-5 分，"
    "每一项必须引用产出原文作为佐证。只输出 JSON，不要输出其他内容。"
)

_WHITESPACE_RE = re.compile(r"\s+")
_ECHO_PREFIX_RE = re.compile(r"^\s*(?:\d+[.、)]|[-*])\s*")


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub("", text)


def _coerce_score(value) -> int | None:
    """分数宽容化：int 直接用；数字串/整值浮点 coerce；bool 永不折算成分数（微妙守卫）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _echo_key(echoed: str) -> str:
    """回显维度归一化：剥 prompt 序号/项目符前缀（"1. 口径" → "口径"）+ 空白折叠。"""
    return _normalize(_ECHO_PREFIX_RE.sub("", echoed))


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
    if response.text and not items_raw:
        logger.debug("judge 回复解析出 0 项，reply 前 200 字: %s", response.text[:200])
    rubric_by_norm = {_normalize(item): item for item in rubric}
    by_item: dict[str, dict] = {}
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        echoed = str(it.get("item", ""))
        rubric_item = rubric_by_norm.get(_echo_key(echoed))
        if rubric_item is not None:  # 剥序号归一化后仍对不上的回显项不收
            by_item[rubric_item] = it
    answer_norm = _normalize(answer)
    results = []
    for item in rubric:
        it = by_item.get(item, {})
        quote = str(it.get("quote", ""))
        score = _coerce_score(it.get("score"))
        valid = (
            score is not None
            and 1 <= score <= 5
            and bool(quote)
            and _normalize(quote) in answer_norm
        )
        results.append(
            {"item": item, "score": score if valid else None, "quote": quote, "valid": valid}
        )
    return results
