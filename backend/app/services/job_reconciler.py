"""Resolve job rows the queue never delivered, or a worker died holding.

Postgres is the source of truth for job status; Redis only carries the
message. That split is what makes losing Redis survivable — but it also
means a row can be left describing work that is no longer going to happen:

* **Never delivered.** The row committed as `queued`, then the enqueue
  failed, or Redis restarted without persistence before a worker popped it.
  Nothing in Redis will ever run it, and nothing else would ever notice.
* **Abandoned mid-flight.** A worker was killed — a deploy, an OOM, a
  container reschedule — after flipping the row to `running`. arq's own
  retry cannot help, because the row is not queued any more.

Without this, the first case leaves a job queued forever and the frontend
polling forever; the second leaves it running forever. Only CheckRun had any
recovery before (a per-surface stale reclaim), and only when a *later* check
of the same surface happened to run.

Re-enqueueing rather than failing is safe for the queued case because every
runner is idempotent: each re-reads its row and returns early unless it is
still in the state it expects. If the original message does turn out to be
alive in Redis, arq refuses the duplicate id and nothing happens twice.
"""

import logging
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models.battlecard_update_job import (
    BattlecardUpdateJob,
    BattlecardUpdateJobStatus,
)
from app.models.briefing_job import BriefingJob, BriefingJobStatus
from app.models.check_run import CheckRun, CheckRunStatus
from app.models.check_sweep import CheckSweep, CheckSweepStatus
from app.models.competitor_discovery_job import (
    CompetitorDiscoveryJob,
    CompetitorDiscoveryJobStatus,
)
from app.models.site_summary_job import SiteSummaryJob, SiteSummaryJobStatus
from app.queue import JobSpec, dispatch_jobs, queue_is_configured

__all__ = ["reconcile_stuck_jobs"]

logger = logging.getLogger(__name__)

# Long enough that an ordinary queue backlog is never mistaken for a lost
# message — a worker at max_jobs=2 chewing through a sweep of slow checks can
# legitimately leave later jobs queued for a while.
_QUEUED_REDELIVER_MINUTES = 10

# Comfortably beyond arq_job_timeout (900s / 15 min), so a job that is merely
# slow is never killed by the reconciler; only one whose worker is genuinely
# gone reaches this.
_RUNNING_ABANDONED_MINUTES = 30

# A sweep whose children are all resolved but which never got closed — every
# child finishing at once can race the counter update. Rare, cheap to fix.
_SWEEP_STUCK_MINUTES = 45

# The hard boundary: a sweep this old is closed whether or not its children
# resolved, failing the ones still outstanding. Without it a sweep with a
# single permanently-queued child — a message Redis lost that keeps being
# re-enqueued into a queue nothing is consuming — stays `running` forever, and
# the frontend polls it forever. Comfortably past _SWEEP_STUCK_MINUTES so the
# ordinary "children all finished" close always gets there first.
_SWEEP_MAX_MINUTES = 90


def _timestamp_column(model):
    """Jobs use created_at; check runs use started_at."""
    return getattr(model, "created_at", None) or model.started_at


def reconcile_stuck_jobs() -> dict[str, int]:
    """One pass. Returns a count per action, for logging and tests."""

    counts = {"redelivered": 0, "failed": 0, "sweeps_closed": 0}
    now = datetime.utcnow()
    queued_before = now - timedelta(minutes=_QUEUED_REDELIVER_MINUTES)
    running_before = now - timedelta(minutes=_RUNNING_ABANDONED_MINUTES)

    db = SessionLocal()
    try:
        # --- abandoned mid-flight -> failed --------------------------------
        for model, status_enum, label in (
            (BriefingJob, BriefingJobStatus, "briefing"),
            (BattlecardUpdateJob, BattlecardUpdateJobStatus, "battlecard update"),
            (CompetitorDiscoveryJob, CompetitorDiscoveryJobStatus, "discovery"),
            (SiteSummaryJob, SiteSummaryJobStatus, "site summary"),
            (CheckRun, CheckRunStatus, "check"),
        ):
            stamp = _timestamp_column(model)
            abandoned = (
                db.query(model)
                .filter(model.status == status_enum.running, stamp < running_before)
                .all()
            )
            for row in abandoned:
                row.status = status_enum.failed
                row.error = (
                    f"Abandoned: no worker reported an outcome within "
                    f"{_RUNNING_ABANDONED_MINUTES} minutes"
                )
                row.finished_at = now
                counts["failed"] += 1
                logger.warning("Reconciled abandoned %s job %s", label, row.id)
        db.commit()

        # --- never delivered -> re-enqueue ---------------------------------
        if queue_is_configured():
            specs: list[JobSpec] = []

            from app.services.battlecard_service import run_battlecard_update_job
            from app.services.briefing_service import run_briefing_job
            from app.services.check_service import execute_surface_check
            from app.services.competitor_discovery_service import (
                run_competitor_discovery_job,
            )
            from app.services.site_summary_service import run_site_summary_job

            for model, status_enum, task_name, fn, prefix in (
                (BriefingJob, BriefingJobStatus, "run_briefing_job",
                 run_briefing_job, "briefing"),
                (BattlecardUpdateJob, BattlecardUpdateJobStatus,
                 "run_battlecard_update_job", run_battlecard_update_job, "battlecard"),
                (CompetitorDiscoveryJob, CompetitorDiscoveryJobStatus,
                 "run_competitor_discovery_job", run_competitor_discovery_job,
                 "discovery"),
                (SiteSummaryJob, SiteSummaryJobStatus, "run_site_summary_job",
                 run_site_summary_job, "site-summary"),
                (CheckRun, CheckRunStatus, "execute_surface_check",
                 execute_surface_check, "check:run"),
            ):
                stamp = _timestamp_column(model)
                undelivered = (
                    db.query(model.id)
                    .filter(model.status == status_enum.queued, stamp < queued_before)
                    .all()
                )
                for (row_id,) in undelivered:
                    specs.append(
                        JobSpec(
                            task_name=task_name,
                            fn=fn,
                            args=(row_id,),
                            job_id=f"{prefix}:{row_id}",
                        )
                    )
                    logger.warning(
                        "Re-enqueueing undelivered %s job %s", task_name, row_id
                    )

            if specs:
                results = dispatch_jobs(None, specs)
                counts["redelivered"] = sum(1 for created in results if created)

        # --- sweeps past the hard boundary ---------------------------------
        expired_sweeps = (
            db.query(CheckSweep)
            .filter(
                CheckSweep.status.in_(
                    [CheckSweepStatus.queued, CheckSweepStatus.running]
                ),
                CheckSweep.created_at < now - timedelta(minutes=_SWEEP_MAX_MINUTES),
            )
            .all()
        )
        for sweep in expired_sweeps:
            outstanding = (
                db.query(CheckRun)
                .filter(
                    CheckRun.sweep_id == sweep.id,
                    CheckRun.status.in_(
                        [CheckRunStatus.queued, CheckRunStatus.running]
                    ),
                )
                .all()
            )
            for run in outstanding:
                run.status = CheckRunStatus.failed
                run.error = (
                    f"Abandoned: its sweep was closed after "
                    f"{_SWEEP_MAX_MINUTES} minutes"
                )
                run.finished_at = now
                counts["failed"] += 1

            # Counters set rather than incremented: the whole point of this
            # branch is that the per-child bookkeeping did not happen, so
            # trusting it here would leave the sweep short of total again.
            sweep.failed_count += len(outstanding)
            sweep.finished = sweep.total
            sweep.status = (
                CheckSweepStatus.failed
                if sweep.total > 0 and sweep.failed_count >= sweep.total
                else CheckSweepStatus.success
            )
            sweep.finished_at = now
            counts["sweeps_closed"] += 1
            logger.warning(
                "Closed check sweep %s at the %s-minute boundary with %s "
                "unresolved check(s)", sweep.id, _SWEEP_MAX_MINUTES, len(outstanding),
            )
        db.commit()

        # --- sweeps whose children all resolved but never closed -----------
        stuck_sweeps = (
            db.query(CheckSweep)
            .filter(
                CheckSweep.status.in_(
                    [CheckSweepStatus.queued, CheckSweepStatus.running]
                ),
                CheckSweep.created_at < now - timedelta(minutes=_SWEEP_STUCK_MINUTES),
            )
            .all()
        )
        for sweep in stuck_sweeps:
            outstanding = (
                db.query(CheckRun)
                .filter(
                    CheckRun.sweep_id == sweep.id,
                    CheckRun.status.in_(
                        [CheckRunStatus.queued, CheckRunStatus.running]
                    ),
                )
                .count()
            )
            if outstanding:
                continue

            sweep.status = (
                CheckSweepStatus.failed
                if sweep.total > 0 and sweep.failed_count >= sweep.total
                else CheckSweepStatus.success
            )
            sweep.finished_at = now
            counts["sweeps_closed"] += 1
            logger.warning("Closed stuck check sweep %s", sweep.id)

        db.commit()
    finally:
        db.close()

    if any(counts.values()):
        logger.warning("Job reconciler: %s", counts)

    return counts
