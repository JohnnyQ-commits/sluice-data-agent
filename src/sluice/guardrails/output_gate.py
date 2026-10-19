"""输出门禁（拦截点③，产出交付前）：非空 + 必需小节。

确定性判断：小节标题是 Skill 在 frontmatter 里声明的产出契约（output_contract），
门禁只查"在不在"，不查"写得好不好"——语义质量归 P3 evals。
"""

from __future__ import annotations


def check_output(answer: str, required_sections: list[str]) -> str | None:
    """返回 None 放行；返回拦截原因（含缺失清单，可直接回喂模型自修）。"""
    if not answer or not answer.strip():
        return "最终回答为空"
    missing = [s for s in required_sections if s not in answer]
    if missing:
        return f"最终回答缺少必需小节: {'、'.join(missing)}"
    return None
