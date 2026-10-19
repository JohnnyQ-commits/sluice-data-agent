"""sluice eval CLI 测试：run_eval 分发 + parser 契约。"""

from pathlib import Path
from typing import ClassVar

from sluice.cli.main import build_parser
from sluice.config.settings import Settings
from sluice.evals.runner import run_eval
from sluice.skills.loader import SkillPackage
from sluice.tools.base import Tool, ToolResult
from sluice.tools.registry import ToolRegistry


def test_parser_eval_flags():
    args = build_parser().parse_args(["eval", "--all", "--baseline", "b.json", "--skip-judge"])
    assert args.command == "eval" and args.all and args.baseline == "b.json" and args.skip_judge
    args2 = build_parser().parse_args(["eval", "weekly-report"])
    assert args2.skill == "weekly-report" and not args2.all


class StubTool(Tool):
    name = "alpha.query"
    description = "d"
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}

    def execute(self, args):
        return ToolResult("ok")


def _pkg(evals):
    return SkillPackage(name="alpha", description="d", path=Path("."), evals=evals)


def test_run_eval_dispatches_three_layers(tmp_path):
    """三层用例经 run_eval 正确分发：guardrail 执行、outcome/behavior 无 client 时 skipped。"""
    registry = ToolRegistry()
    registry.register(StubTool())
    pkg = _pkg(
        [
            {
                "name": "g1",
                "layer": "guardrail",
                "input": "x",
                "script": [{"text": "done"}],
                "expect_guardrails": [],
            },
            {"name": "b1", "layer": "behavior", "input": "x", "expect_skill": "alpha"},
            {"name": "o1", "layer": "outcome", "input": "x", "must_contain": ["指标"]},
        ]
    )
    results = run_eval(
        [pkg],
        [pkg],
        registry,
        Settings(audit_dir=str(tmp_path / "a")),
        client=None,
        judge_client=None,
    )
    by_name = {r.case: r for r in results}
    assert by_name["g1"].status == "pass"  # 剧本单 final + 期望空 → pass
    assert by_name["b1"].status == "skipped"
    assert by_name["o1"].status == "skipped"


def test_run_eval_malformed_case_is_error_not_crash(tmp_path):
    registry = ToolRegistry()
    pkg = _pkg([{"name": "bad", "layer": "magic", "input": "x"}])
    results = run_eval(
        [pkg],
        [pkg],
        registry,
        Settings(audit_dir=str(tmp_path / "a")),
        client=None,
        judge_client=None,
    )
    assert results[0].status == "error" and "未知评测层" in results[0].detail["error"]


def test_run_eval_non_dict_case_is_error_not_crash(tmp_path):
    """evals 条目非 dict（如误写整数）归 error，不炸整批评测。"""
    registry = ToolRegistry()
    pkg = _pkg([42])
    results = run_eval(
        [pkg],
        [pkg],
        registry,
        Settings(audit_dir=str(tmp_path / "a")),
        client=None,
        judge_client=None,
    )
    assert results[0].status == "error"
