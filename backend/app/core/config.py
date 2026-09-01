from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret_key: str
    jwt_algorithm: str
    access_token_expire_minutes: int

    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    # NIM retires hosted models on published end-of-life dates, after which
    # every call returns 410 Gone — a pinned default WILL break eventually
    # (nvidia/nemotron-3-nano-30b-a3b died 2026-09-01, nv-embedqa-e5-v5
    # 2026-08). Both are overridable via env so a deployer can move to a
    # live model without waiting on a code change; NIM_* is the documented
    # name, NVIDIA_* is kept so existing deployments keep working.
    nvidia_chat_model: str = Field(
        default="nvidia/nemotron-3-super-120b-a12b",
        validation_alias=AliasChoices("NIM_CHAT_MODEL", "NVIDIA_CHAT_MODEL"),
    )
    # 2048-dim. Changing this invalidates every stored ChangeEmbedding.vector:
    # find_similar_changes compares vectors across rows with zip(), which
    # silently truncates to the shorter of two differing dimensions rather
    # than raising, so mixed-model rows yield quietly wrong similarity.
    nvidia_embed_model: str = Field(
        default="nvidia/nemotron-3-embed-1b",
        validation_alias=AliasChoices("NIM_EMBED_MODEL", "NVIDIA_EMBED_MODEL"),
    )

    # Queue transport for background jobs (arq). Redis carries the job
    # *message* only — every job's status, result and error lives in its
    # Postgres row, which stays the single source of truth. Losing Redis
    # therefore costs throughput, never a user-visible record; the
    # job_reconciler resolves rows whose message never arrived.
    #
    # None means "no queue configured": enqueue falls back to running the
    # job inline, which is what keeps the test suite and a Redis-less local
    # dev environment working exactly as they did before.
    redis_url: str | None = None

    # Each concurrent job can hold a Chromium process (~300MB resident), so
    # this is bounded by worker memory rather than CPU. Start low.
    arq_max_jobs: int = 2

    # Generous, because a site-summary fan-out across a 40-surface competitor
    # is minutes of browser work by design (see site_summary_service).
    arq_job_timeout: int = 900

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
        "https://competitive-intelligence-monitor.vercel.app",
    ]


settings = Settings()
