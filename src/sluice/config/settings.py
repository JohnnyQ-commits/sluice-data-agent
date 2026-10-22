from pathlib import Path

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

    # Evals（P3）
    eval_judge_threshold: float = 4.0  # judge 有效项均分低于此值 → 用例 fail
    eval_judge_model: str = ""  # 空 = 复用 llm_model；judge 与被评模型可分离

    # MCP（P4）
    mcp_enabled: bool = False  # 默认关：无 .mcp.json 场景行为零变化
    mcp_config_path: str = ".mcp.json"  # 相对 repo 根

    # Web / 数据
    demo_db_path: str = "data/demo.db"


def resolve_audit_dir(settings: Settings) -> Settings:
    """audit 目录锚定 repo 根（settings.py 在 src/sluice/config/ 下，parents[3] = repo 根）。

    仅当 audit_dir 仍为默认值 "audit" 时才锚定，防 IDE/其他 cwd 启动时把 audit/ 写散；
    用户通过 env/.env 显式配置的 SLUICE_AUDIT_DIR 不被覆盖。
    """
    if settings.audit_dir != "audit":
        return settings
    return settings.model_copy(
        update={"audit_dir": str(Path(__file__).resolve().parents[3] / "audit")}
    )
