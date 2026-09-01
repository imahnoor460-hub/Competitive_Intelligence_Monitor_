"""The one seam between a request and a background job.

Routers call `dispatch_job` instead of touching arq directly, which buys
three things:

1. **A Redis-less fallback.** With no `REDIS_URL` configured, dispatch falls
   back to FastAPI's `BackgroundTasks` — exactly what every one of these
   call sites did before arq existed. The test suite and a local dev machine
   without Redis therefore behave identically to the way they always have,
   and nothing in `tests/` needs to know arq exists.

2. **A sync API over an async client.** Every router here is a sync `def`
   (they use the sync SQLAlchemy Session), while arq's client is asyncio.
   `_run_coro` bridges the two without imposing `async def` on routers,
   which would put blocking DB work on the event loop.

3. **Deterministic job ids in one place.** arq refuses to enqueue a job whose
   `_job_id` already exists in the queue or is currently running, so a
   double-clicked button or an overlapping scheduled check collapses to one
   job at the queue layer — before the per-job database guard is consulted.

Redis is the delivery mechanism, not the record: a job's status, result and
error live in its Postgres row. If Redis loses a message the row is left at
`queued`, which is what `services/job_reconciler.py` exists to resolve.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Sequence, TypeVar

from app.core.config import settings

__all__ = ["JobSpec", "dispatch_job", "dispatch_jobs", "queue_is_configured"]

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class JobSpec:
    """One unit of dispatchable work.

    `task_name` is the name arq registers (see worker.py `functions`);
    `fn` is the same work as a plain callable, used by the BackgroundTasks
    fallback. Both must refer to the same job, or the two paths diverge.
    """

    task_name: str
    fn: Callable[..., Any]
    args: tuple[Any, ...] = ()
    # Deterministic and unique per unit of work. Two dispatches sharing a key
    # collapse into one queued job.
    job_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def queue_is_configured() -> bool:
    return bool(settings.redis_url)


def _run_coro(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from sync code, whether or not a loop is already
    running in this thread.

    FastAPI runs sync endpoints in a threadpool and APScheduler runs sync
    jobs in its own executor, so in practice there is no running loop and
    `asyncio.run` applies. The fallback covers being called from a thread
    that does have one, where `asyncio.run` would raise rather than block.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _enqueue(specs: Sequence[JobSpec]) -> list[bool]:
    # Imported lazily so the package remains importable — and the whole test
    # suite keeps running — on an environment that has no arq installed.
    from arq import create_pool
    from arq.connections import RedisSettings

    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        results: list[bool] = []
        for spec in specs:
            job = await redis.enqueue_job(spec.task_name, *spec.args, _job_id=spec.job_id)
            # enqueue_job returns None when a job with this id already exists
            # (queued or in progress). That is a successful no-op, not an
            # error: the work is already going to happen exactly once.
            if job is None:
                logger.info(
                    "Job %s already queued, not enqueueing a duplicate", spec.job_id
                )
            results.append(job is not None)
        return results
    finally:
        await redis.aclose()


def dispatch_jobs(background_tasks, specs: Sequence[JobSpec]) -> list[bool]:
    """Dispatch a batch over one Redis connection.

    Returns one flag per spec: True when this call created the job, False
    when an identical job was already queued. Callers use that to keep
    counters honest rather than to detect failure.
    """

    if not specs:
        return []

    if not queue_is_configured():
        for spec in specs:
            if background_tasks is None:
                # No request to defer onto — the scheduler's tick already runs
                # off the request path, so running inline here is exactly the
                # behaviour it had before a queue existed.
                spec.fn(*spec.args)
            else:
                background_tasks.add_task(spec.fn, *spec.args)
        return [True] * len(specs)

    try:
        return _run_coro(_enqueue(specs))
    except Exception:
        # Deliberately no in-process fallback here. Once a queue is
        # configured, the caller's row is already committed as `queued`, so
        # the work is not lost — job_reconciler re-enqueues rows whose
        # message never landed. Running the job inline instead would put a
        # browser-and-LLM pipeline back in the web process at exactly the
        # moment the system is already unhealthy, and a fanned-out sweep
        # would do it dozens of times over. Degrading to "queued, picked up
        # shortly" is strictly better than degrading to "web service stalls".
        logger.exception(
            "Enqueue failed for %s; rows stay queued for the reconciler",
            [spec.job_id for spec in specs],
        )
        return [False] * len(specs)


def dispatch_job(background_tasks, spec: JobSpec) -> bool:
    return dispatch_jobs(background_tasks, [spec])[0]
