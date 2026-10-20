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


def test_known_issue_fail_exempt_from_gate():
    """known_issue 的 fail 不进通过率分母：基线豁免的结构性 fail 不拖累也卡不住门禁。"""
    base = _report(
        [
            CaseResult("s", "a", "guardrail", "pass"),
            CaseResult("s", "route", "behavior", "fail", known_issue=True),
        ]
    )
    new = _report(
        [
            CaseResult("s", "a", "guardrail", "pass"),
            CaseResult("s", "route", "behavior", "fail", known_issue=True),
        ]
    )
    ok, _ = compare(new, base)
    assert ok  # 两侧 known_issue fail 均不计分


def test_known_issue_does_not_mask_real_regression():
    """豁免只对 known_issue fail 生效：同 skill 普通 fail 下降仍报警。"""
    base = _report(
        [
            CaseResult("s", "a", "guardrail", "pass"),
            CaseResult("s", "route", "behavior", "fail", known_issue=True),
        ]
    )
    new = _report(
        [
            CaseResult("s", "a", "guardrail", "fail"),
            CaseResult("s", "route", "behavior", "fail", known_issue=True),
        ]
    )
    ok, _ = compare(new, base)
    assert not ok


def test_vanished_skill_fails():
    """基线有结果的 skill 在新报告中整体消失（如加载失败）→ 门禁 fail。"""
    base = _report(
        [
            CaseResult("s", "a", "guardrail", "pass"),
            CaseResult("t", "b", "guardrail", "pass"),
        ]
    )
    new = _report([CaseResult("s", "a", "guardrail", "pass")])
    ok, lines = compare(new, base)
    assert not ok and any("消失" in line for line in lines)
