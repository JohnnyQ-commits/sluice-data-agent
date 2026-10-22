import pytest

from sluice.cli import main as cli_main
from sluice.evals.runner import ScriptedClient, _response_from_spec


def test_missing_api_key_exits_with_hint(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("SLUICE_LLM_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # 避免读到仓库 .env
    with pytest.raises(SystemExit):
        cli_main._run("你好")
    assert "SLUICE_LLM_API_KEY" in capsys.readouterr().err


def test_run_executes_weekly_report_tool(monkeypatch, capsys, tmp_path):
    """run 子命令挂载 Skill 后能真实调到 weekly-report 工具（demo 库查询，零 API key）。

    LLMClient 打桩为剧本回放：第 1 轮调 weekly-report.query_sales（对内置 demo 库
    跑真实 SQL），第 2 轮收尾——证明 _run 的 registry 真挂上了 Skill 工具而非只有 echo。
    """
    repo_root = cli_main._REPO_ROOT
    monkeypatch.setenv("SLUICE_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SLUICE_DEMO_DB_PATH", str(repo_root / "data" / "demo.db"))
    monkeypatch.setenv("SLUICE_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.chdir(tmp_path)
    script = ScriptedClient(
        [
            _response_from_spec(
                {
                    "tool": "weekly-report.query_sales",
                    "args": {
                        "sql": "SELECT city, SUM(amount) AS amt FROM sales "
                        "WHERE sale_date BETWEEN '2026-10-12' AND '2026-10-18' GROUP BY city"
                    },
                }
            ),
            _response_from_spec({"text": "周报正文"}),
        ]
    )
    monkeypatch.setattr(cli_main, "LLMClient", lambda settings: script)
    cli_main._run("生成本周销售周报")
    out = capsys.readouterr().out
    assert "weekly-report.query_sales" in out  # Skill 工具进了 registry
    assert "错误" not in out  # demo 库真实查询成功，非工具未找到的报错观测
    assert "周报正文" in out  # 循环正常收尾


def test_main_help():
    with pytest.raises(SystemExit) as exc_info:
        cli_main.main(["--help"])
    assert exc_info.value.code == 0


def test_parser_mcp_flags():
    args = cli_main.build_parser().parse_args(["mcp", "--call", "mcp__mini__echo", "--args", '{"text": "x"}'])
    assert args.command == "mcp" and args.call == "mcp__mini__echo"
