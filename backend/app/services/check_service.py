import hashlib
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.surface import Surface
from app.models.snapshot import Snapshot
from app.models.change_log import ChangeLog
from app.models.competitor import Competitor
from app.models.competitor_site_summary import CompetitorSiteSummary
from app.models.check_run import CheckRun, CheckRunStatus
from app.services.snapshot_service import capture_clean_snapshot, FetchError
from app.services.diff_engine import compute_diff, has_material_diff
from app.services.screenshot_service import capture_screenshot, ScreenshotError
from app.services.visual_diff import compare as compare_screenshots
from app.services.llm.factory import get_llm_client
from app.services.llm.scoring import score_and_classify
from app.services.llm.baseline_summary import summarize_baseline_snapshot
from app.services.synthesis import embed_change_log
from app.services.site_summary_service import generate_site_summary

__all__ = ["run_surface_check", "FetchError"]

logger = logging.getLogger(__name__)

# A crashed process (killed mid-check) can leave a CheckRun stuck at
# 'running' forever with nothing to ever mark it failed. Any run older than
# this is treated as stale and reclaimed rather than permanently blocking
# every future check of that surface.
_STALE_RUN_MINUTES = 15

# generate_site_summary() JS-renders every active surface of a competitor,
# not just the one just checked (see _apply_site_summary docstring for why
# it can't cheat and reuse the snapshot text). A competitor with several
# surfaces checked back-to-back — e.g. right after being added, or a burst
# of scheduled checks landing in the same window — would otherwise redo
# that full multi-surface render on every single one. Skipping it while a
# same-competitor summary is still this fresh keeps that burst to one
# render pass without materially staling the summary, since surfaces are
# normally checked hours apart (daily/weekly) anyway.
_SITE_SUMMARY_DEBOUNCE_MINUTES = 15


def run_surface_check(db: Session, surface: Surface) -> dict:
    """The single check pipeline for a Surface — called by both the manual
    'Check now' endpoint and the scheduler, so both paths stay identical.
    Wraps the check in a CheckRun row so a surface can't have two checks
    running concurrently (e.g. a slow manual check overlapping a scheduled
    one).
    """

    _reclaim_stale_running_checks(db, surface.id)

    already_running = (
        db.query(CheckRun)
        .filter(
            CheckRun.surface_id == surface.id,
            CheckRun.status == CheckRunStatus.running
        )
        .first()
    )
    if already_running:
        return {"status": "already_running"}

    check_run = CheckRun(surface_id=surface.id)
    db.add(check_run)
    db.commit()
    db.refresh(check_run)

    try:
        result = _perform_check(db, surface)
    except Exception as exc:
        # Catch broadly, not just FetchError — any unhandled exception here
        # (a bad DB value, a bug in scoring/embedding, etc.) must still mark
        # the CheckRun failed, or it's stuck at 'running' until the 15-minute
        # stale-reclaim kicks in. A failed commit also leaves the session in
        # a broken state, so it must be rolled back before writing to it again.
        db.rollback()
        check_run.status = CheckRunStatus.failed
        check_run.error = str(exc)[:2000]
        check_run.finished_at = datetime.utcnow()
        db.commit()
        raise

    check_run.status = CheckRunStatus.success
    check_run.finished_at = datetime.utcnow()
    db.commit()

    return result


def _reclaim_stale_running_checks(db: Session, surface_id: int) -> None:
    threshold = datetime.utcnow() - timedelta(minutes=_STALE_RUN_MINUTES)

    stale_runs = (
        db.query(CheckRun)
        .filter(
            CheckRun.surface_id == surface_id,
            CheckRun.status == CheckRunStatus.running,
            CheckRun.started_at < threshold
        )
        .all()
    )

    if not stale_runs:
        return

    for run in stale_runs:
        run.status = CheckRunStatus.failed
        run.error = f"Reclaimed after exceeding {_STALE_RUN_MINUTES}-minute stale-run threshold"
        run.finished_at = datetime.utcnow()

    db.commit()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _perform_check(db: Session, surface: Surface) -> dict:
    new_text = capture_clean_snapshot(surface.url)

    surface.last_checked_at = datetime.utcnow()

    screenshot_path = None
    if surface.capture_visual:
        try:
            screenshot_path = capture_screenshot(surface.url, surface.id)
        except ScreenshotError as exc:
            logger.warning("Screenshot capture failed for surface %s: %s", surface.id, exc)

    previous_snapshot = (
        db.query(Snapshot)
        .filter(Snapshot.surface_id == surface.id)
        .order_by(Snapshot.id.desc())
        .first()
    )

    if previous_snapshot is None or previous_snapshot.text_content is None:
        new_snapshot = Snapshot(
            surface_id=surface.id, text_content=new_text, screenshot_path=screenshot_path,
            content_hash=_hash_text(new_text)
        )
        db.add(new_snapshot)
        db.commit()
        db.refresh(new_snapshot)

        _apply_baseline_summary(db, surface, new_snapshot)
        _apply_site_summary(db, surface)

        return {"status": "baseline_captured"}

    if not has_material_diff(previous_snapshot.text_content, new_text):
        db.commit()

        return {"status": "no_change"}

    new_snapshot = Snapshot(
        surface_id=surface.id, text_content=new_text, screenshot_path=screenshot_path,
        content_hash=_hash_text(new_text)
    )
    db.add(new_snapshot)
    db.flush()

    diff_text = compute_diff(previous_snapshot.text_content, new_text)

    visual_diff_score = _compute_visual_diff_score(
        surface, previous_snapshot.screenshot_path, screenshot_path
    )

    change_log = ChangeLog(
        competitor_id=surface.competitor_id,
        surface_id=surface.id,
        old_snapshot_id=previous_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        diff=diff_text,
        visual_diff_score=visual_diff_score
    )

    _apply_materiality_scoring(db, surface, diff_text, change_log, visual_diff_score)

    db.add(change_log)
    db.commit()
    db.refresh(change_log)

    _apply_embedding(db, surface, change_log)
    _apply_site_summary(db, surface)

    return {"status": "change_detected", "change_log_id": change_log.id}


def _compute_visual_diff_score(
    surface: Surface, old_screenshot_path: str | None, new_screenshot_path: str | None
) -> float | None:
    if not old_screenshot_path or not new_screenshot_path:
        return None

    try:
        return compare_screenshots(old_screenshot_path, new_screenshot_path)
    except Exception as exc:  # noqa: BLE001 — a bad/missing image file shouldn't fail the check
        logger.warning("Visual diff failed for surface %s: %s", surface.id, exc)
        return None


def _apply_materiality_scoring(
    db: Session,
    surface: Surface,
    diff_text: str,
    change_log: ChangeLog,
    visual_diff_score: float | None = None,
) -> None:
    """Best-effort — a missing key or a flaky LLM call should never break
    the underlying watch/diff pipeline (graceful degradation), it just
    means this change_log is left unscored.
    """

    llm_client = get_llm_client()
    if llm_client is None:
        return

    competitor = db.query(Competitor).filter(Competitor.id == surface.competitor_id).first()
    surface_label = f"{competitor.name} — {surface.surface_type.value}" if competitor else surface.url

    prompt_text = diff_text
    if visual_diff_score is not None:
        prompt_text += (
            f"\n\n(Visual layout similarity check: {visual_diff_score:.2f} on a "
            "0=identical to 1=very different scale.)"
        )

    try:
        result = score_and_classify(
            db, llm_client, competitor.workspace_id if competitor else None, surface_label, prompt_text
        )
    except Exception as exc:  # noqa: BLE001 — deliberately broad, see docstring
        logger.warning("Materiality scoring failed for surface %s: %s", surface.id, exc)
        return

    change_log.materiality_score = result.score
    change_log.classification = result.classification
    change_log.rationale = result.rationale
    change_log.highlights = result.highlights or None
    change_log.headline = result.headline or None
    change_log.items = [item.model_dump() for item in result.items] or None


def _apply_baseline_summary(db: Session, surface: Surface, snapshot: Snapshot) -> None:
    """Best-effort, same graceful-degradation rule as scoring/site-summary.
    A surface's first check has no previous snapshot to diff against, so
    there's no rationale/highlights the way a real change gets — this is
    the closest equivalent: a short, readable read of what's actually on
    the page, instead of leaving the raw scraped text as the only option.
    """

    llm_client = get_llm_client()
    if llm_client is None:
        return

    if not snapshot.text_content:
        return

    competitor = db.query(Competitor).filter(Competitor.id == surface.competitor_id).first()
    surface_label = f"{competitor.name} — {surface.surface_type.value}" if competitor else surface.url

    # Reading the result is inside the try, not after it: a provider that
    # returns a malformed or wrong-shaped object fails on attribute access,
    # not inside summarize_baseline_snapshot, and that used to escape this
    # guard and turn a best-effort summary into a 500 on the whole check.
    # Both fields are read into locals first so a failure half-way can't
    # leave a partially-updated snapshot pending in the session; db.commit()
    # stays outside, where a genuine database failure still surfaces
    # instead of being swallowed as "summary unavailable".
    try:
        result = summarize_baseline_snapshot(
            db,
            llm_client,
            competitor.workspace_id if competitor else None,
            surface_label,
            snapshot.text_content,
        )

        headline = result.headline or None
        facts = [fact.model_dump() for fact in result.facts] or None
    except Exception as exc:  # noqa: BLE001 — deliberately broad, see docstring
        logger.warning(
            "Baseline summary failed for surface %s: %s", surface.id, exc, exc_info=True
        )
        return

    snapshot.headline = headline
    snapshot.facts = facts
    db.commit()


def _apply_embedding(db: Session, surface: Surface, change_log: ChangeLog) -> None:
    """Best-effort, same graceful-degradation rule as scoring. Only embeds
    once a classification exists — an unscored raw diff alone is a weak,
    noisy signal for cross-competitor similarity search.
    """

    if change_log.classification is None:
        return

    llm_client = get_llm_client()
    if llm_client is None:
        return

    competitor = db.query(Competitor).filter(Competitor.id == surface.competitor_id).first()
    if competitor is None:
        return

    try:
        embed_change_log(db, llm_client, competitor.workspace_id, change_log)
        db.commit()
    except Exception as exc:  # noqa: BLE001 — deliberately broad, see docstring
        logger.warning("Embedding failed for change_log %s: %s", change_log.id, exc)
        db.rollback()


def _apply_site_summary(db: Session, surface: Surface) -> None:
    """Best-effort, same graceful-degradation rule as scoring/embedding —
    runs automatically whenever a surface's content is new (baseline or a
    detected change), not on 'no_change', so "what's on their site" stays
    fresh without requiring a manual click, and isn't re-analyzed when
    nothing actually changed.

    generate_site_summary always uses a JS-rendered fetch, not the plain
    snapshot text just captured earlier in this same check — an earlier
    version of this function skipped that render here to save one browser
    launch per check, but that meant an automatic run could silently
    overwrite a good, accurate summary with an empty one derived from a
    JS-empty plain-HTTP snapshot (exactly the failure mode this whole
    feature exists to avoid). One extra browser launch per surface per
    check (at most a few times a day) is not a real cost concern.
    """

    llm_client = get_llm_client()
    if llm_client is None:
        return

    competitor = db.query(Competitor).filter(Competitor.id == surface.competitor_id).first()
    if competitor is None:
        return

    existing_summary = (
        db.query(CompetitorSiteSummary)
        .filter(CompetitorSiteSummary.competitor_id == competitor.id)
        .first()
    )
    debounce_threshold = datetime.utcnow() - timedelta(minutes=_SITE_SUMMARY_DEBOUNCE_MINUTES)
    if (
        existing_summary is not None
        and existing_summary.generated_at is not None
        and existing_summary.generated_at > debounce_threshold
    ):
        return

    try:
        generate_site_summary(db, llm_client, competitor.workspace_id, competitor.id)
    except Exception as exc:  # noqa: BLE001 — deliberately broad, see docstring
        logger.warning("Site summary generation failed for competitor %s: %s", competitor.id, exc)
        db.rollback()
