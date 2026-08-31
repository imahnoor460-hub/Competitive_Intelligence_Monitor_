from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.llm_usage import TokenUsageLog, LLMUsagePurpose
from app.models.surface import Surface
from app.services.budget_service import check_budget
from app.services.llm.client import LLMClient
from app.services.llm.prompts import CATEGORY_PRICE_SYSTEM_PROMPT, category_price_user_prompt
from app.services.rendered_content_service import (
    capture_rendered_text,
    find_category_listing_url,
    RenderedContentError,
)

__all__ = ["get_category_price_stats", "CategoryPriceStats", "NoSurfaceAvailable"]


class NoSurfaceAvailable(Exception):
    pass


class CategoryPriceDraft(BaseModel):
    prices: list[float]
    currency: str | None = None


@dataclass
class CategoryPriceStats:
    category: str
    listing_url: str | None
    prices_found: int
    min_price: float | None
    max_price: float | None
    avg_price: float | None
    currency: str | None


def get_category_price_stats(
    db: Session, llm_client: LLMClient, workspace_id: int, competitor_id: int, category: str
) -> CategoryPriceStats:
    """Best-effort: finds a page on the competitor's site that looks like a
    listing for `category` (see find_category_listing_url) and asks the LLM
    to read off the prices shown there. This only ever reflects whatever
    products are visible on that one page load — not the competitor's full
    catalog — and returns prices_found=0 (not an error) whenever no matching
    listing page can be located at all, since that's a real, fairly common
    outcome rather than a failure.
    """

    surface = (
        db.query(Surface)
        .filter(Surface.competitor_id == competitor_id, Surface.is_active.is_(True))
        .order_by(Surface.id.asc())
        .first()
    )
    if surface is None:
        raise NoSurfaceAvailable(
            "This competitor has no captured page yet — run a check first"
        )

    # Read before releasing: expire_on_commit is on, so `surface` must not be
    # touched after the commit below.
    surface_url = surface.url

    # find_category_listing_url launches a browser with a 60s navigation
    # timeout. This runs on a request thread holding a get_db() session, so
    # without this the session's pooled connection would sit idle-in-
    # transaction for the whole render.
    db.commit()

    try:
        listing_url = find_category_listing_url(surface_url, category)
    except RenderedContentError:
        listing_url = None

    if listing_url is None:
        return CategoryPriceStats(
            category=category,
            listing_url=None,
            prices_found=0,
            min_price=None,
            max_price=None,
            avg_price=None,
            currency=None,
        )

    check_budget(db, workspace_id)

    # check_budget checked a connection back out; the second render and the
    # completion that follows it are both blocking network work, so release
    # again before them.
    db.commit()

    try:
        page_text = capture_rendered_text(listing_url)
    except RenderedContentError:
        return CategoryPriceStats(
            category=category,
            listing_url=listing_url,
            prices_found=0,
            min_price=None,
            max_price=None,
            avg_price=None,
            currency=None,
        )

    result = llm_client.complete(
        system=CATEGORY_PRICE_SYSTEM_PROMPT,
        user=category_price_user_prompt(category, page_text),
        response_model=CategoryPriceDraft,
    )

    db.add(TokenUsageLog(
        workspace_id=workspace_id,
        purpose=LLMUsagePurpose.category_price,
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    ))
    db.commit()

    prices = result.value.prices
    if not prices:
        return CategoryPriceStats(
            category=category,
            listing_url=listing_url,
            prices_found=0,
            min_price=None,
            max_price=None,
            avg_price=None,
            currency=result.value.currency,
        )

    return CategoryPriceStats(
        category=category,
        listing_url=listing_url,
        prices_found=len(prices),
        min_price=min(prices),
        max_price=max(prices),
        avg_price=sum(prices) / len(prices),
        currency=result.value.currency,
    )
