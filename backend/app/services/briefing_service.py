import logging
import re
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.briefing import (
    Briefing, BriefingAudience, BriefingDigestType, BriefingStatus, briefing_change_logs
)
from app.models.briefing_job import BriefingJob, BriefingJobStatus
from app.models.approval_item import ApprovalItem, ApprovalItemType, ApprovalStatus
from app.models.change_log import ChangeLog
from app.models.competitor import Competitor
from app.models.llm_usage import TokenUsageLog, LLMUsagePurpose
from app.services.budget_service import check_budget, BudgetExceededError
from app.services.llm.client import LLMClient
from app.services.llm.factory import get_llm_client
from app.services.llm.prompts import UNTRUSTED_CONTENT_PREAMBLE, wrap_untrusted

__all__ = ["generate_briefing", "run_briefing_job", "BriefingDraft", "NoMatchingChangeLogs"]

logger = logging.getLogger(__name__)

# job.error is rendered straight into the browser, so scrub any API key a
# provider error might echo back before it is persisted.
_SECRET_PATTERN = re.compile(r"nvapi-[A-Za-z0-9_\-]+")
_MAX_ERROR_CHARS = 500


def _safe_error(message: str) -> str:
    return _SECRET_PATTERN.sub("nvapi-***", message.strip())[:_MAX_ERROR_CHARS]


class NoMatchingChangeLogs(Exception):
    pass


class BriefingDraft(BaseModel):
    title: str
    body_markdown: str


_AUDIENCE_GUIDANCE = {
    "exec": "Keep it to 3-4 sentences of business impact — no jargon, no raw diffs.",
    "sales": "Focus on talk-track-ready facts a rep could use on a call today.",
    "product": "Focus on feature and positioning implications relevant to roadmap decisions.",
    "all": "Write a general-audience summary balancing business impact and specifics.",
}


def _system_prompt(audience: str) -> str:
    guidance = _AUDIENCE_GUIDANCE.get(audience, _AUDIENCE_GUIDANCE["all"])

    return (
        f"You are a competitive intelligence analyst drafting a briefing for "
        f"a '{audience}' audience. {guidance}\n\n"
        f"{UNTRUSTED_CONTENT_PREAMBLE}\n\n"
        "Every briefing you draft is reviewed by a human and must be "
        "explicitly approved before it is ever sent anywhere — write it as "
        "a draft awaiting that review, not as something already delivered.\n\n"
        'Respond with ONLY a JSON object: {"title": <short headline, under '
        '12 words>, "body_markdown": <briefing body in markdown, roughly '
        "100-250 words>}"
    )


def generate_briefing(
    db: Session,
    llm_client: LLMClient,
    workspace_id: int,
    audience: BriefingAudience,
    digest_type: BriefingDigestType,
    change_log_ids: list[int],
    generated_by_user_id: int | None = None,
) -> Briefing:
    rows = (
        db.query(ChangeLog, Competitor.name)
        .join(Competitor, ChangeLog.competitor_id == Competitor.id)
        .filter(
            ChangeLog.id.in_(change_log_ids),
            Competitor.workspace_id == workspace_id
        )
        .all()
    )
    if not rows:
        raise NoMatchingChangeLogs(
            "None of the given change_log_ids belong to this workspace"
        )

    lines = [
        f"- [{competitor_name}] {change_log.classification or 'change'} "
        f"(materiality {change_log.materiality_score}): "
        f"{change_log.rationale or (change_log.diff or '')[:300]}"
        for change_log, competitor_name in rows
    ]

    check_budget(db, workspace_id)

    result = llm_client.complete(
        system=_system_prompt(audience.value),
        user=wrap_untrusted("\n".join(lines)),
        response_model=BriefingDraft,
    )

    db.add(TokenUsageLog(
        workspace_id=workspace_id,
        purpose=LLMUsagePurpose.briefing,
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    ))

    briefing = Briefing(
        workspace_id=workspace_id,
        audience=audience,
        digest_type=digest_type,
        title=result.value.title,
        body_markdown=result.value.body_markdown,
        status=BriefingStatus.draft,
        generated_by_user_id=generated_by_user_id,
    )
    db.add(briefing)
    db.flush()

    for change_log, _ in rows:
        db.execute(
            briefing_change_logs.insert().values(
                briefing_id=briefing.id, change_log_id=change_log.id
            )
        )

    # Generation and queuing happen together here — there's no separate
    # "save as draft, submit later" step yet — but the two are still
    # separate status transitions (draft -> pending_approval) so that
    # step could be split out later without a schema change.
    briefing.status = BriefingStatus.pending_approval
    db.add(ApprovalItem(
        workspace_id=workspace_id,
        item_type=ApprovalItemType.briefing,
        item_id=briefing.id,
        status=ApprovalStatus.pending,
    ))

    db.commit()
    db.refresh(briefing)

    return briefing


def run_briefing_job(job_id: int) -> None:
    """Runs generate_briefing() for a queued BriefingJob and records the
    outcome on it. Called via FastAPI's BackgroundTasks (see
    routers/briefings.py), so it opens its own session rather than reusing
    the request-scoped one, matching the pattern scheduler.py already uses
    for out-of-request work.
    """
    db = SessionLocal()
    try:
        job = db.query(BriefingJob).filter(BriefingJob.id == job_id).first()
        if job is None:
            return

        job.status = BriefingJobStatus.running
        db.commit()

        try:
            llm_client = get_llm_client()
            if llm_client is None:
                raise RuntimeError("No LLM is configured for this deployment")

            briefing = generate_briefing(
                db, llm_client, job.workspace_id,
                job.audience, job.digest_type, job.change_log_ids,
                generated_by_user_id=job.created_by_user_id,
            )
            job.status = BriefingJobStatus.success
            job.briefing_id = briefing.id
        except (NoMatchingChangeLogs, BudgetExceededError, RuntimeError) as exc:
            db.rollback()
            job = db.query(BriefingJob).filter(BriefingJob.id == job_id).first()
            job.status = BriefingJobStatus.failed
            job.error = _safe_error(str(exc))
        except Exception as exc:  # noqa: BLE001 — any unexpected failure must still resolve the job, not hang it
            logger.exception("Briefing job %s failed unexpectedly", job_id)
            db.rollback()
            job = db.query(BriefingJob).filter(BriefingJob.id == job_id).first()
            job.status = BriefingJobStatus.failed
            # Record what actually broke (provider auth/quota/model errors,
            # unparseable model output) rather than a generic message — the
            # frontend surfaces job.error verbatim, and a briefing that
            # silently "isn't generated" is undebuggable without it.
            job.error = _safe_error(f"{type(exc).__name__}: {exc}")

        job.finished_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
