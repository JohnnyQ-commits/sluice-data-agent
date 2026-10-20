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


def test_judge_avg_skips_dirty_detail_missing_key():
    r = EvalReport(
        results=[
            CaseResult("s", "a", "outcome", "pass", {"judge_valid": True}),  # 缺 judge_avg
            CaseResult("s", "b", "outcome", "pass", {"judge_avg": 3.0, "judge_valid": True}),
        ]
    )
    assert r.judge_avg() == 3.0  # 脏条目静默跳过，不抛 KeyError


def test_judge_avg_skips_dirty_detail_string_score():
    r = EvalReport(
        results=[
            # 字符串分数是 JSON 手改/上游 bug 的典型脏数据，不能 TypeError 也不能计入
            CaseResult("s", "a", "outcome", "pass", {"judge_avg": "4.5", "judge_valid": True}),
            CaseResult("s", "b", "outcome", "pass", {"judge_avg": 2.0, "judge_valid": True}),
        ]
    )
    assert r.judge_avg() == 2.0


def test_judge_avg_string_true_not_treated_as_valid():
    r = EvalReport(
        results=[
            # judge_valid 为字符串 "true"（JSON 手改）不得当 True，宁缺不错
            CaseResult("s", "a", "outcome", "pass", {"judge_avg": 4.5, "judge_valid": "true"}),
        ]
    )
    assert r.judge_avg() is None


def test_judge_avg_bool_score_not_counted():
    r = EvalReport(
        results=[
            # bool 是 int 子类：True 当 1.0 计入均分是错的
            CaseResult("s", "a", "outcome", "pass", {"judge_avg": True, "judge_valid": True}),
        ]
    )
    assert r.judge_avg() is None


def test_must_contain_string_raises():
    # "SQL" 被 list() 逐字符拆成 ['S','Q','L'] 是最危险的静默失效
    with pytest.raises(ValueError, match="must_contain"):
        _case(must_contain="SQL")


def test_script_non_list_raises():
    with pytest.raises(ValueError, match="script"):
        _case(script={"steps": 1})


def test_allowed_keys_synced_with_dataclass_fields():
    import dataclasses

    from sluice.evals import model

    assert {f.name for f in dataclasses.fields(EvalCase)} - {"skill"} == model._ALLOWED_KEYS


def test_known_issue_field_roundtrip():
    """known_issue 进 EvalCase 与 CaseResult，且 JSON roundtrip 保留（P4：结构性已知 fail 豁免门禁）。"""
    case = EvalCase.from_dict(
        "s", {"name": "c", "layer": "behavior", "input": "x", "known_issue": True}
    )
    assert case.known_issue is True
    default = EvalCase.from_dict("s", {"name": "c", "layer": "behavior", "input": "x"})
    assert default.known_issue is False
    r = CaseResult("s", "c", "behavior", "fail", known_issue=True)
    assert EvalReport(results=[r]).to_dict()["results"][0]["known_issue"] is True


def test_save_load_roundtrip(tmp_path):
    r = EvalReport(
        results=[
            CaseResult("s", "a", "guardrail", "pass", {"k": 1}),
            # judge_avg 为 None：json null 往返场景
            CaseResult("s", "b", "outcome", "fail", {"judge_avg": None, "judge_valid": False}),
        ]
    )
    p = tmp_path / "rep.json"
    r.save(p)
    loaded = EvalReport.load(p)
    assert loaded.generated_at == r.generated_at
    for orig, back in zip(r.results, loaded.results):
        assert back.skill == orig.skill and back.case == orig.case
        assert back.layer == orig.layer and back.status == orig.status
        assert back.detail == orig.detail
