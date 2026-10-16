"""sluice CLI。P0 只有一个子命令：run（单次指令跑完整 ReAct 循环）。"""
from __future__ import annotations

import argparse
import sys

from ..config.settings import Settings
from ..exceptions import SluiceError
from ..llm.client import LLMClient
from ..runtime.loop import ReActRuntime
from ..tools.echo import EchoTool
from ..tools.registry import ToolRegistry


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="sluice", description="Sluice — 可挂载 Skill 的数据 Agent 平台"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="单次指令：跑完整 ReAct 循环并打印轨迹")
    run_p.add_argument("message", help="发给 Agent 的指令")
    args = parser.parse_args(argv)

    if args.command == "run":
        _run(args.message)


def _run(message: str) -> None:
    settings = Settings()
    if not settings.llm_api_key:
        print("缺少 SLUICE_LLM_API_KEY（复制 .env.example 为 .env 并填写）", file=sys.stderr)
        sys.exit(1)

    registry = ToolRegistry()
    registry.register(EchoTool())
    runtime = ReActRuntime(
        LLMClient(settings), registry, max_rounds=settings.react_max_rounds
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
