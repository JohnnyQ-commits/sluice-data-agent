"""基线比对与门禁测试：per-skill 通过率 + judge 均分，分数下降即 fail。"""

from sluice.evals.compare import compare
from sluice.evals.model import CaseResult, EvalReport


def _report(results):
    return EvalReport(results=results)


def test_flat_results_pass():
    base = _report(
        [CaseResult("s", "a", "guardrail", "pass"), CaseResult("s", "b", "guardrail", "pass")]
    )
    new = _report(
        [CaseResult("s", "a", "guardrail", "pass"), CaseResult("s", "b", "guardrail", "pass")]
    )
    ok, lines = compare(new, base)
    assert ok and any("s" in line for line in lines)


def test_pass_rate_drop_fails():
    base = _report(
        [CaseResult("s", "a", "guardrail", "pass"), CaseResult("s", "b", "guardrail", "pass")]
    )
    new = _report(
        [CaseResult("s", "a", "guardrail", "pass"), CaseResult("s", "b", "guardrail", "fail")]
    )
    ok, lines = compare(new, base)
    assert not ok and any("❌" in line for line in lines)


def test_judge_avg_drop_fails():
    base = _report(
        [CaseResult("s", "a", "outcome", "pass", {"judge_avg": 4.5, "judge_valid": True})]
    )
    new = _report(
        [CaseResult("s", "a", "outcome", "pass", {"judge_avg": 3.8, "judge_valid": True})]
    )
    ok, lines = compare(new, base)
    assert not ok and any("judge" in line for line in lines)


def test_skipped_excluded_from_comparison():
    """无 API key 的新报告（outcome/behavior 全 skipped）不误触门禁。"""
    base = _report(
        [
            CaseResult("s", "a", "guardrail", "pass"),
            CaseResult("s", "b", "outcome", "pass", {"judge_avg": 4.5, "judge_valid": True}),
        ]
    )
    new = _report(
        [CaseResult("s", "a", "guardrail", "pass"), CaseResult("s", "b", "outcome", "skipped")]
    )
    ok, _ = compare(new, base)
    assert ok  # guardrail 通过率持平；judge 侧 new 无有效分不参与


def test_new_skill_added_is_neutral():
    base = _report([CaseResult("s", "a", "guardrail", "pass")])
    new = _report(
        [CaseResult("s", "a", "guardrail", "pass"), CaseResult("t", "b", "guardrail", "fail")]
    )
    ok, _ = compare(new, base)
    assert ok  # 新增 skill 的失败不追溯旧基线
