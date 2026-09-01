import math
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.change_log import ChangeLog
from app.models.change_embedding import ChangeEmbedding
from app.models.competitor import Competitor
from app.models.surface import Surface
from app.models.llm_usage import TokenUsageLog, LLMUsagePurpose
from app.services.budget_service import check_budget
from app.services.llm.client import LLMClient
from app.services.llm.embeddings import embed_and_log
from app.services.llm.prompts import UNTRUSTED_CONTENT_PREAMBLE, wrap_untrusted


def _embedding_text(change_log: ChangeLog) -> str:
    label = change_log.classification or "change"
    body = change_log.rationale or (change_log.diff or "")[:1000]
    return f"{label}: {body}"


def embed_change_log(
    db: Session, llm_client: LLMClient, workspace_id: int, change_log: ChangeLog
) -> ChangeEmbedding:
    vectors, model = embed_and_log(db, llm_client, workspace_id, [_embedding_text(change_log)])

    existing = (
        db.query(ChangeEmbedding)
        .filter(ChangeEmbedding.change_log_id == change_log.id)
        .first()
    )

    if existing:
        existing.vector = vectors[0]
        existing.model = model
        return existing

    embedding = ChangeEmbedding(
        change_log_id=change_log.id,
        workspace_id=workspace_id,
        vector=vectors[0],
        model=model,
    )
    db.add(embedding)
    return embedding


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


@dataclass
class SimilarChange:
    change_log: ChangeLog
    similarity: float


def find_similar_changes(
    db: Session, workspace_id: int, change_log_id: int, top_k: int = 5
) -> list[SimilarChange]:
    target = (
        db.query(ChangeEmbedding)
        .filter(
            ChangeEmbedding.change_log_id == change_log_id,
            ChangeEmbedding.workspace_id == workspace_id
        )
        .first()
    )
    if target is None:
        return []

    # Only compare against rows embedded by the same model. Embedding spaces
    # aren't comparable across models even at equal dimensionality, and when
    # the dimensions differ (nv-embedqa-e5-v5 was 1024, nemotron-3-embed-1b
    # is 2048) _cosine_similarity's zip() truncates to the shorter vector
    # instead of raising — so mixing models yields a plausible-looking but
    # meaningless score. Rows from a retired model stay in the table and
    # simply stop matching until they're re-embedded.
    candidates = (
        db.query(ChangeEmbedding)
        .filter(
            ChangeEmbedding.workspace_id == workspace_id,
            ChangeEmbedding.change_log_id != change_log_id,
            ChangeEmbedding.model == target.model
        )
        .all()
    )
    if not candidates:
        return []

    scored = sorted(
        ((c, _cosine_similarity(target.vector, c.vector)) for c in candidates),
        key=lambda pair: pair[1],
        reverse=True,
    )[:top_k]

    change_log_ids = [c.change_log_id for c, _ in scored]
    change_logs_by_id = {
        cl.id: cl
        for cl in db.query(ChangeLog).filter(ChangeLog.id.in_(change_log_ids)).all()
    }

    return [
        SimilarChange(change_log=change_logs_by_id[c.change_log_id], similarity=score)
        for c, score in scored
        if c.change_log_id in change_logs_by_id
    ]


class SynthesisResult(BaseModel):
    summary: str


SYNTHESIS_SYSTEM_PROMPT = (
    "You are a competitive intelligence analyst. You are given a list of "
    "recent, already-scored changes detected across a team's tracked "
    "competitors. Look for patterns connecting two or more of them — e.g. "
    "several competitors moving in the same direction in the same period "
    "— and write a short narrative summary.\n\n"
    f"{UNTRUSTED_CONTENT_PREAMBLE}\n\n"
    "If there is no clear cross-competitor pattern, say so plainly rather "
    "than inventing one.\n\n"
    'Respond with ONLY a JSON object: {"summary": <2-4 sentence narrative>}'
)


@dataclass
class SynthesisOutcome:
    summary: str
    based_on: int


def generate_cross_competitor_summary(
    db: Session, llm_client: LLMClient, workspace_id: int, since: datetime
) -> SynthesisOutcome | None:
    rows = (
        db.query(ChangeLog, Competitor.name)
        .join(Surface, ChangeLog.surface_id == Surface.id)
        .join(Competitor, Surface.competitor_id == Competitor.id)
        .filter(
            Competitor.workspace_id == workspace_id,
            ChangeLog.created_at >= since,
            ChangeLog.materiality_score.isnot(None),
        )
        .order_by(ChangeLog.materiality_score.desc())
        .limit(30)
        .all()
    )

    if not rows:
        return None

    lines = [
        f"- [{competitor_name}] {change_log.classification}: "
        f"{change_log.rationale or (change_log.diff or '')[:200]}"
        for change_log, competitor_name in rows
    ]

    check_budget(db, workspace_id)

    result = llm_client.complete(
        system=SYNTHESIS_SYSTEM_PROMPT,
        user=wrap_untrusted("\n".join(lines)),
        response_model=SynthesisResult,
    )

    db.add(TokenUsageLog(
        workspace_id=workspace_id,
        purpose=LLMUsagePurpose.briefing,
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    ))

    return SynthesisOutcome(summary=result.value.summary, based_on=len(rows))
