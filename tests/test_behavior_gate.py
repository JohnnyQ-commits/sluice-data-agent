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
