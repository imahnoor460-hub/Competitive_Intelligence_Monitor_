import enum
from datetime import datetime

from sqlalchemy import Column, Integer, Text, DateTime, Enum, ForeignKey
from app.base import Base


class SiteSummaryJobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"


class SiteSummaryJob(Base):
    """Tracks a manual "Analyze site" refresh so POST /site-summary/refresh
    can return immediately — see
    services/site_summary_service.py::run_site_summary_job().

    The refresh reads every active surface of the competitor, and discovery
    allows up to 40. Even after site_summary_service went HTTP-first, that is
    40 sequential fetches: measured at ~1.6s each against a real storefront,
    so roughly a minute locally and longer on a small shared-CPU container.
    Held open as a synchronous request that is long enough for an edge proxy
    to drop the connection, after which the work completes but the caller
    never learns the outcome.

    Deliberately not a singleton per competitor: two refreshes queued back to
    back are two rows, and the route's own in-flight check is what stops a
    double-click becoming duplicate work. The generated summary itself stays a
    singleton on CompetitorSiteSummary; this table records the *attempt*.
    """

    __tablename__ = "site_summary_jobs"

    id = Column(Integer, primary_key=True, index=True)

    workspace_id = Column(
        Integer,
        ForeignKey("workspaces.id"),
        nullable=False
    )

    competitor_id = Column(
        Integer,
        ForeignKey("competitors.id"),
        nullable=False,
        index=True
    )

    status = Column(
        Enum(SiteSummaryJobStatus),
        nullable=False,
        default=SiteSummaryJobStatus.queued
    )

    error = Column(Text, nullable=True)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
