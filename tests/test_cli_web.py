import pytest

from sluice.cli import main as cli_main
from sluice.cli.main import build_parser


def test_web_subcommand_parses():
    args = build_parser().parse_args(["web", "--port", "5002"])
    assert args.command == "web" and args.port == 5002 and args.host == "127.0.0.1"


def test_web_defaults():
    args = build_parser().parse_args(["web"])
    assert args.command == "web" and args.port == 5001 and args.host == "127.0.0.1"


def test_web_missing_api_key_exits_with_hint(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("SLUICE_LLM_API_KEY", raising=False)
    monkeypatch.setenv("SLUICE_LLM_API_KEY", "")  # 盖过可能的 .env 读取
    monkeypatch.chdir(tmp_path)  # 避免读到仓库 .env
    args = build_parser().parse_args(["web"])
    with pytest.raises(SystemExit):
        cli_main._web(args)
    assert "SLUICE_LLM_API_KEY" in capsys.readouterr().err


def test_list_skills_missing_dir_returns_empty_without_raising(tmp_path):
    assert cli_main._list_skills(tmp_path / "no_such_skills") == []
