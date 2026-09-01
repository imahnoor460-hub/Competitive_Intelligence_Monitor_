import hashlib
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.surface import Surface
from app.models.snapshot import Snapshot
from app.models.change_log import ChangeLog
from app.models.competitor import Competitor
from app.models.competitor_site_summary import CompetitorSiteSummary
from app.models.check_run import CheckRun, CheckRunStatus
from app.models.check_sweep import CheckSweep, CheckSweepStatus
from app.services.snapshot_service import capture_clean_snapshot, FetchError
from app.services.diff_engine import compute_diff, has_material_diff
from app.services.screenshot_service import capture_screenshot, ScreenshotError
from app.services.visual_diff import compare as compare_screenshots
from app.services.llm.factory import get_llm_client
from app.services.llm.scoring import score_and_classify
from app.services.llm.baseline_summary import summarize_baseline_snapshot
from app.services.synthesis import embed_change_log
from app.services.site_summary_service import generate_site_summary

__all__ = [
    "run_surface_check",
    "enqueue_surface_check",
    "execute_surface_check",
    "FetchError",
]

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

# How many surfaces the *automatic* post-check site summary may read. The
# manual refresh is unbounded; this path fires on every check that finds new
# content, so it is bounded to keep one check from fanning out across all 40
# surfaces a competitor is allowed to have.
_SITE_SUMMARY_AUTO_MAX_PAGES = 8


def enqueue_surface_check(
    db: Session, surface: Surface, sweep_id: int | None = None
) -> tuple[CheckRun, bool]:
    """Claim the right to check this surface, without doing any of the work.

    Returns `(run, created)`. `created` is False when a check was already
    queued or running for this surface, in which case the existing run is
    returned and no second one is made — this is the database half of the
    duplicate-check guard, and it holds whether the caller goes on to run the
    check inline or hand it to a worker. The queue's deterministic job id is
    the other half; either alone is enough, and having both means a duplicate
    is stopped whichever layer sees it first.
    """

    _reclaim_stale_running_checks(db, surface.id)

    in_flight = (
        db.query(CheckRun)
        .filter(
            CheckRun.surface_id == surface.id,
            CheckRun.status.in_([CheckRunStatus.queued, CheckRunStatus.running]),
        )
        .first()
    )
    if in_flight:
        return in_flight, False

    check_run = CheckRun(
        surface_id=surface.id,
        status=CheckRunStatus.queued,
        sweep_id=sweep_id,
        enqueued_at=datetime.utcnow(),
    )
    db.add(check_run)
    db.commit()
    db.refresh(check_run)

    return check_run, True


def run_surface_check(db: Session, surface: Surface) -> dict:
    """The inline check path — claim a run and execute it in this session.

    Still what the manual endpoint and the scheduler use when no queue is
    configured, so a Redis-less deployment behaves exactly as it always has.
    With a queue configured the two halves are split across processes:
    `enqueue_surface_check` runs in the request, `execute_surface_check` runs
    in the worker. Both share `_run_claimed_check`, so there is one check
    pipeline, not two.
    """

    check_run, created = enqueue_surface_check(db, surface)
    if not created:
        return {"status": "already_running", "check_run_id": check_run.id}

    return _run_claimed_check(db, check_run, surface)


def _run_claimed_check(db: Session, check_run: CheckRun, surface: Surface) -> dict:
    """Execute a check whose CheckRun row is already claimed.

    Raises on failure after recording it, so the inline caller still turns a
    FetchError into a 502. The worker wrapper catches instead — there is
    nobody to return a status code to there, and the row already says failed.
    """

    sweep_id = check_run.sweep_id
    check_run_id = check_run.id
    check_run.status = CheckRunStatus.running
    check_run.started_at = datetime.utcnow()
    db.commit()

    try:
        result = _perform_check(db, surface)
    except Exception as exc:
        # Catch broadly, not just FetchError — any unhandled exception here
        # (a bad DB value, a bug in scoring/embedding, etc.) must still mark
        # the CheckRun failed, or it's stuck at 'running' until the 15-minute
        # stale-reclaim kicks in. A failed commit also leaves the session in
        # a broken state, so it must be rolled back before writing to it again.
        db.rollback()
        _finish_run(db, check_run_id, CheckRunStatus.failed, error=str(exc)[:2000])
        _record_sweep_outcome(db, sweep_id, failed=True)
        raise

    # Recorded on the row, not just returned, so a worker-executed check can
    # report the same outcome the inline path puts in its response body.
    _finish_run(
        db, check_run_id, CheckRunStatus.success, outcome=result.get("status")
    )
    _record_sweep_outcome(db, sweep_id, failed=False)

    # Every check response carries the run id, inline or queued, so a single
    # frontend code path can poll when the status is `queued` and skip
    # polling when it already has a terminal result.
    return {**result, "check_run_id": check_run_id}


def execute_surface_check(check_run_id: int) -> None:
    """Worker entry point for a queued CheckRun.

    Opens its own session rather than reusing a request-scoped one, matching
    the pattern briefing_service and competitor_discovery_service already use
    for out-of-request work.

    Idempotent by design: a run that is not still `queued` has already been
    picked up, so a redelivered message returns without re-running the
    pipeline — which matters because a re-run would re-bill NIM tokens.
    """

    db = SessionLocal()
    try:
        check_run = db.query(CheckRun).filter(CheckRun.id == check_run_id).first()
        if check_run is None:
            logger.warning("Check run %s no longer exists; nothing to do", check_run_id)
            return

        if check_run.status != CheckRunStatus.queued:
            logger.info(
                "Check run %s is %s, not queued — skipping duplicate delivery",
                check_run_id, check_run.status.value,
            )
            return

        surface = (
            db.query(Surface).filter(Surface.id == check_run.surface_id).first()
        )
        if surface is None:
            sweep_id = check_run.sweep_id
            _finish_run(
                db, check_run_id, CheckRunStatus.failed,
                error="Surface was deleted before the check ran",
            )
            _record_sweep_outcome(db, sweep_id, failed=True)
            return

        try:
            _run_claimed_check(db, check_run, surface)
        except Exception as exc:  # noqa: BLE001 — the row already records it
            logger.warning("Check run %s failed: %s", check_run_id, exc)
    finally:
        db.close()


def _finish_run(
    db: Session,
    check_run_id: int,
    status: CheckRunStatus,
    error: str | None = None,
    outcome: str | None = None,
) -> None:
    """Write a run's terminal state by id rather than through a held instance.

    The caller may have just rolled back, which expires every instance in the
    session; re-reading is both cheaper and safer than trusting one that may
    now be detached or stale.
    """

    run = db.query(CheckRun).filter(CheckRun.id == check_run_id).first()
    if run is None:
        return

    run.status = status
    run.error = error
    run.outcome = outcome
    run.finished_at = datetime.utcnow()
    db.commit()


def _record_sweep_outcome(db: Session, sweep_id: int | None, failed: bool) -> None:
    """Count one finished child against its sweep, and close the sweep when
    the last one lands.

    The increments are SQL-side (`finished = finished + 1`) rather than
    read-modify-write: sweeps fan out across concurrent workers in separate
    processes, and two of them reading the same value before either writes
    would silently lose a completion and leave the sweep stuck below total.
    """

    if sweep_id is None:
        return

    db.query(CheckSweep).filter(CheckSweep.id == sweep_id).update(
        {
            CheckSweep.finished: CheckSweep.finished + 1,
            CheckSweep.failed_count: CheckSweep.failed_count + (1 if failed else 0),
            CheckSweep.status: CheckSweepStatus.running,
        },
        synchronize_session=False,
    )
    db.commit()

    sweep = db.query(CheckSweep).filter(CheckSweep.id == sweep_id).first()
    if sweep is None or sweep.finished < sweep.total:
        return

    # Every check failing is the only case reported as a failed sweep; a
    # partial failure stays `success` with failed_count telling the real
    # story, so the frontend's terminal-status set stays success|failed.
    sweep.status = (
        CheckSweepStatus.failed
        if sweep.total > 0 and sweep.failed_count >= sweep.total
        else CheckSweepStatus.success
    )
    sweep.finished_at = datetime.utcnow()
    db.commit()


def _reclaim_stale_running_checks(db: Session, surface_id: int) -> None:
    threshold = datetime.utcnow() - timedelta(minutes=_STALE_RUN_MINUTES)

    # `queued` is reclaimed on the same threshold as `running`. A row whose
    # queue message was never delivered would otherwise block this surface
    # from ever being checked again, since the in-flight guard above counts
    # it as a check already under way.
    stale_runs = (
        db.query(CheckRun)
        .filter(
            CheckRun.surface_id == surface_id,
            CheckRun.status.in_([CheckRunStatus.queued, CheckRunStatus.running]),
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
    # Every value the fetch/render phase needs is read into a plain local
    # before the db.commit() below hands this session's pooled connection
    # back. expire_on_commit is on (the default), so touching any ORM
    # attribute after that commit would silently re-SELECT and check a
    # connection straight back out — precisely what this is avoiding.
    surface_id = surface.id
    surface_url = surface.url
    surface_competitor_id = surface.competitor_id
    capture_visual = surface.capture_visual

    # run_surface_check's db.refresh(check_run) left a read transaction open,
    # so a connection is checked out right now. The fetch and the screenshot
    # below are network- and browser-bound and can together run well past a
    # minute; a connection held idle-in-transaction across them is what
    # drains the pool once a handful of checks overlap, since the scheduler
    # and the request threadpool can put far more checks in flight at once
    # than QueuePool's 5 + 10 overflow can cover.
    db.commit()

    new_text = capture_clean_snapshot(surface_url)

    screenshot_path = None
    if capture_visual:
        try:
            screenshot_path = capture_screenshot(surface_url, surface_id)
        except ScreenshotError as exc:
            logger.warning("Screenshot capture failed for surface %s: %s", surface_id, exc)

    # First DB access since the release — this checks a connection back out.
    previous_snapshot = (
        db.query(Snapshot)
        .filter(Snapshot.surface_id == surface_id)
        .order_by(Snapshot.id.desc())
        .first()
    )

    # Set after the I/O rather than before it: `surface` is expired by the
    # release above, and this is the first point where the session holds a
    # connection again anyway. Nothing between here and its original position
    # read the value, and run_surface_check rolls the session back on failure
    # either way.
    surface.last_checked_at = datetime.utcnow()

    if previous_snapshot is None or previous_snapshot.text_content is None:
        new_snapshot = Snapshot(
            surface_id=surface_id, text_content=new_text, screenshot_path=screenshot_path,
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
        surface_id=surface_id, text_content=new_text, screenshot_path=screenshot_path,
        content_hash=_hash_text(new_text)
    )
    db.add(new_snapshot)
    db.flush()

    diff_text = compute_diff(previous_snapshot.text_content, new_text)

    visual_diff_score = _compute_visual_diff_score(
        surface, previous_snapshot.screenshot_path, screenshot_path
    )

    change_log = ChangeLog(
        competitor_id=surface_competitor_id,
        surface_id=surface_id,
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

    Capped at `_SITE_SUMMARY_AUTO_MAX_PAGES` surfaces, with the surface whose
    check triggered this placed first. Uncapped, one check fanned out across
    every active surface of the competitor — up to 40, each its own fetch —
    so a single detected change cost dozens of network round trips, and
    before site_summary_service went HTTP-first, dozens of browser launches.
    The manual "Analyze site" refresh still considers every surface; it is
    user-initiated and runs one at a time, whereas this fires on every check.

    The accuracy concern that once justified rendering every page is now
    handled inside site_summary_service._page_text, which falls back to a
    browser only when a plain fetch comes back short enough to look
    JavaScript-empty.
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
        generate_site_summary(
            db,
            llm_client,
            competitor.workspace_id,
            competitor.id,
            max_pages=_SITE_SUMMARY_AUTO_MAX_PAGES,
            priority_surface_id=surface.id,
        )
    except Exception as exc:  # noqa: BLE001 — deliberately broad, see docstring
        logger.warning("Site summary generation failed for competitor %s: %s", competitor.id, exc)
        db.rollback()
