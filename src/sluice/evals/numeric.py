"""确定性数值通道：expect_values 的 SQL 真值对账（judge 查库通道的替代实现，备案①）。

数值正确性不交给 LLM judge 评分——judge 无查库通道，"数值与真实聚合一致"这类维度
此前只能凭语气给分（编造数值照样 valid）。改用例自带 SQL 真值、runner 直接查库对账：
judge 只评口径/表述等质性维度，数值正确性走确定性比对。

匹配是"真值数字必须出现在产出中"的宽容方向：整数/1 位小数/2 位小数多种格式候选
任一命中即可（真值 174.0 接受产出写 174 或 174.0），词边界断开防止子串误命中
（真值 311 不匹配 1311）。千分位逗号在产出侧剥除后再比对。
"""

from __future__ import annotations

import re
import sqlite3

# 词边界不用 \b：小数点两侧的数字段（"16.5" 之于 "16.52"）\b 拦不住，显式前后向断言更严
_L = r"(?<![\d.])"
_R = r"(?![\d.])"


def _candidates(value: float) -> list[str]:
    """真值 → 可接受的产出格式候选，去重保序。

    基础形态：2 位小数/1 位小数/整值；中文报告习惯"万"缩写（213177.33 → 21.32万），
    万级真值追加 1~2 位小数的万缩写候选。
    """
    out = [f"{value:.2f}", f"{value:.1f}"]
    if value == int(value):
        out.append(str(int(value)))
    if abs(value) >= 10000:
        wan = value / 10000
        out += [f"{wan:.2f}万", f"{wan:.1f}万"]
    return list(dict.fromkeys(out))


def check_value_in_answer(value: float, answer: str) -> bool:
    """真值数字（词边界限定）是否出现在产出中。"""
    normalized = answer.replace(",", "")
    return any(re.search(_L + re.escape(cand) + _R, normalized) for cand in _candidates(value))


def verify_expect_values(db_path: str, expect_values: list[dict], answer: str) -> list[dict]:
    """逐条执行用例自带 SQL 取真值并对账，返回 [{label, expected, found}（+error?）]。

    SQL 出错/空结果/非数值 → found=False 并如实带 error 字段（评测引擎不炸，确定性
    通道的失败就是用例 fail 的证据，宁可见 fail 不可静默放行）。
    """
    results: list[dict] = []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for spec in expect_values:
            label, sql = spec["label"], spec["sql"]
            try:
                row = conn.execute(sql).fetchone()
                value = row[0] if row else None
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    results.append(
                        {
                            "label": label,
                            "expected": value,
                            "found": False,
                            "error": "SQL 真值不是数值" if value is not None else "SQL 真值为空",
                        }
                    )
                else:
                    value = float(value)
                    results.append(
                        {
                            "label": label,
                            "expected": value,
                            "found": check_value_in_answer(value, answer),
                        }
                    )
            except sqlite3.Error as exc:
                results.append(
                    {
                        "label": label,
                        "expected": None,
                        "found": False,
                        "error": f"SQL 执行失败: {exc}",
                    }
                )
    finally:
        conn.close()
    return results
