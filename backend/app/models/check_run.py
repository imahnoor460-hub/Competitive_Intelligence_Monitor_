import enum
from datetime import datetime

from sqlalchemy import Column, Index, Integer, Text, DateTime, Enum, ForeignKey
from app.base import Base


class CheckRunStatus(str, enum.Enum):
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

    error = Column(Text, nullable=True)

    started_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    finished_at = Column(DateTime, nullable=True)
