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


# --- CLI exit 语义（_eval 门禁/选包/基线落盘） ---

import pytest

from sluice.cli import main as cli_main
from sluice.evals.model import CaseResult, EvalReport


def _stub_pkg(fail=False):
    evals = [
        {
            "name": "g1",
            "layer": "guardrail",
            "input": "x",
            "script": [{"text": "done"}],
            # fail=True：期望门禁事件但剧本不触发 → missing → fail
            "expect_guardrails": [{"gate": "input", "action": "blocked"}] if fail else [],
        }
    ]
    return SkillPackage(name="alpha", description="d", path=Path("."), evals=evals)


def _patch_cli(monkeypatch, tmp_path, pkgs):
    """repo 根指到 tmp、强制无 key、audit 指 tmp、Skill 加载返回桩包。"""
    monkeypatch.setattr(cli_main, "_REPO_ROOT", tmp_path)
    monkeypatch.setenv("SLUICE_LLM_API_KEY", "")
    monkeypatch.setenv("SLUICE_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setattr(cli_main.SkillLoader, "load", lambda self, d, registry: pkgs)


def test_eval_gate_fail_exits_1(tmp_path, monkeypatch):
    _patch_cli(monkeypatch, tmp_path, [_stub_pkg(fail=True)])
    baseline = EvalReport(results=[CaseResult("alpha", "g1", "guardrail", "pass")])
    bp = tmp_path / "baseline.json"
    baseline.save(bp)
    with pytest.raises(SystemExit) as ei:
        cli_main.main(["eval", "--all", "--baseline", str(bp)])
    assert ei.value.code == 1


def test_eval_gate_pass_exits_0(tmp_path, monkeypatch):
    _patch_cli(monkeypatch, tmp_path, [_stub_pkg()])
    baseline = EvalReport(results=[CaseResult("alpha", "g1", "guardrail", "pass")])
    bp = tmp_path / "baseline.json"
    baseline.save(bp)
    with pytest.raises(SystemExit) as ei:
        cli_main.main(["eval", "--all", "--baseline", str(bp)])
    assert ei.value.code == 0


def test_eval_unknown_skill_exits_2(tmp_path, monkeypatch):
    _patch_cli(monkeypatch, tmp_path, [_stub_pkg()])
    with pytest.raises(SystemExit) as ei:
        cli_main.main(["eval", "ghost"])
    assert ei.value.code == 2


def test_report_marks_known_issue(capsys):
    from sluice.cli.main import _print_report
    from sluice.evals.model import CaseResult, EvalReport

    _print_report(
        EvalReport(results=[CaseResult("s", "route", "behavior", "fail", known_issue=True)]),
        out=None,
    )
    assert "known_issue" in capsys.readouterr().out


def test_eval_update_baseline_writes_file(tmp_path, monkeypatch):
    _patch_cli(monkeypatch, tmp_path, [_stub_pkg()])
    with pytest.raises(SystemExit) as ei:
        cli_main.main(["eval", "--all", "--update-baseline"])
    assert ei.value.code == 0
    assert (tmp_path / "evals" / "baseline.json").is_file()
