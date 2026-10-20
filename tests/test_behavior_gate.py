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


def test_same_round_aba_sequence():
    """同轮 A→B→再调 A：_round_keys 是集合语义，不是只记最后一次调用的槽位。"""
    gate = BehaviorGate(token_budget=0)
    a = ToolUseBlock("t1", "demo.report", {"text": "a"})
    b = ToolUseBlock("t2", "demo.report", {"text": "b"})
    assert gate.check_call(a) is None  # 放行并 claim
    assert gate.check_call(b) is None  # 放行并 claim
    reason = gate.check_call(ToolUseBlock("t3", "demo.report", {"text": "a"}))
    assert reason is not None and "同一轮内" in reason


def test_ab_a_pattern_still_allowed():
    """A→B→A 隔轮重复仍放行（既有语义，留给 evals 观察是否收紧）。"""
    gate = BehaviorGate(token_budget=0)
    a, b = ToolUseBlock("t1", "t.a", {}), ToolUseBlock("t2", "t.b", {})
    assert gate.check_call(a) is None
    gate.commit_round([a])
    assert gate.check_call(b) is None
    gate.commit_round([b])
    assert gate.check_call(a) is None


def test_concurrent_check_call_thread_safe():
    """并发 check_call 不串状态：线程安全是并发工具执行的前置（P4 红线）。

    注：check_call 既有语义是同轮内重复调用即拦，同一线程重复发同一 tu 第 2 次起
    本就该被拦（非并发缺陷）——故每次迭代用唯一指纹，保证 200x2 次互不命中全部放行。
    """
    import threading

    gate = BehaviorGate(token_budget=0)
    blocked: list[bool] = []
    errors: list[Exception] = []

    def _worker(i: int):
        try:
            for j in range(200):
                tu = ToolUseBlock(f"t{i}-{j}", "demo.report", {"text": f"{i}-{j}"})
                blocked.append(gate.check_call(tu) is not None)
        except Exception as e:  # noqa: BLE001 — 测试需捕获线程内任意异常
            errors.append(e)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    # 唯一指纹互不命中：200x2 次全部放行
    assert not any(blocked)


def test_concurrent_same_key_exactly_one_passes():
    """同指纹 barrier 钉子：8 线程对同一 ToolUseBlock 并发 check_call，恰好 1 个放行。

    持锁时确定性绿——锁串行化 check_call，首个放行并 claim，其余必被同轮查重拦截；
    失锁时 TOCTOU 双放行概率性红（GIL 下 set.add/in 静默不抛异常，失锁是静默的）。
    """
    import threading

    gate = BehaviorGate(token_budget=0)
    n = 8
    barrier = threading.Barrier(n)
    results: list[str | None] = [None] * n

    def _worker(i: int):
        barrier.wait()
        tu = ToolUseBlock(f"t{i}", "demo.report", {"text": "same"})
        results[i] = gate.check_call(tu)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    # 同轮查重语义：恰好 1 个放行（claim 当轮指纹），其余 7 个全被"同一轮内重复"拦截
    assert sum(r is None for r in results) == 1
    assert all(r is not None and "同一轮内" in r for r in results if r is not None)


def test_concurrent_check_call_and_commit_round_no_error():
    """check_call 与 commit_round 并发穿插：唯一指纹互不命中，全程无异常、无拦截。

    防回归钉子（概率性）：GIL 下失锁是静默的，本组并发测试靠概率抓回归，
    真正的确定性保证在 test_concurrent_same_key_exactly_one_passes。
    """
    import threading

    gate = BehaviorGate(token_budget=0)
    errors: list[Exception] = []
    blocked: list[bool] = []

    def _worker(i: int):
        try:
            for j in range(100):
                tu = ToolUseBlock(f"id{i}-{j}", "demo.report", {"i": i, "j": j})
                blocked.append(gate.check_call(tu) is not None)
                gate.commit_round([ToolUseBlock(f"c{i}-{j}", "demo.report", {"i": i})])
        except Exception as e:  # noqa: BLE001 — 测试需捕获线程内任意异常
            errors.append(e)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    assert not any(blocked)
