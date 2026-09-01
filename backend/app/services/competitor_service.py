from sqlalchemy.orm import Session

from app.models.approval_item import ApprovalItem, ApprovalItemType
from app.models.battlecard import Battlecard
from app.models.battlecard_update import BattlecardUpdate
from app.models.battlecard_update_job import BattlecardUpdateJob
from app.models.briefing import briefing_change_logs
from app.models.change_embedding import ChangeEmbedding
from app.models.change_log import ChangeLog
from app.models.check_run import CheckRun
from app.models.company_profile import CompanyProfile
from app.models.competitor import Competitor
from app.models.competitor_discovery_job import CompetitorDiscoveryJob
from app.models.competitor_site_summary import CompetitorSiteSummary
from app.models.response_library import ResponseLibraryItem
from app.models.snapshot import Snapshot
from app.models.surface import Surface
from app.models.traffic_snapshot import TrafficSnapshot
from app.scheduler import unschedule_surface

__all__ = ["delete_competitor"]


def delete_competitor(db: Session, workspace_id: int, competitor_id: int) -> bool:
    """Deletes a competitor and everything that accumulates under it,
    child-before-parent, mirroring own_site_service.delete_own_site (see
    that docstring for why Postgres needs strict per-level flushing here
    rather than one batched delete — SQLite's laxer FK enforcement lets
    the wrong order slip through the test suite unnoticed).

    Unlike the own-site deletion, a regular tracked competitor can
    genuinely have a CompanyProfile, Battlecard, CompetitorSiteSummary, and
    TrafficSnapshot history, so those are cleaned up here too. Response
    Library items reference competitors only loosely (nullable FK, and
    manually authored by a team member rather than derived from checks) —
    their competitor_id is cleared rather than deleting the item, so a
    team's talk tracks don't disappear just because the competitor they
    were written about stopped being tracked.
    """

    competitor = (
        db.query(Competitor)
        .filter(Competitor.id == competitor_id, Competitor.workspace_id == workspace_id)
        .first()
    )
    if competitor is None:
        return False

    surface_ids = [
        row[0]
        for row in db.query(Surface.id).filter(Surface.competitor_id == competitor.id).all()
    ]

    if surface_ids:
        change_log_ids = [
            row[0]
            for row in db.query(ChangeLog.id).filter(ChangeLog.surface_id.in_(surface_ids)).all()
        ]

        if change_log_ids:
            db.query(ChangeEmbedding).filter(
                ChangeEmbedding.change_log_id.in_(change_log_ids)
            ).delete(synchronize_session=False)
            db.execute(
                briefing_change_logs.delete().where(
                    briefing_change_logs.c.change_log_id.in_(change_log_ids)
                )
            )
            db.flush()

            db.query(ChangeLog).filter(ChangeLog.id.in_(change_log_ids)).delete(
                synchronize_session=False
            )
            db.flush()

        db.query(CheckRun).filter(CheckRun.surface_id.in_(surface_ids)).delete(
            synchronize_session=False
        )
        db.query(Snapshot).filter(Snapshot.surface_id.in_(surface_ids)).delete(
            synchronize_session=False
        )
        db.flush()

    surfaces = db.query(Surface).filter(Surface.competitor_id == competitor.id).all()
    for surface in surfaces:
        unschedule_surface(surface.id)
        db.delete(surface)
    db.flush()

    # Before the battlecard block below, because a BattlecardUpdateJob has an
    # FK to *both* competitors.id and battlecard_updates.id — leaving these
    # rows in place blocks the BattlecardUpdate delete further down as well as
    # the competitor delete at the end. Every job for this competitor targets
    # this competitor's battlecard, so clearing them all here settles both
    # references at once.
    db.query(BattlecardUpdateJob).filter(
        BattlecardUpdateJob.competitor_id == competitor.id
    ).delete(synchronize_session=False)
    db.flush()

    battlecard = db.query(Battlecard).filter(Battlecard.competitor_id == competitor.id).first()
    if battlecard is not None:
        battlecard_update_ids = [
            row[0]
            for row in db.query(BattlecardUpdate.id)
            .filter(BattlecardUpdate.battlecard_id == battlecard.id)
            .all()
        ]
        if battlecard_update_ids:
            db.query(ApprovalItem).filter(
                ApprovalItem.item_type == ApprovalItemType.battlecard_update,
                ApprovalItem.item_id.in_(battlecard_update_ids),
            ).delete(synchronize_session=False)
            db.query(BattlecardUpdate).filter(
                BattlecardUpdate.id.in_(battlecard_update_ids)
            ).delete(synchronize_session=False)
            db.flush()
        db.delete(battlecard)

    # Every competitor added with a website_url gets one of these at creation
    # (see routers/competitor.py::create_competitor), so this is the one
    # dependent row essentially guaranteed to exist — and the FK that failed
    # every delete before it was cleaned up here.
    db.query(CompetitorDiscoveryJob).filter(
        CompetitorDiscoveryJob.competitor_id == competitor.id
    ).delete(synchronize_session=False)
    db.query(CompanyProfile).filter(CompanyProfile.competitor_id == competitor.id).delete(
        synchronize_session=False
    )
    db.query(CompetitorSiteSummary).filter(
        CompetitorSiteSummary.competitor_id == competitor.id
    ).delete(synchronize_session=False)
    db.query(TrafficSnapshot).filter(TrafficSnapshot.competitor_id == competitor.id).delete(
        synchronize_session=False
    )
    db.query(ResponseLibraryItem).filter(
        ResponseLibraryItem.competitor_id == competitor.id
    ).update({"competitor_id": None}, synchronize_session=False)
    db.flush()

    db.delete(competitor)
    db.commit()
    return True
