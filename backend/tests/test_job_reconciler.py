from datetime import datetime, timedelta

from app.models.briefing import BriefingAudience, BriefingDigestType
from app.models.briefing_job import BriefingJob, BriefingJobStatus
from app.models.check_run import CheckRun, CheckRunStatus
from app.models.check_sweep import CheckSweep, CheckSweepStatus
from app.models.competitor import Competitor
from app.models.surface import Surface, SurfaceType
from app.models.user import User
from app.models.workspace import Workspace
from app.services.job_reconciler import (
    _QUEUED_REDELIVER_MINUTES,
    _RUNNING_ABANDONED_MINUTES,
    _SWEEP_STUCK_MINUTES,
    reconcile_stuck_jobs,
)


def _workspace_with_surface(db_session):
    user = User(email="rec@example.com", hashed_password="x", full_name="Rec")
    db_session.add(user)
    db_session.flush()

    workspace = Workspace(name="Rec", slug="rec")
    db_session.add(workspace)
    db_session.flush()

    competitor = Competitor(
        name="Rival", workspace_id=workspace.id, created_by_user_id=user.id
    )
    db_session.add(competitor)
    db_session.flush()

    surface = Surface(
        competitor_id=competitor.id,
        surface_type=SurfaceType.pricing,
        url="https://rival.example.com",
    )
    db_session.add(surface)
    db_session.commit()

    return workspace, surface


def _ago(minutes):
    return datetime.utcnow() - timedelta(minutes=minutes)


def test_abandoned_running_check_is_marked_failed(db_session):
    """A worker killed mid-check leaves a row at `running` that nothing else
    would ever resolve."""

    _workspace, surface = _workspace_with_surface(db_session)

    abandoned = CheckRun(
        surface_id=surface.id,
        status=CheckRunStatus.running,
        started_at=_ago(_RUNNING_ABANDONED_MINUTES + 5),
    )
    db_session.add(abandoned)
    db_session.commit()

    counts = reconcile_stuck_jobs()

    db_session.refresh(abandoned)
    assert counts["failed"] == 1
    assert abandoned.status == CheckRunStatus.failed
    assert abandoned.finished_at is not None
    assert "Abandoned" in abandoned.error


def test_a_check_that_is_merely_slow_is_left_alone(db_session):
    """The threshold sits well beyond arq's job timeout precisely so a long
    site-summary fan-out is never mistaken for a dead worker."""

    _workspace, surface = _workspace_with_surface(db_session)

    slow = CheckRun(
        surface_id=surface.id,
        status=CheckRunStatus.running,
        started_at=_ago(_RUNNING_ABANDONED_MINUTES - 5),
    )
    db_session.add(slow)
    db_session.commit()

    counts = reconcile_stuck_jobs()

    db_session.refresh(slow)
    assert counts["failed"] == 0
    assert slow.status == CheckRunStatus.running


def test_abandoned_briefing_job_is_marked_failed(db_session):
    """Before this, only CheckRun had any recovery — the three job tables
    would sit at `running` forever after a deploy killed their worker."""

    workspace, _surface = _workspace_with_surface(db_session)

    job = BriefingJob(
        workspace_id=workspace.id,
        audience=BriefingAudience.sales,
        digest_type=BriefingDigestType.daily,
        change_log_ids=[],
        status=BriefingJobStatus.running,
        created_at=_ago(_RUNNING_ABANDONED_MINUTES + 5),
    )
    db_session.add(job)
    db_session.commit()

    reconcile_stuck_jobs()

    db_session.refresh(job)
    assert job.status == BriefingJobStatus.failed
    assert job.finished_at is not None


def test_undelivered_queued_rows_are_not_touched_without_a_queue(db_session):
    """With no queue configured the work went to BackgroundTasks, so a queued
    row is not evidence of a lost message and must not be re-dispatched."""

    _workspace, surface = _workspace_with_surface(db_session)

    queued = CheckRun(
        surface_id=surface.id,
        status=CheckRunStatus.queued,
        started_at=_ago(_QUEUED_REDELIVER_MINUTES + 5),
    )
    db_session.add(queued)
    db_session.commit()

    counts = reconcile_stuck_jobs()

    db_session.refresh(queued)
    assert counts["redelivered"] == 0
    assert queued.status == CheckRunStatus.queued


def test_undelivered_queued_rows_are_re_enqueued_when_a_queue_exists(
    db_session, monkeypatch
):
    _workspace, surface = _workspace_with_surface(db_session)

    queued = CheckRun(
        surface_id=surface.id,
        status=CheckRunStatus.queued,
        started_at=_ago(_QUEUED_REDELIVER_MINUTES + 5),
    )
    fresh = CheckRun(
        surface_id=surface.id,
        status=CheckRunStatus.queued,
        started_at=datetime.utcnow(),
    )
    db_session.add_all([queued, fresh])
    db_session.commit()

    dispatched = []

    import app.services.job_reconciler as reconciler

    monkeypatch.setattr(reconciler, "queue_is_configured", lambda: True)
    monkeypatch.setattr(
        reconciler,
        "dispatch_jobs",
        lambda bt, specs: dispatched.extend(specs) or [True] * len(specs),
    )

    counts = reconcile_stuck_jobs()

    assert counts["redelivered"] == 1
    # Only the stale one — a row queued seconds ago is an ordinary backlog.
    assert [spec.args for spec in dispatched] == [(queued.id,)]
    assert dispatched[0].job_id == f"check:run:{queued.id}"


def test_a_sweep_whose_children_all_finished_is_closed(db_session):
    _workspace, surface = _workspace_with_surface(db_session)

    sweep = CheckSweep(
        workspace_id=_workspace.id,
        status=CheckSweepStatus.running,
        total=2,
        finished=1,          # a counter update was lost
        failed_count=0,
        created_at=_ago(_SWEEP_STUCK_MINUTES + 5),
    )
    db_session.add(sweep)
    db_session.flush()

    db_session.add_all([
        CheckRun(surface_id=surface.id, status=CheckRunStatus.success, sweep_id=sweep.id),
        CheckRun(surface_id=surface.id, status=CheckRunStatus.success, sweep_id=sweep.id),
    ])
    db_session.commit()

    counts = reconcile_stuck_jobs()

    db_session.refresh(sweep)
    assert counts["sweeps_closed"] == 1
    assert sweep.status == CheckSweepStatus.success
    assert sweep.finished_at is not None


def test_a_sweep_with_children_still_running_is_left_open(db_session):
    _workspace, surface = _workspace_with_surface(db_session)

    sweep = CheckSweep(
        workspace_id=_workspace.id,
        status=CheckSweepStatus.running,
        total=2,
        finished=1,
        failed_count=0,
        created_at=_ago(_SWEEP_STUCK_MINUTES + 5),
    )
    db_session.add(sweep)
    db_session.flush()

    db_session.add_all([
        CheckRun(surface_id=surface.id, status=CheckRunStatus.success, sweep_id=sweep.id),
        CheckRun(
            surface_id=surface.id,
            status=CheckRunStatus.running,
            sweep_id=sweep.id,
            started_at=datetime.utcnow(),
        ),
    ])
    db_session.commit()

    counts = reconcile_stuck_jobs()

    db_session.refresh(sweep)
    assert counts["sweeps_closed"] == 0
    assert sweep.status == CheckSweepStatus.running


def test_reconciler_is_a_no_op_when_nothing_is_stuck(db_session):
    _workspace, surface = _workspace_with_surface(db_session)

    healthy = CheckRun(surface_id=surface.id, status=CheckRunStatus.success)
    db_session.add(healthy)
    db_session.commit()

    assert reconcile_stuck_jobs() == {
        "redelivered": 0,
        "failed": 0,
        "sweeps_closed": 0,
    }
