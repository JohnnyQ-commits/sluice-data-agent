"""评测层数据模型测试（Task 1）。"""

import pytest

from sluice.evals.model import CaseResult, EvalCase, EvalReport


def _case(**kw):
    base = {"name": "c1", "layer": "guardrail", "input": "hi"}
    return EvalCase.from_dict("weekly-report", {**base, **kw})


def test_from_dict_defaults():
    c = _case()
    assert c.skill == "weekly-report" and c.name == "c1" and c.layer == "guardrail"
    assert c.must_contain == [] and c.judge_rubric == []
    assert c.expect_skill == "" and c.script == [] and c.expect_guardrails == []


def test_from_dict_missing_required_raises():
    with pytest.raises(ValueError, match="name"):
        EvalCase.from_dict("s", {"layer": "guardrail", "input": "hi"})


def test_from_dict_unknown_layer_raises():
    with pytest.raises(ValueError, match="未知评测层"):
        _case(layer="magic")


def test_from_dict_unknown_key_raises():
    with pytest.raises(ValueError, match="exepct_skill"):
        _case(exepct_skill="x")  # 故意拼错：未知键必须报错，防 CASES 笔误静默失效


def test_pass_rate_skips_skipped():
    r = EvalReport(
        results=[
            CaseResult("s", "a", "guardrail", "pass"),
            CaseResult("s", "b", "guardrail", "fail"),
            CaseResult("s", "c", "outcome", "skipped"),
        ]
    )
    assert r.pass_rate() == 0.5  # skipped 不进分母


def test_pass_rate_all_skipped_is_one():
    r = EvalReport(results=[CaseResult("s", "a", "outcome", "skipped")])
    assert r.pass_rate() == 1.0  # 无 API key 全 skipped 不算 0 分


def test_judge_avg_aggregates_valid_only():
    r = EvalReport(
        results=[
            CaseResult("s", "a", "outcome", "pass", {"judge_avg": 4.5, "judge_valid": True}),
            CaseResult("s", "b", "outcome", "fail", {"judge_avg": None, "judge_valid": False}),
            CaseResult("s", "c", "guardrail", "pass"),  # 非 outcome 层不参与
        ]
    )
    assert r.judge_avg() == 4.5


def test_save_load_roundtrip(tmp_path):
    r = EvalReport(results=[CaseResult("s", "a", "guardrail", "pass", {"k": 1})])
    p = tmp_path / "rep.json"
    r.save(p)
    loaded = EvalReport.load(p)
    assert loaded.results[0].skill == "s" and loaded.results[0].detail == {"k": 1}
