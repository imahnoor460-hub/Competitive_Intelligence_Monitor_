import enum
from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, ForeignKey
from app.base import Base


class CompetitorDiscoveryJobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"


class CompetitorDiscoveryJob(Base):
    """Tracks a background surface-discovery pass for a newly added competitor
    so POST /competitors can return immediately and the frontend can poll for
    completion — see
    services/competitor_discovery_service.py::run_competitor_discovery_job().

    Discovery drives a real browser (surface_discovery_service launches
    Chromium, allows a 60s navigation timeout and then waits out an
    unconditional 8s settle), so running it inline held the HTTP request open
    for the better part of a minute on every add, before the up-to-40 surface
    inserts had even started.
    """

    __tablename__ = "competitor_discovery_jobs"

    id = Column(Integer, primary_key=True, index=True)

    workspace_id = Column(
        Integer,
        ForeignKey("workspaces.id"),
        nullable=False
    )

    competitor_id = Column(
        Integer,
        ForeignKey("competitors.id"),
        nullable=False
    )

    # Kept on the job rather than re-read off the competitor: website_url is
    # request input that the Competitor row never stores.
    website_url = Column(String, nullable=False)

    status = Column(
        Enum(CompetitorDiscoveryJobStatus),
        nullable=False,
        default=CompetitorDiscoveryJobStatus.queued
    )

    surfaces_discovered = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
