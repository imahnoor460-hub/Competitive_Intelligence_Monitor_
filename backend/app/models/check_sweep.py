import enum
from datetime import datetime

from sqlalchemy import Column, Integer, DateTime, Enum, ForeignKey
from app.base import Base


class CheckSweepStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"


class CheckSweep(Base):
    """One "check every surface in this workspace" request.

    Exists so a sweep is a server-side record rather than a loop in someone's
    browser. Before this, "check all" was a sequential `await` per surface in
    Header.tsx: nothing on the server knew a sweep was happening, and a
    refresh or a closed tab abandoned it halfway with no way to see what had
    been missed.

    Progress is counted rather than derived by scanning child rows, because
    the workers finishing those rows run concurrently and in a different
    process — `finished` and `failed_count` are incremented with atomic SQL
    UPDATEs (see check_service._record_sweep_outcome), never read-modify-write.

    Terminal status stays limited to success/failed so the frontend's existing
    `isTerminalStatus` (success | failed) needs no change. A sweep where some
    surfaces failed is still `success`; `failed_count` is what tells the user
    "28 of 30 checked", and a sweep is only `failed` when every check failed.
    """

    __tablename__ = "check_sweeps"

    id = Column(Integer, primary_key=True, index=True)

    workspace_id = Column(
        Integer,
        ForeignKey("workspaces.id"),
        nullable=False,
        index=True
    )

    status = Column(
        Enum(CheckSweepStatus),
        nullable=False,
        default=CheckSweepStatus.queued
    )

    # Set once at creation from the number of surfaces actually enqueued, so
    # progress is measured against what was really dispatched rather than
    # against how many surfaces exist now — surfaces can be added or deleted
    # while a sweep is in flight.
    total = Column(Integer, nullable=False, default=0)

    finished = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
