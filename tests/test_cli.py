import pytest

from sluice.cli import main as cli_main


def test_missing_api_key_exits_with_hint(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("SLUICE_LLM_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # 避免读到仓库 .env
    with pytest.raises(SystemExit):
        cli_main._run("你好")
    assert "SLUICE_LLM_API_KEY" in capsys.readouterr().err


def test_main_help():
    with pytest.raises(SystemExit) as exc_info:
        cli_main.main(["--help"])
    assert exc_info.value.code == 0
