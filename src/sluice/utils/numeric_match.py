"""数值词边界匹配：真值数字是否出现在文本产出中。

共用方有两处：
- evals/（备案①确定性数值通道）：expect_values 的 SQL 真值必须出现在产出中；
- fact-check Skill（报告验真）：报告声称值 vs 查库真值，逐一盖章 属实/不符。

匹配是宽容方向：真值数字的多种常见格式（整数/1 位小数/2 位小数/中文"万"缩写）
任一出现在产出即算命中，词边界断开防止子串误命中（真值 311 不匹配 1311）。
千分位逗号在文本侧剥除后再比对。
"""

from __future__ import annotations

import re

# 词边界不用 \b：小数点两侧的数字段（"16.5" 之于 "16.52"）\b 拦不住，显式前后向断言更严
_L = r"(?<![\d.])"
_R = r"(?![\d.])"


def _candidates(value: float) -> list[str]:
    """真值 → 可接受的文本格式候选，去重保序。

    基础形态：2 位小数/1 位小数/整值；中文报告习惯"万"缩写（213177.33 → 21.32万），
    万级真值追加 1~2 位小数的万缩写候选。
    非整值追加最近整数候选（194.57 → "195"）：取整是渲染差异不是编造——编造差异
    是数量级的（1311 vs 311），词边界+候选集足以区分。
    """
    out = [f"{value:.2f}", f"{value:.1f}"]
    if value == int(value):
        out.append(str(int(value)))
    else:
        out.append(str(round(value)))
    if abs(value) >= 10000:
        wan = value / 10000
        out += [f"{wan:.2f}万", f"{wan:.1f}万"]
    return list(dict.fromkeys(out))


def check_value_in_answer(value: float, answer: str) -> bool:
    """真值数字（词边界限定）是否出现在文本产出中。"""
    normalized = answer.replace(",", "")
    return any(re.search(_L + re.escape(cand) + _R, normalized) for cand in _candidates(value))
