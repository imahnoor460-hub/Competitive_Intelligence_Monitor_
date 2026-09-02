from datetime import datetime

from pydantic import BaseModel

from app.models.check_run import CheckRunStatus
from app.models.check_sweep import CheckSweepStatus


class CheckRunResponse(BaseModel):
    id: int
    surface_id: int
    status: CheckRunStatus
    sweep_id: int | None = None
    # baseline_captured | no_change | change_detected once the run
    # succeeds; None while it is queued or running, or if it failed.
    outcome: str | None = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None

    class Config:
        from_attributes = True


class CheckSweepResponse(BaseModel):
    """Progress for one "check all" request.

    `finished`/`total` is what the UI shows while a sweep runs. `failed_count`
    is reported separately rather than folded into the status because a sweep
    where two of thirty surfaces failed is a normal, useful outcome, not a
    failure — see models/check_sweep.py.
    """

    id: int
    workspace_id: int
    status: CheckSweepStatus
    total: int
    finished: int
    failed_count: int
    created_at: datetime | None = None
    finished_at: datetime | None = None

    class Config:
        from_attributes = True


class CompetitorJobRef(BaseModel):
    """A job whose poll URL is nested under its competitor.

    Battlecard-update and discovery jobs are polled at
    `/competitors/{competitor_id}/.../{job_id}`, so an id alone is not enough
    to re-attach to one — the frontend cannot rebuild the URL. Briefing jobs
    are polled at a workspace-level path and so stay bare ids.
    """

    id: int
    competitor_id: int

    class Config:
        from_attributes = True


class ActiveJobsResponse(BaseModel):
    """Every non-terminal job in the workspace, so a page that has just
    loaded can re-attach a poller to work started before the reload.

    Without this the poll lifecycle lived only in React state and a refresh
    silently orphaned every running job — the UI would sit idle while a
    worker was still going, and the result would appear only on a later
    manual reload.
    """

    check_runs: list[CheckRunResponse] = []
    check_sweeps: list[CheckSweepResponse] = []
    briefing_job_ids: list[int] = []
    battlecard_update_jobs: list[CompetitorJobRef] = []
    competitor_discovery_jobs: list[CompetitorJobRef] = []
    site_summary_jobs: list[CompetitorJobRef] = []


class LatestCheckRunsResponse(BaseModel):
    """The dashboard's whole check-run payload in one response.

    `latest` is the current state of each surface. The three counts are over
    the workspace's *entire* run history, which `latest` deliberately does not
    cover — the crawl success rate has always been measured across every run
    ever recorded, and collapsing it to one run per surface would silently
    change a user-facing number.

    `finished_runs` is separate from `total_runs` because a run in progress has
    no outcome yet: the rate is successful/finished, while total only decides
    whether there is anything to report at all.
    """

    latest: list[CheckRunResponse]
    total_runs: int
    finished_runs: int
    successful_runs: int
