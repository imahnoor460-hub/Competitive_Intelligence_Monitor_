"""arq worker entrypoint:  arq app.worker.WorkerSettings

Runs as a second Northflank service on the same image as the web app — it
needs the identical Chromium install and the identical NIM configuration, so
sharing the image is the point rather than an economy.

Every task here is a thin async wrapper over a *synchronous* job runner that
already existed and is unchanged. The wrapping matters: the runners do
blocking database, browser and HTTP work, so calling one directly from an
async task would block the worker's event loop and make `max_jobs`
concurrency meaningless. `asyncio.to_thread` keeps the loop free to accept
and heartbeat other jobs.

One consequence to be aware of: arq enforces `job_timeout` by cancelling the
coroutine, and a thread running blocking code cannot be cancelled. On
timeout the job is marked failed and the worker moves on, but the thread
finishes its work in the background. That is survivable precisely because
each runner writes its own terminal status to Postgres, and because the
per-surface database guard stops a late finisher from colliding with a retry.
"""

import asyncio
import logging

from arq.connections import RedisSettings as _RedisSettings

from app.core.config import settings

logger = logging.getLogger(__name__)


async def run_briefing_job(ctx, job_id: int) -> None:
    from app.services.briefing_service import run_briefing_job as _run

    await asyncio.to_thread(_run, job_id)


async def run_battlecard_update_job(ctx, job_id: int) -> None:
    from app.services.battlecard_service import run_battlecard_update_job as _run

    await asyncio.to_thread(_run, job_id)


async def run_competitor_discovery_job(ctx, job_id: int) -> None:
    from app.services.competitor_discovery_service import (
        run_competitor_discovery_job as _run,
    )

    await asyncio.to_thread(_run, job_id)


async def execute_surface_check(ctx, check_run_id: int) -> None:
    from app.services.check_service import execute_surface_check as _run

    await asyncio.to_thread(_run, check_run_id)


async def on_startup(ctx) -> None:
    """Log the resolved NIM configuration on every worker boot.

    The web service and this worker are separate Northflank services with
    separate environments, and nothing in the code can stop them being given
    different model names. A divergent embedding model is the dangerous case:
    find_similar_changes compares only embeddings whose `model` matches, so a
    worker writing vectors under a different model name produces rows that
    are silently invisible to similarity search — no error, no failed job.
    Logging both names at startup makes that diagnosable from the worker's
    first line of output instead of from missing search results weeks later.
    """

    logger.warning(
        "arq worker starting | chat_model=%s | embed_model=%s | "
        "llm_configured=%s | max_jobs=%s | job_timeout=%ss",
        settings.nvidia_chat_model,
        settings.nvidia_embed_model,
        bool(settings.nvidia_api_key),
        settings.arq_max_jobs,
        settings.arq_job_timeout,
    )


class WorkerSettings:
    functions = [
        run_briefing_job,
        run_battlecard_update_job,
        run_competitor_discovery_job,
        execute_surface_check,
    ]

    on_startup = on_startup

    max_jobs = settings.arq_max_jobs
    job_timeout = settings.arq_job_timeout

    # Retries are off by default and deliberately so. arq re-invokes a task
    # from the top, and these runners spend real money: a retried briefing or
    # check re-runs materiality scoring, the baseline summary and the site
    # summary, re-billing NIM tokens against the workspace budget for work
    # that may have already partly succeeded. Each runner instead catches its
    # own failures and records them on its job row, which matches the
    # graceful-degradation behaviour the pipeline already had. Per-job
    # overrides belong on the enqueue call, not here.
    max_tries = 1

    # Keep finished job metadata briefly. The Postgres row is the record that
    # matters, so there is nothing to gain from retaining results in Redis.
    keep_result = 300

    # Resolved at import. The worker is only ever started with REDIS_URL set,
    # and failing here with a clear message beats silently connecting to a
    # default localhost that happens to be empty and then never running a job.
    if not settings.redis_url:
        raise RuntimeError(
            "REDIS_URL is not configured — the arq worker has no queue to read. "
            "Set REDIS_URL on this service (it must match the web service's)."
        )

    redis_settings = _RedisSettings.from_dsn(settings.redis_url)
