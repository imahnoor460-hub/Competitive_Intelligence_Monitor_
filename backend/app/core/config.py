from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret_key: str
    jwt_algorithm: str
    access_token_expire_minutes: int

    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_chat_model: str = "nvidia/nemotron-3-nano-30b-a3b"
    nvidia_embed_model: str = "nvidia/nv-embedqa-e5-v5"

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None

    snapshot_storage_path: str = "./storage"

    # Blended estimate applied to (prompt_tokens + completion_tokens) from
    # every TokenUsageLog row — NIM's community API pricing isn't published
    # per-token the way OpenAI's is, so this is a conservative placeholder
    # rate a deployer should tune to their actual plan. Defaulting it to a
    # small non-zero value (rather than 0) means WorkspaceBudget enforcement
    # is exercised out of the box instead of silently doing nothing until
    # someone remembers to configure it.
    llm_cost_per_1k_tokens_usd: float = 0.002

    rate_limit_llm_requests: int = 20
    rate_limit_llm_window_seconds: float = 60.0

    # Traffic estimates (SimilarWeb) — informational only, not wired into
    # materiality scoring. None until a deployer configures a real key,
    # matching the same optional-integration pattern as nvidia_api_key.
    similarweb_api_key: str | None = None
    similarweb_base_url: str = "https://api.similarweb.com/v1"

    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "https://your-frontend.vercel.app",
    ]


settings = Settings()
