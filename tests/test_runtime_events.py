"""ReActRuntime 事件回调测试（SSE 数据源）。

复用 test_runtime_loop 的测试替身（ScriptedClient / make_registry / resp_*），
避免重复定义。事件契约见 sluice/runtime/loop.py 模块注释。
"""

import threading

import pytest

from sluice.exceptions import RoundsExhaustedError
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


def test_callback_exception_does_not_kill_run():
    """事件是可观察性旁路：坏回调不能 kill run()（fail-open）。"""

    def bad_callback(event):
        raise RuntimeError("回调炸了")

    rt = ReActRuntime(make_two_round_client(), make_registry(), on_event=bad_callback)
    assert rt.run("hi") == "最终回答"


def test_error_event_on_rounds_exhausted():
    """异常终止路径：先 emit error 事件再向上传播异常。"""
    events = []
    client = ScriptedClient([resp_tool("继续调", "tu_1", "echo", {"text": "x"})])
    rt = ReActRuntime(client, make_registry(), max_rounds=1, on_event=events.append)
    with pytest.raises(RoundsExhaustedError):
        rt.run("死循环")
    assert events[-1]["type"] == "error"
    assert "ReAct 循环超过 1 轮" in events[-1]["message"]


def test_unknown_tool_observation_event_is_error():
    """未知工具路径：observation 事件 is_error=True（前端红色渲染契约）。"""
    events = []
    client = ScriptedClient(
        [
            resp_tool("调一个不存在的工具", "tu_1", "no_such_tool", {}),
            resp_text("好的我换个说法"),
        ]
    )
    rt = ReActRuntime(client, make_registry(), on_event=events.append)
    assert rt.run("随便") == "好的我换个说法"
    obs_events = [e for e in events if e["type"] == "observation"]
    assert len(obs_events) == 1
    assert obs_events[0]["name"] == "no_such_tool"
    assert obs_events[0]["is_error"] is True
    assert "未知工具" in obs_events[0]["observation"]


def test_concurrent_runs_do_not_cross_write_trace():
    """共享 runtime 并发 run：trace 局部累积、结束原子赋值，互不串写。

    编排：甲线程两轮（工具→最终），第二轮 chat 挂起；乙线程的 run 在甲
    round1 已记录后才启动。旧实现（run 开头 self.trace=[]）会把甲的
    round1 抹掉，甲的快照只剩 round2；新实现局部累积不受影响。
    """
    for _ in range(5):
        _run_choreo_once()


def _run_choreo_once():
    ev = {k: threading.Event() for k in ("a1", "a_in2", "a2", "b_in", "b")}

    class ChoreoClient:
        def chat(self, messages, system=None, tools=None):
            text = messages[0].content[0].text
            if text == "问题甲":
                if len(messages) == 1:  # 第一轮
                    ev["a1"].wait(timeout=5)
                    return resp_tool("甲思考", "tu_1", "echo", {"text": "x"})
                ev["a_in2"].set()
                ev["a2"].wait(timeout=5)
                return resp_text("甲")
            ev["b_in"].set()  # run 已启动（旧实现此处已清 self.trace）
            ev["b"].wait(timeout=5)
            return resp_text("乙")

    rt = ReActRuntime(ChoreoClient(), make_registry(), max_rounds=3)
    results = {}

    def run_a():
        ans = rt.run("问题甲")
        results["甲"] = (ans, [dict(r) for r in rt.trace])

    def run_b():
        ans = rt.run("问题乙")
        results["乙"] = (ans, [dict(r) for r in rt.trace])

    ta = threading.Thread(target=run_a)
    ta.start()
    ev["a1"].set()
    assert ev["a_in2"].wait(timeout=5)  # 甲 round1 已记录、正挂第二轮
    tb = threading.Thread(target=run_b)
    tb.start()
    assert ev["b_in"].wait(timeout=5)  # 乙 run 已启动
    ev["a2"].set()
    ta.join()
    ev["b"].set()
    tb.join()

    ans_a, snap_a = results["甲"]
    ans_b, snap_b = results["乙"]
    assert ans_a == "甲" and ans_b == "乙"
    assert [r["round"] for r in snap_a] == [1, 2]  # 旧实现此处只剩 [2]
    assert snap_a[1]["thought"] == "甲"
    assert len(snap_b) == 1 and snap_b[0]["thought"] == "乙"
    assert rt.trace[0]["thought"] == "乙"  # last-writer-wins，trace 仍可用
