"""基线比对与门禁：与基线比 per-skill 通过率 + judge 均分，分数下降即 fail。

比对只看两个报告共有 skill 的可计数结果；skipped 不进分母，
新增 skill 的失败不追溯旧基线（新基线由 --update-baseline 建立后再门禁）。
"""

from __future__ import annotations

from .model import EvalReport


def _skill_pass_rate(report: EvalReport, skill: str) -> float:
    counted = [r for r in report.results if r.skill == skill and r.status != "skipped"]
    if not counted:
        return 1.0
    return sum(1 for r in counted if r.status == "pass") / len(counted)


def compare(new: EvalReport, baseline: EvalReport) -> tuple[bool, list[str]]:
    """返回 (门禁是否通过, 人类可读比对行)。"""
    lines: list[str] = []
    ok = True
    common = {r.skill for r in baseline.results if r.status != "skipped"} & {
        r.skill for r in new.results if r.status != "skipped"
    }
    for skill in sorted(common):
        bp, np_ = _skill_pass_rate(baseline, skill), _skill_pass_rate(new, skill)
        mark = "✅" if np_ >= bp else "❌"
        if np_ < bp:
            ok = False
        lines.append(f"{mark} {skill}: 通过率 {bp:.0%} → {np_:.0%}")
    bj, nj = baseline.judge_avg(), new.judge_avg()
    if bj is not None and nj is not None:
        mark = "✅" if nj >= bj else "❌"
        if nj < bj:
            ok = False
        lines.append(f"{mark} judge 均分: {bj:.2f} → {nj:.2f}")
    return ok, lines
