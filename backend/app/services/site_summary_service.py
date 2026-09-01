import logging
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.competitor_site_summary import CompetitorSiteSummary
from app.models.llm_usage import TokenUsageLog, LLMUsagePurpose
from app.models.snapshot import Snapshot
from app.models.surface import Surface
from app.services.budget_service import check_budget
from app.services.llm.client import LLMClient
from app.services.llm.prompts import SITE_SUMMARY_SYSTEM_PROMPT, site_summary_user_prompt
from app.services.rendered_content_service import capture_rendered_text, RenderedContentError

__all__ = ["generate_site_summary", "SiteSummaryDraft", "NoSnapshotAvailable"]

logger = logging.getLogger(__name__)


class NoSnapshotAvailable(Exception):
    pass


class SiteSummaryDraft(BaseModel):
    categories: list[str]
    current_offers: list[str]


def _latest_pages(db: Session, competitor_id: int) -> list[tuple[str, str]]:
    """One (label, text) pair per active surface. Always prefers a fresh
    JavaScript-rendered fetch (see rendered_content_service.py) over the
    stored Snapshot.text_content, since the stored snapshot comes from a
    plain HTTP fetch that misses anything a storefront renders client-side
    (hero banners, sale badges, category nav) — exactly the kind of content
    this feature needs, and the entire reason it exists (see the Bareeze
    bug this was built to fix: its real offers only ever showed up in the
    rendered fetch, never the plain-HTTP snapshot). Falls back to the
    stored snapshot only if rendering itself fails, so a flaky render never
    makes the whole thing unavailable — but a *successful but JS-empty*
    plain snapshot is never substituted for a working render, since that's
    what silently regressed this feature to "no categories found" before.

    This runs both from the manual "Analyze site" refresh and automatically
    after every check that finds new content (see
    check_service._apply_site_summary) — an extra browser launch once or
    twice a day per surface is not a real cost concern, and accuracy here
    matters more than shaving that cost.
    """

    # Materialized into plain tuples so the render loop below can run with
    # this session's pooled connection handed back. Every capture_rendered_text
    # call is a full browser launch with a 60s navigation timeout, and a
    # competitor can have dozens of active surfaces (discovery caps at 40), so
    # a connection held across the loop is the longest DB hold in the app.
    # expire_on_commit is on, so nothing below may touch a Surface instance —
    # hence reading the label and URL out here.
    surfaces = [
        (surface.id, f"{surface.surface_type.value} — {surface.url}", surface.url)
        for surface in (
            db.query(Surface)
            .filter(Surface.competitor_id == competitor_id, Surface.is_active.is_(True))
            .all()
        )
    ]

    db.commit()

    # Keyed by position so the snapshot fallbacks below can be batched after
    # the renders without reordering `pages` relative to the surface order the
    # prompt has always seen.
    pages_by_index: dict[int, tuple[str, str]] = {}
    fallbacks: list[tuple[int, int, str]] = []

    for index, (surface_id, label, url) in enumerate(surfaces):
        try:
            rendered_text = capture_rendered_text(url)
            if rendered_text:
                pages_by_index[index] = (label, rendered_text)
                continue
        except RenderedContentError as exc:
            logger.warning("Rendered fetch failed for surface %s, falling back to last snapshot: %s", surface_id, exc)

        fallbacks.append((index, surface_id, label))

    # First DB access since the release, and only for the surfaces that
    # actually need it — one checkout for the fallbacks instead of one held
    # across every render.
    for index, surface_id, label in fallbacks:
        snapshot = (
            db.query(Snapshot)
            .filter(Snapshot.surface_id == surface_id)
            .order_by(Snapshot.id.desc())
            .first()
        )
        if snapshot is not None and snapshot.text_content:
            pages_by_index[index] = (label, snapshot.text_content)

    return [pages_by_index[index] for index in sorted(pages_by_index)]


def generate_site_summary(
    db: Session, llm_client: LLMClient, workspace_id: int, competitor_id: int
) -> CompetitorSiteSummary:
    """Analyzes a competitor's *current* snapshot content — independent of
    the diff/materiality pipeline, so it's available even when zero changes
    have been detected yet. Upserts a singleton row per competitor (see
    CompetitorSiteSummary docstring) rather than accumulating a history.
    """

    pages = _latest_pages(db, competitor_id)
    if not pages:
        raise NoSnapshotAvailable(
            "This competitor has no captured snapshot yet — run a check first"
        )

    check_budget(db, workspace_id)

    # check_budget's queries leave a transaction open; the completion below is
    # a blocking network call, so hand the connection back before it. The
    # db.add() that follows emits no SQL (autoflush is off), so the connection
    # stays returned until the upsert query further down re-acquires it.
    db.commit()

    result = llm_client.complete(
        system=SITE_SUMMARY_SYSTEM_PROMPT,
        user=site_summary_user_prompt(pages),
        response_model=SiteSummaryDraft,
    )

    db.add(TokenUsageLog(
        workspace_id=workspace_id,
        purpose=LLMUsagePurpose.site_summary,
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    ))

    summary = (
        db.query(CompetitorSiteSummary)
        .filter(CompetitorSiteSummary.competitor_id == competitor_id)
        .first()
    )

    if summary is None:
        # competitor_id is unique here, and a "check all" sweep fans several
        # surfaces of the same competitor across workers at once — both can
        # find no row and both try to insert. The 15-minute debounce in
        # check_service does not help, because neither has written anything
        # yet when the other looks. The savepoint keeps the losing INSERT from
        # poisoning the session, so it can fall through to updating the row
        # the winner just created.
        try:
            with db.begin_nested():
                summary = CompetitorSiteSummary(competitor_id=competitor_id)
                db.add(summary)
                db.flush()
        except IntegrityError:
            summary = (
                db.query(CompetitorSiteSummary)
                .filter(CompetitorSiteSummary.competitor_id == competitor_id)
                .one()
            )

    # Written the same way whether the row was just created or already
    # existed — last writer wins, which is what a rolling "current state of
    # their site" summary wants.
    summary.categories = result.value.categories
    summary.current_offers = result.value.current_offers
    summary.generated_at = datetime.utcnow()

    db.commit()
    db.refresh(summary)
    return summary
