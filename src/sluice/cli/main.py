"""sluice CLI。run：单次指令跑完整 ReAct 循环；web：启动 Web 界面。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from ..config.settings import Settings, resolve_audit_dir
from ..evals.compare import compare
from ..evals.model import EvalReport
from ..evals.runner import run_eval
from ..exceptions import SluiceError
from ..guardrails.chain import GuardrailChain
from ..llm.client import LLMClient
from ..runtime.loop import ReActRuntime
from ..skills.loader import SkillLoader
from ..tools.echo import EchoTool
from ..tools.query_data import QueryDataTool
from ..tools.registry import ToolRegistry

# repo 根锚定（main.py 在 src/sluice/cli/ 下，parents[3] = repo 根）：
# skills 目录、evals 报告/基线统一由此出发，防 IDE/其他 cwd 启动时路径漂移
_REPO_ROOT = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sluice", description="Sluice — 可挂载 Skill 的数据 Agent 平台"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="单次指令：跑完整 ReAct 循环并打印轨迹")
    run_p.add_argument("message", help="发给 Agent 的指令")
    web_p = sub.add_parser("web", help="启动 Web 界面（Flask + SSE）")
    web_p.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    web_p.add_argument("--port", type=int, default=5001, help="监听端口（默认 5001）")
    eval_p = sub.add_parser("eval", help="Skill 评测（三层：产出/行为/门禁）+ 基线回归门禁")
    eval_p.add_argument("skill", nargs="?", default=None, help="Skill 名；与 --all 二选一")
    eval_p.add_argument("--all", action="store_true", help="评测全部 Skill")
    eval_p.add_argument(
        "--baseline", metavar="JSON", help="基线报告路径：比对并门禁（分数下降 exit 1）"
    )
    eval_p.add_argument(
        "--update-baseline", action="store_true", help="本次报告写为基线 evals/baseline.json"
    )
    eval_p.add_argument(
        "--skip-judge", action="store_true", help="跳过 LLM-as-judge（只跑确定性断言）"
    )
    mcp_p = sub.add_parser("mcp", help="MCP 工具发现与调用演示（读 .mcp.json）")
    mcp_p.add_argument("--call", metavar="TOOL", help="调用指定工具（如 mcp__mini__echo）")
    mcp_p.add_argument("--args", metavar="JSON", default="{}", help="工具参数 JSON")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        _run(args.message)
    elif args.command == "web":
        _web(args)
    elif args.command == "eval":
        _eval(args)
    elif args.command == "mcp":
        _mcp(args)


def _list_skills(skills_dir: str | Path = "skills") -> list[str]:
    """独立探测 Skill 名清单（用临时 ToolRegistry，不复用 create_app 内部的）。

    目录缺失/损坏时留痕并降级为空清单，不抛异常——cwd 不对等场景不阻断诊断。
    正常启动路径的清单来自 create_app 的 app.config["SLUICE_SKILLS"]，不二次 load。
    """
    try:
        packages = SkillLoader().load(skills_dir, ToolRegistry())
        return [pkg.name for pkg in packages]
    except Exception as exc:  # noqa: BLE001 — 目录缺失/损坏时降级，不阻断诊断
        logging.getLogger(__name__).warning("Skill 探测失败: %s", exc)
        return []


def _eval(args: argparse.Namespace) -> None:
    settings = Settings()
    settings = resolve_audit_dir(settings)
    registry = ToolRegistry()
    registry.register(QueryDataTool(settings.demo_db_path))
    packages = SkillLoader().load(_REPO_ROOT / "skills", registry)

    if args.all:
        selected = packages
    elif args.skill:
        selected = [p for p in packages if p.name == args.skill]
        if not selected:
            names = ", ".join(p.name for p in packages) or "（无）"
            print(f"未找到 Skill: {args.skill}（可用: {names}）", file=sys.stderr)
            sys.exit(2)
    else:
        print("指定 Skill 名或 --all", file=sys.stderr)
        sys.exit(2)

    has_key = bool(settings.llm_api_key)
    client = LLMClient(settings) if has_key else None
    judge_client = None
    if has_key and not args.skip_judge:
        judge_settings = (
            settings.model_copy(
                update={"llm_model": settings.eval_judge_model, "llm_max_tokens": 16384}
            )
            if settings.eval_judge_model
            else settings.model_copy(update={"llm_max_tokens": 16384})
        )
        # judge 单轮纯文本，思考型模型 4096 全耗思考正文零输出（T5 实录）
        judge_client = LLMClient(judge_settings)

    results = run_eval(selected, packages, registry, settings, client, judge_client)
    report = EvalReport(results=results)
    # 微秒精度：同秒内连续两次 eval 不互相覆写报告（time.strftime 在 Windows 不支持 %f）
    out = _REPO_ROOT / "evals" / "reports" / f"{datetime.now().strftime('%Y%m%d-%H%M%S%f')}.json"
    report.save(out)
    _print_report(report, out)

    gate_ok = True
    if args.baseline:
        baseline = EvalReport.load(args.baseline)
        gate_ok, lines = compare(report, baseline)
        print("--- 基线比对 ---")
        for line in lines or ["（无可比条目）"]:
            print(line)
    if args.update_baseline:
        baseline_path = _REPO_ROOT / "evals" / "baseline.json"
        report.save(baseline_path)
        print(f"基线已更新: {baseline_path}")
    sys.exit(0 if gate_ok else 1)


def _print_report(report: EvalReport, out: Path) -> None:
    flag = {"pass": "✅", "fail": "❌", "error": "💥", "skipped": "⏭"}
    avg = report.judge_avg()
    print(f"通过率 {report.pass_rate():.0%}  judge 均分 {f'{avg:.2f}' if avg is not None else '-'}")
    for r in report.results:
        # known_issue 豁免不隐藏：结构性已知 fail 原样展示并显式标注
        issue = " ⚠️known_issue" if r.known_issue and r.status == "fail" else ""
        print(f"  {flag[r.status]} [{r.skill}] {r.case} ({r.layer}) {r.status}{issue}")
        if r.status in ("fail", "error") and r.detail:
            print(f"      {r.detail}")
    print(f"报告: {out}")


def _mcp(args: argparse.Namespace) -> None:
    settings = Settings()
    config = _REPO_ROOT / settings.mcp_config_path
    if not config.is_file():  # mcp_enabled 与否不影响：文件不存在就无从演示，直接退出
        print("未找到 .mcp.json", file=sys.stderr)
        sys.exit(2)

    from ..mcp.registry import load_mcp_tools  # 延迟导入：run/eval 子命令无需 MCP

    servers = load_mcp_tools(config)
    try:
        tools = [t for s in servers for t in s.tools]
        if not args.call:
            for t in tools:
                print(f"{t.name}  {t.description}")
            print(f"共 {len(tools)} 个 MCP 工具")
            return
        tool = next((t for t in tools if t.name == args.call), None)
        if tool is None:
            print(f"未找到工具: {args.call}", file=sys.stderr)
            sys.exit(2)
        result = tool.execute(json.loads(args.args))
        print(result.content)
        sys.exit(1 if result.is_error else 0)
    finally:
        for s in servers:
            s.close()


def _web(args: argparse.Namespace) -> None:
    settings = Settings()
    if not settings.llm_api_key:
        print("缺少 SLUICE_LLM_API_KEY（复制 .env.example 为 .env 并填写）", file=sys.stderr)
        sys.exit(1)

    from ..web.app import create_app  # 延迟导入：run 子命令无需 Flask

    # skills 目录锚定 repo 根，防 IDE/其他 cwd 启动时静默丢 Skill；
    # 目录缺失时 SkillLoader 返回空清单自然降级
    skills_dir = _REPO_ROOT / "skills"
    app = create_app(skills_dir=str(skills_dir))

    packages = app.config.get("SLUICE_SKILLS", [])
    print(f"Sluice Web 启动: http://{args.host}:{args.port}")
    if packages:
        names = ", ".join(pkg.name for pkg in packages)
        print(f"已加载 Skill ({len(packages)}): {names}")
    else:
        print("无 Skill（skills/ 目录不存在或为空）")
    app.run(host=args.host, port=args.port)


def _run(message: str) -> None:
    settings = Settings()
    settings = resolve_audit_dir(settings)
    if not settings.llm_api_key:
        print("缺少 SLUICE_LLM_API_KEY（复制 .env.example 为 .env 并填写）", file=sys.stderr)
        sys.exit(1)

    registry = ToolRegistry()
    registry.register(EchoTool())
    guardrails = GuardrailChain(settings) if settings.guardrails_enabled else None
    runtime = ReActRuntime(
        LLMClient(settings),
        registry,
        max_rounds=settings.react_max_rounds,
        guardrails=guardrails,
    )

    print(f"指令: {message}\n---")
    try:
        final = runtime.run(message)
    except SluiceError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        sys.exit(1)

    for event in runtime.trace:
        print(f"第 {event['round']} 轮")
        if event["thought"]:
            print(f"  Thought: {event['thought']}")
        for tc in event["tool_calls"]:
            status = "错误" if tc["is_error"] else "观测"
            print(f"  Action: {tc['name']}({tc['args']})")
            print(f"  {status}: {tc['observation']}")
    print("---")
    print(f"最终回答: {final}")


if __name__ == "__main__":
    main()
