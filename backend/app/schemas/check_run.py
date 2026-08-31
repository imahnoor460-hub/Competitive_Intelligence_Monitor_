from datetime import datetime

from pydantic import BaseModel

from app.models.check_run import CheckRunStatus


class CheckRunResponse(BaseModel):
    id: int
    surface_id: int
    status: CheckRunStatus
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None

    class Config:
        from_attributes = True


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
