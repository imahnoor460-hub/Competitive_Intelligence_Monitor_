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
from app.core.config import settings
from app.services.llm.prompts import SITE_SUMMARY_SYSTEM_PROMPT, site_summary_user_prompt
from app.services.rendered_content_service import capture_rendered_text, RenderedContentError
from app.services.snapshot_service import capture_clean_snapshot, FetchError

__all__ = ["generate_site_summary", "SiteSummaryDraft", "NoSnapshotAvailable"]

logger = logging.getLogger(__name__)


# Below this, a plain-HTTP fetch is treated as JavaScript-empty rather than
# as a short page, and the browser is consulted if it is available. Chosen
# from real measurements: a storefront homepage yields ~5,500 characters over
# plain HTTP and a category page ~40,000, while a genuinely JS-only page
# yields a few hundred characters of chrome and nothing else.
_MIN_HTTP_TEXT_CHARS = 600


class NoSnapshotAvailable(Exception):
    pass


class SiteSummaryDraft(BaseModel):
    categories: list[str]
    current_offers: list[str]


def _page_text(db: Session, surface_id: int, url: str) -> str | None:
    """Text for one surface, cheapest usable source first.

    Order is HTTP, then browser, then the stored snapshot. It used to be
    browser-first unconditionally, which was correct on accuracy and
    unaffordable in practice: one browser launch per surface across a
    competitor with 40 surfaces is minutes of work and hundreds of megabytes,
    and on a 512MB container it is an OOM rather than a slow path.

    The accuracy concern that motivated browser-first is preserved by the
    length test rather than by always rendering. The failure it guards
    against is a *successful but JavaScript-empty* plain fetch silently
    replacing a good summary with "no categories found" (the Bareeze bug).
    A JS-empty page returns a few hundred characters; a real one returns
    thousands, so `_MIN_HTTP_TEXT_CHARS` separates them and only the
    suspicious case pays for a render.

    Rendering is additionally gated on ENABLE_BROWSER_RENDERING, which is
    off by default — where Chromium cannot run, a short HTTP body is still
    better evidence than nothing, so it is used rather than discarded.
    """

    http_text: str | None = None
    try:
        http_text = capture_clean_snapshot(url)
    except FetchError as exc:
        logger.info("Plain fetch failed for surface %s: %s", surface_id, exc)

    if http_text and len(http_text) >= _MIN_HTTP_TEXT_CHARS:
        return http_text

    if settings.enable_browser_rendering:
        try:
            rendered_text = capture_rendered_text(url)
            if rendered_text:
                return rendered_text
        except RenderedContentError as exc:
            logger.warning(
                "Rendered fetch failed for surface %s, falling back: %s", surface_id, exc
            )

    # Short but real beats nothing at all.
    if http_text:
        return http_text

    snapshot = (
        db.query(Snapshot)
        .filter(Snapshot.surface_id == surface_id)
        .order_by(Snapshot.id.desc())
        .first()
    )
    if snapshot is not None and snapshot.text_content:
        return snapshot.text_content
    return None


def _latest_pages(
    db: Session,
    competitor_id: int,
    max_pages: int | None = None,
    priority_surface_id: int | None = None,
) -> list[tuple[str, str]]:
    """One (label, text) pair per active surface, cheapest source first (see
    `_page_text`).

    `max_pages` bounds the fan-out. The manual "Analyze site" refresh passes
    None and still considers every surface; the automatic post-check path
    passes a small cap, because that path fires per check and a competitor
    can have 40 surfaces — one check must not turn into 40 fetches.
    `priority_surface_id` puts the surface whose check triggered this first,
    so the page that actually changed is always inside the cap.
    """

    # Materialized into plain tuples so the fetch loop below can run with this
    # session's pooled connection handed back. expire_on_commit is on, so
    # nothing below may touch a Surface instance — hence reading the label and
    # URL out here.
    surfaces = [
        (surface.id, f"{surface.surface_type.value} — {surface.url}", surface.url)
        for surface in (
            db.query(Surface)
            .filter(Surface.competitor_id == competitor_id, Surface.is_active.is_(True))
            .all()
        )
    ]

    if priority_surface_id is not None:
        surfaces.sort(key=lambda row: row[0] != priority_surface_id)
    if max_pages is not None:
        surfaces = surfaces[:max_pages]

    db.commit()

    pages: list[tuple[str, str]] = []
    for surface_id, label, url in surfaces:
        text = _page_text(db, surface_id, url)
        if text:
            pages.append((label, text))

    return pages


def generate_site_summary(
    db: Session,
    llm_client: LLMClient,
    workspace_id: int,
    competitor_id: int,
    max_pages: int | None = None,
    priority_surface_id: int | None = None,
) -> CompetitorSiteSummary:
    """Analyzes a competitor's *current* snapshot content — independent of
    the diff/materiality pipeline, so it's available even when zero changes
    have been detected yet. Upserts a singleton row per competitor (see
    CompetitorSiteSummary docstring) rather than accumulating a history.

    `max_pages` and `priority_surface_id` bound and order the fan-out; both
    default to the unbounded behaviour the manual refresh has always had.
    See `_latest_pages`.
    """

    pages = _latest_pages(
        db, competitor_id, max_pages=max_pages, priority_surface_id=priority_surface_id
    )
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
