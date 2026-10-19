from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。env 前缀 SLUICE_，支持 .env 文件注入。"""

    model_config = SettingsConfigDict(env_prefix="SLUICE_", env_file=".env", extra="ignore")

    # LLM 网关
    llm_base_url: str = "https://api.anthropic.ai"
    llm_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 3
    llm_max_tokens: int = 4096

    # ReAct 循环
    react_max_rounds: int = 10

    # Guardrails（P2）
    guardrails_enabled: bool = True
    react_token_budget: int = 200_000
    audit_dir: str = "audit"

    # Web / 数据
    demo_db_path: str = "data/demo.db"
