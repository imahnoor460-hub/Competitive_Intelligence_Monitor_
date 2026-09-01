import enum
from datetime import datetime

from sqlalchemy import Column, Index, Integer, String, Text, DateTime, Enum, ForeignKey
from app.base import Base


class CheckRunStatus(str, enum.Enum):
    # `queued` means the row exists and a job has been handed to the queue,
    # but no worker has picked it up yet. It is deliberately a real persisted
    # state rather than an implicit gap: it is what makes a check visible and
    # pollable the instant the endpoint returns, and what lets the reconciler
    # tell "never delivered" apart from "running for a long time".
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"


class CheckRun(Base):
    __tablename__ = "check_runs"

    # Every read of this table filters or partitions by surface_id and then
    # orders by started_at — see routers/check_runs.py and
    # check_service._reclaim_stale_running_checks.
    __table_args__ = (
        Index("ix_check_runs_surface_id_started_at", "surface_id", "started_at"),
    )

    id = Column(Integer, primary_key=True, index=True)

    surface_id = Column(
        Integer,
        ForeignKey("surfaces.id"),
        nullable=False
    )

    status = Column(
        Enum(CheckRunStatus),
        nullable=False,
        default=CheckRunStatus.running
    )

    # Set when this run is part of a "check all" sweep, so the sweep can
    # report progress without the frontend polling every run individually.
    # Null for a single-surface check, which has no parent.
    sweep_id = Column(
        Integer,
        ForeignKey("check_sweeps.id"),
        nullable=True,
        index=True
    )

    # When the job was handed to the queue. Distinct from started_at, which
    # is when a worker actually began: the gap between them is queue latency,
    # and a row with an enqueued_at but no progress is what the reconciler
    # looks for.
    enqueued_at = Column(DateTime, nullable=True)

    # What the check actually concluded: baseline_captured | no_change |
    # change_detected. `status` only says whether the run finished, which is
    # all a worker-executed check could otherwise report — the descriptive
    # outcome used to exist solely in the inline endpoint's response body and
    # was lost the moment the work moved to a worker.
    outcome = Column(String, nullable=True)

    error = Column(Text, nullable=True)

    started_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    finished_at = Column(DateTime, nullable=True)
