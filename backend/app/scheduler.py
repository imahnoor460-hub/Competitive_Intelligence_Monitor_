import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.base import JobLookupError

from app.database import SessionLocal
from app.models.surface import Surface
from app.models.briefing import Briefing, BriefingStatus, BriefingDigestType
from app.queue import JobSpec, dispatch_job, queue_is_configured
from app.services.check_service import (
    enqueue_surface_check,
    execute_surface_check,
    run_surface_check,
)
from app.services.snapshot_service import FetchError

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# In-process, single-instance scheduler (per project decision — no Redis/Celery).
# Running more than one app process/replica will double-fire these jobs; if this
# ever needs to scale horizontally, replace this module with a Redis-backed queue
# (Celery/RQ) rather than adding locking here.
_FREQUENCY_INTERVALS = {
    "hourly": {"hours": 1},
    "daily": {"hours": 24},
    "weekly": {"days": 7},
}


def _job_id(surface_id: int) -> str:
    return f"surface-check-{surface_id}"


def run_scheduled_check(surface_id: int) -> None:
    db = SessionLocal()
    try:
        surface = db.query(Surface).filter(Surface.id == surface_id).first()
        if surface is None or not surface.is_active:
            return

        if queue_is_configured():
            # Hand the browser and LLM work to the worker rather than doing it
            # here. This is what stops scheduled checks competing with request
            # handling for the web process's threadpool — the tick now costs
            # one INSERT and one enqueue. Passing background_tasks=None is
            # deliberate: there is no request to attach to, and with a queue
            # configured none is needed.
            check_run, created = enqueue_surface_check(db, surface)
            if not created:
                logger.info("Surface %s already has a check in flight", surface_id)
                return

            dispatch_job(
                None,
                JobSpec(
                    task_name="execute_surface_check",
                    fn=execute_surface_check,
                    args=(check_run.id,),
                    job_id=f"check:run:{check_run.id}",
                ),
            )
            logger.info("Scheduled check for surface %s queued", surface_id)
            return

        try:
            result = run_surface_check(db, surface)
            logger.info("Scheduled check for surface %s: %s", surface_id, result.get("status"))
        except FetchError as exc:
            logger.warning("Scheduled check failed for surface %s: %s", surface_id, exc)
    finally:
        db.close()


def schedule_surface(surface: Surface) -> None:
    interval = _FREQUENCY_INTERVALS.get(surface.check_frequency, _FREQUENCY_INTERVALS["daily"])

    # Anchor next_run_time off last_checked_at rather than letting
    # IntervalTrigger default to "now" — this is a single-process, in-memory
    # scheduler (see module docstring), so every app restart (every --reload
    # in dev, every deploy) re-adds every surface's job from scratch. Without
    # this, a restart happening more often than the check interval means the
    # "next run" keeps getting pushed a full interval into the future and a
    # surface's checks silently stop firing forever. If a check is already
    # overdue, run it on the next tick instead of waiting out another full
    # interval. Omit the kwarg entirely (rather than passing None) when
    # there's no prior check yet — APScheduler treats an explicit
    # next_run_time=None as "add this job paused," not "use the default."
    next_run_kwargs = {}
    if surface.last_checked_at is not None:
        due = surface.last_checked_at + timedelta(**interval)
        next_run_kwargs["next_run_time"] = max(due, datetime.utcnow())

    scheduler.add_job(
        run_scheduled_check,
        trigger=IntervalTrigger(**interval),
        args=[surface.id],
        id=_job_id(surface.id),
        replace_existing=True,
        misfire_grace_time=300,
        jitter=60,
        **next_run_kwargs,
    )


def unschedule_surface(surface_id: int) -> None:
    try:
        scheduler.remove_job(_job_id(surface_id))
    except JobLookupError:
        pass


def _run_digests_for_type(digest_type: BriefingDigestType) -> None:
    from app.services.delivery.delivery_service import deliver_digest

    db = SessionLocal()
    try:
        workspace_ids = [
            row[0] for row in
            db.query(Briefing.workspace_id)
            .filter(Briefing.status == BriefingStatus.approved, Briefing.digest_type == digest_type)
            .distinct()
            .all()
        ]
    finally:
        db.close()

    for workspace_id in workspace_ids:
        digest_db = SessionLocal()
        try:
            deliver_digest(digest_db, workspace_id, digest_type)
        except Exception as exc:  # noqa: BLE001 — one workspace's digest failing must not skip the rest
            logger.warning("Digest delivery failed for workspace %s: %s", workspace_id, exc)
        finally:
            digest_db.close()


def _run_daily_digests() -> None:
    _run_digests_for_type(BriefingDigestType.daily)


def _run_weekly_digests() -> None:
    _run_digests_for_type(BriefingDigestType.weekly)


def schedule_digest_jobs() -> None:
    """Two global cron jobs (not one per workspace) — cadence is a fixed
    daily/weekly schedule rather than a per-surface interval, so each job
    just queries across every workspace that has approved, undelivered
    briefings at that cadence when it fires.
    """
    scheduler.add_job(
        _run_daily_digests, CronTrigger(hour=8, minute=0),
        id="digest-daily", replace_existing=True,
    )
    scheduler.add_job(
        _run_weekly_digests, CronTrigger(day_of_week="mon", hour=8, minute=0),
        id="digest-weekly", replace_existing=True,
    )


def _run_job_reconciler() -> None:
    from app.services.job_reconciler import reconcile_stuck_jobs

    try:
        reconcile_stuck_jobs()
    except Exception as exc:  # noqa: BLE001 — a failed sweep must not kill the scheduler
        logger.warning("Job reconciler pass failed: %s", exc)


def schedule_job_reconciler() -> None:
    """Resolve jobs the queue never delivered or a worker died holding.

    Runs in the web process rather than the worker on purpose: the case it
    exists to catch is precisely the one where no worker is consuming, so a
    reconciler living in the worker would be asleep exactly when it is needed.
    The pass is two indexed queries and, in the normal case, changes nothing.
    """

    scheduler.add_job(
        _run_job_reconciler,
        trigger=IntervalTrigger(minutes=5),
        id="job-reconciler",
        replace_existing=True,
        misfire_grace_time=120,
        jitter=30,
    )


def start_scheduler() -> None:
    db = SessionLocal()
    try:
        active_surfaces = db.query(Surface).filter(Surface.is_active.is_(True)).all()
        for surface in active_surfaces:
            schedule_surface(surface)
    finally:
        db.close()

    schedule_digest_jobs()
    schedule_job_reconciler()
    scheduler.start()


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
