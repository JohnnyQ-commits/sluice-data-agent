from sluice.config.settings import Settings


def test_defaults():
    s = Settings(llm_api_key="test-key")
    assert s.llm_base_url == "https://api.anthropic.ai"
    assert s.llm_model == "claude-sonnet-5-5"
    assert s.llm_timeout_seconds == 120.0
    assert s.llm_max_retries == 3
    assert s.react_max_rounds == 10


def test_env_prefix(monkeypatch):
    monkeypatch.setenv("SLUICE_LLM_MODEL", "claude-haiku-5-5")
    s = Settings(llm_api_key="test-key")
    assert s.llm_model == "claude-haiku-5-5"
