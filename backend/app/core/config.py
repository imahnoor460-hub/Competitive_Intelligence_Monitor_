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

    # Off by default, and that default is the deployed configuration. Page
    # discovery is sitemap-first (see surface_discovery_service); the browser
    # path is kept only for environments with memory to spare. Measured on a
    # real storefront: one Chromium discovery pass peaked at ~596MB resident
    # against a 512MB container limit, so leaving this on is an OOM, not a
    # slow path. Turn it on only where the worker has ~1GB.
    enable_browser_discovery: bool = False

    # Same reasoning for the site-summary path, which renders a page per
    # surface. Off by default: site_summary_service fetches over plain HTTP
    # and only consults the browser when the HTTP body is short enough to
    # look JavaScript-empty, so this gates that last resort rather than the
    # normal path.
    enable_browser_rendering: bool = False

    # Each concurrent job can hold a Chromium process (~300MB resident), so
    # this is bounded by worker memory rather than CPU. Start low.
    arq_max_jobs: int = 2

    # Generous, because a site-summary fan-out across a 40-surface competitor
    # is minutes of browser work by design (see site_summary_service).
    arq_job_timeout: int = 900

    # How many pages of one competitor are actually watched: scheduled daily,
    # and swept by "Run check now". Discovery finds up to 40 per pass and a
    # storefront's sitemap can offer hundreds, but every watched page is a
    # daily HTTP fetch plus, when it changes, an LLM call — on 0.2 shared CPU
    # a workspace of 8 competitors at 40 pages each is 320 daily checks and a
    # sweep that takes an hour.
    #
    # Three is the homepage plus the two highest-ranked business pages (see
    # services/surface_selection.py, which reads a page's role from its URL
    # rather than trusting discovery order). Everything else discovery finds is
    # stored with is_active=false: not swept, not scheduled, not checked by
    # anything — kept only so a user can turn one on by hand rather than
    # having it silently discarded.
    max_active_surfaces_per_competitor: int = 3

    # Bounds on a single page fetch (services/snapshot.py). Split because
    # they fail differently: a dead host trips connect, a stalled response
    # trips read, and a server dribbling one byte at a time trips neither —
    # requests' timeout is per socket operation, not per request, so a slow
    # drip resets it forever. http_total_timeout is the wall-clock ceiling
    # that makes a fetch genuinely bounded, and http_max_bytes stops a
    # multi-gigabyte body from being read into a 512MB container.
    http_connect_timeout: float = 5.0
    http_read_timeout: float = 10.0
    http_total_timeout: float = 25.0
    http_max_bytes: int = 3_000_000

    # The OpenAI SDK defaults to a 600-second timeout, which is longer than
    # any job here should live: a check that hangs on materiality scoring
    # holds a worker slot (of two) for ten minutes and looks stuck to the
    # user. Best-effort LLM steps catch the timeout and degrade, exactly as
    # they already do for any other provider failure.
    llm_request_timeout: float = 90.0

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
