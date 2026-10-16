"""ReActRuntime 事件回调测试（SSE 数据源）。

复用 test_runtime_loop 的测试替身（ScriptedClient / make_registry / resp_*），
避免重复定义。事件契约见 sluice/runtime/loop.py 模块注释。
"""

from sluice.runtime.loop import ReActRuntime
from test_runtime_loop import ScriptedClient, make_registry, resp_text, resp_tool


def make_two_round_client():
    """第一轮调 echo 工具，第二轮出最终回答。"""
    return ScriptedClient(
        [
            resp_tool("我调用 echo 工具", "tu_1", "echo", {"text": "done"}),
            resp_text("最终回答"),
        ]
    )


def test_events_emitted_in_order():
    events = []
    rt = ReActRuntime(make_two_round_client(), make_registry(), on_event=events.append)
    rt.run("hi")
    types = [e["type"] for e in events]
    assert types == ["round_start", "tool_call", "observation", "round_start", "final"]
    assert events[1] == {
        "type": "tool_call",
        "round": 1,
        "name": "echo",
        "args": {"text": "done"},
    }
    assert events[2]["round"] == 1
    assert events[2]["name"] == "echo"
    assert events[2]["observation"] == "done" and events[2]["is_error"] is False
    assert events[-1] == {"type": "final", "answer": "最终回答"}


def test_no_callback_default_runs_fine():
    rt = ReActRuntime(make_two_round_client(), make_registry())
    assert rt.run("hi") == "最终回答"


def test_trace_unchanged_with_events():
    rt = ReActRuntime(make_two_round_client(), make_registry(), on_event=lambda e: None)
    rt.run("hi")
    assert len(rt.trace) == 2 and rt.trace[0]["round"] == 1


def test_run_level_on_event_overrides_constructor():
    """run() 的 on_event 参数非 None 时覆盖构造器值（Flask 每请求独立队列需要）。"""
    constructor_events, run_events = [], []
    rt = ReActRuntime(make_two_round_client(), make_registry(), on_event=constructor_events.append)
    rt.run("hi", on_event=run_events.append)
    assert run_events and not constructor_events


def test_run_level_none_keeps_constructor():
    constructor_events = []
    rt = ReActRuntime(make_two_round_client(), make_registry(), on_event=constructor_events.append)
    rt.run("hi", on_event=None)
    assert constructor_events
