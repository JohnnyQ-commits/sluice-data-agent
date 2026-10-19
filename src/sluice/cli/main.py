"""sluice CLI。run：单次指令跑完整 ReAct 循环；web：启动 Web 界面。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..config.settings import Settings
from ..exceptions import SluiceError
from ..llm.client import LLMClient
from ..runtime.loop import ReActRuntime
from ..skills.loader import SkillLoader
from ..tools.echo import EchoTool
from ..tools.registry import ToolRegistry


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
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        _run(args.message)
    elif args.command == "web":
        _web(args)


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


def _web(args: argparse.Namespace) -> None:
    settings = Settings()
    if not settings.llm_api_key:
        print("缺少 SLUICE_LLM_API_KEY（复制 .env.example 为 .env 并填写）", file=sys.stderr)
        sys.exit(1)

    from ..web.app import create_app  # 延迟导入：run 子命令无需 Flask

    # skills 目录锚定 repo 根（main.py 在 src/sluice/cli/ 下，parents[3] = repo 根），
    # 防 IDE/其他 cwd 启动时静默丢 Skill；目录缺失时 SkillLoader 返回空清单自然降级
    skills_dir = Path(__file__).resolve().parents[3] / "skills"
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
    if not settings.llm_api_key:
        print("缺少 SLUICE_LLM_API_KEY（复制 .env.example 为 .env 并填写）", file=sys.stderr)
        sys.exit(1)

    registry = ToolRegistry()
    registry.register(EchoTool())
    runtime = ReActRuntime(LLMClient(settings), registry, max_rounds=settings.react_max_rounds)

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
