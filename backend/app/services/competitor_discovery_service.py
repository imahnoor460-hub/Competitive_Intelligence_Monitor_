import logging
from datetime import datetime

from app.database import SessionLocal
from app.models.competitor_discovery_job import (
    CompetitorDiscoveryJob,
    CompetitorDiscoveryJobStatus,
)
from app.models.surface import Surface
from app.scheduler import schedule_surface
from app.services.surface_discovery_service import discover_surfaces, SurfaceDiscoveryError

__all__ = ["run_competitor_discovery_job"]

logger = logging.getLogger(__name__)


def run_competitor_discovery_job(job_id: int) -> None:
    """Runs discover_surfaces() for a queued CompetitorDiscoveryJob and records
    the outcome on it. Called via FastAPI's BackgroundTasks (see
    routers/competitor.py), so it opens its own session rather than reusing the
    request-scoped one, matching the pattern briefing_service.py and
    battlecard_service.py already use for out-of-request work.
    """

    db = SessionLocal()
    try:
        job = (
            db.query(CompetitorDiscoveryJob)
            .filter(CompetitorDiscoveryJob.id == job_id)
            .first()
        )
        if job is None:
            return

        job.status = CompetitorDiscoveryJobStatus.running

        # Read into plain locals before the commit below. expire_on_commit is
        # on, so touching the job afterwards would re-SELECT and check a
        # connection straight back out of the pool — which is exactly what
        # must not happen while the browser is navigating.
        competitor_id = job.competitor_id
        website_url = job.website_url
        db.commit()

        surfaces_discovered = 0
        error: str | None = None

        try:
            # No connection is held here: the commit above ended the
            # transaction, and nothing below touches the session until the
            # inserts.
            discovered = discover_surfaces(website_url)

            # One INSERT batch and one COMMIT for every discovered page rather
            # than a commit-and-refresh per surface. Discovery caps at 40 pages
            # (_MAX_DISCOVERED) and the database is a pooled Postgres in
            # another region, so the per-surface version cost ~80-120
            # sequential round trips on a single add.
            surfaces = [
                Surface(
                    competitor_id=competitor_id,
                    surface_type=surface_type,
                    name=name,
                    url=url,
                )
                for surface_type, name, url in discovered
            ]
            db.add_all(surfaces)
            db.flush()
            new_ids = [surface.id for surface in surfaces]
            db.commit()

            # Re-read in one query instead of letting each committed instance
            # lazily reload its own expired columns, which would be one SELECT
            # per surface and would put back the round trips just removed.
            # Scheduling deliberately happens after the commit, so a failed
            # insert can't leave jobs armed for surfaces that don't exist.
            for surface in db.query(Surface).filter(Surface.id.in_(new_ids)).all():
                schedule_surface(surface)

            surfaces_discovered = len(new_ids)
            status = CompetitorDiscoveryJobStatus.success
        except SurfaceDiscoveryError as exc:
            logger.warning(
                "Surface discovery failed for competitor %s (%s): %s",
                competitor_id, website_url, exc,
            )
            db.rollback()
            status = CompetitorDiscoveryJobStatus.failed
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 — any unexpected failure must still resolve the job, not hang it
            logger.exception("Competitor discovery job %s failed unexpectedly", job_id)
            db.rollback()
            status = CompetitorDiscoveryJobStatus.failed
            # Record what actually broke rather than a generic message — the
            # frontend surfaces job.error verbatim, same as briefing jobs.
            error = f"{type(exc).__name__}: {exc}"

        job = (
            db.query(CompetitorDiscoveryJob)
            .filter(CompetitorDiscoveryJob.id == job_id)
            .first()
        )
        job.status = status
        job.surfaces_discovered = surfaces_discovered
        job.error = error[:2000] if error else None
        job.finished_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
