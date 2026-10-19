"""tests/test_behavior_gate.py"""

import pytest

from sluice.exceptions import BudgetExceededError
from sluice.guardrails.behavior_gate import BehaviorGate, call_key
from sluice.llm.types import ToolUseBlock


def _tu(name="q", sql="select 1"):
    return ToolUseBlock("t1", name, {"sql": sql})


def test_call_key_order_insensitive():
    a = ToolUseBlock("t", "x", {"a": 1, "b": 2})
    b = ToolUseBlock("t", "x", {"b": 2, "a": 1})
    assert call_key(a) == call_key(b)


def test_repeated_call_in_next_round_blocked():
    g = BehaviorGate(token_budget=100_000)
    assert g.check_call(_tu()) is None
    g.commit_round([_tu()])
    reason = g.check_call(_tu())
    assert reason is not None and "重复调用" in reason


def test_different_args_not_blocked():
    g = BehaviorGate(token_budget=100_000)
    g.check_call(_tu())
    g.commit_round([_tu()])
    assert g.check_call(_tu(sql="select 2")) is None


def test_repeat_allowed_after_gap_round():
    """A → B → A 不算连续重复：中间隔了一轮不同的调用。"""
    g = BehaviorGate(token_budget=100_000)
    g.check_call(_tu())
    g.commit_round([_tu()])
    g.check_call(_tu(sql="select 2"))
    g.commit_round([_tu(sql="select 2")])
    assert g.check_call(_tu()) is None


def test_token_budget_exceeded():
    g = BehaviorGate(token_budget=100)
    assert g.record_usage({"input_tokens": 80, "output_tokens": 10}) is None
    assert "token 预算超限" in g.record_usage({"input_tokens": 80, "output_tokens": 10})


def test_token_budget_zero_disables_check():
    g = BehaviorGate(token_budget=0)
    assert g.record_usage({"input_tokens": 10**9}) is None


def test_budget_exceeded_error_is_sluice_error():
    with pytest.raises(BudgetExceededError):
        raise BudgetExceededError("x")


def test_same_round_identical_call_blocked():
    """同一条 assistant 消息里两个完全相同的 tool_use：第二个被打断（P2 终审遗留）。"""
    gate = BehaviorGate(token_budget=0)
    first = ToolUseBlock("t1", "demo.report", {"text": "x"})
    second = ToolUseBlock("t2", "demo.report", {"text": "x"})
    assert gate.check_call(first) is None  # 放行并登记当轮指纹
    reason = gate.check_call(second)
    assert reason is not None and "同一轮内" in reason


def test_same_round_same_tool_different_args_pass():
    gate = BehaviorGate(token_budget=0)
    assert gate.check_call(ToolUseBlock("t1", "demo.report", {"text": "a"})) is None
    assert gate.check_call(ToolUseBlock("t2", "demo.report", {"text": "b"})) is None


def test_blocked_call_does_not_claim_round_key():
    """被拦（与上一轮重复）的调用不登记当轮指纹——下轮重试仍由对应门禁接住，不误判。"""
    gate = BehaviorGate(token_budget=0)
    tu = ToolUseBlock("t1", "demo.report", {})
    gate.commit_round([tu])  # 上一轮调过 A
    assert gate.check_call(tu) is not None  # 本轮再调 → 与上一轮重复被打断，不 claim
    gate.commit_round([])  # 轮转清空
    assert gate.check_call(tu) is None  # 新一轮首调放行


def test_commit_round_clears_round_keys():
    gate = BehaviorGate(token_budget=0)
    tu = ToolUseBlock("t1", "demo.report", {})
    assert gate.check_call(tu) is None
    gate.commit_round([tu])
    assert gate.check_call(tu) is not None  # commit 后 A 变成"上一轮"，再调被拦（既有语义）


def test_ab_a_pattern_still_allowed():
    """A→B→A 隔轮重复仍放行（既有语义，留给 evals 观察是否收紧）。"""
    gate = BehaviorGate(token_budget=0)
    a, b = ToolUseBlock("t1", "t.a", {}), ToolUseBlock("t2", "t.b", {})
    assert gate.check_call(a) is None
    gate.commit_round([a])
    assert gate.check_call(b) is None
    gate.commit_round([b])
    assert gate.check_call(a) is None
