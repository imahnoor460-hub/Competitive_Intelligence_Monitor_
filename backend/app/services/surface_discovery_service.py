import logging
import re
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import (
    sync_playwright,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from app.models.surface import SurfaceType

__all__ = ["discover_surfaces", "normalize_url", "SurfaceDiscoveryError"]

# A heavy storefront theme (large mega-menu, dozens of third-party scripts —
# e.g. a big Shopify site) can easily take 20-30s just to reach
# domcontentloaded from this sandbox. A short timeout here doesn't fail
# gracefully to "found fewer pages" — it fails the whole discovery pass
# outright, which reads as "found nothing" to the caller.
_GOTO_TIMEOUT_MS = 60_000
_SETTLE_MS = 8_000

# Hard ceiling on how many pages a single discovery pass will turn into
# Surfaces — a single mega-menu can easily list 100+ links, and each one
# becomes a page checked on a recurring schedule, so this keeps a single
# "add competitor" or "discover more pages" click from silently creating an
# unbounded amount of monitoring work. Raised from an earlier, tighter cap
# once real-world testing (a large fashion e-commerce mega-menu) showed a
# tight cap filling up on near-duplicate category links before reaching
# distinct ones — see the name-based dedup below, which is what actually
# fixed that; this cap now only bounds the long tail.
_MAX_DISCOVERED = 40

# Links found inside these regions are the site's own table of contents —
# scanning only them (rather than every <a> on the page) keeps body-copy
# links (a blog post citing a partner, a footnote) out of the result.
_NAV_FOOTER_SELECTOR = "header a, nav a, footer a, [role='navigation'] a"

# Visible link text is matched against these per-type keyword lists so
# known page kinds (pricing/blog/changelog/jobs/product) still get their
# specific SurfaceType instead of falling back to `other`. Ordered by
# specificity — "product"/"blog" are broad enough that they're checked
# last so they don't shadow a more specific match found earlier.
_TYPE_KEYWORDS: dict[SurfaceType, list[str]] = {
    SurfaceType.pricing: ["pricing", "price", "plans", "plan"],
    SurfaceType.changelog: ["changelog", "release notes", "whats new", "what's new", "updates"],
    SurfaceType.jobs: ["careers", "career", "jobs", "we're hiring", "join us", "join our team"],
    SurfaceType.blog: ["blog", "news", "insights"],
    SurfaceType.product: ["product", "products", "features"],
}

_SKIPPED_SCHEMES = {"mailto", "tel", "javascript"}

# Anchor text past this length reads as a sentence, not a page name (e.g. a
# footer link whose visible text is a full tagline) — truncated rather than
# dropped so the card still has something to show.
_MAX_NAME_LENGTH = 60


class SurfaceDiscoveryError(Exception):
    pass


_VIEW_ALL_PREFIX = re.compile(r"(?i)^view all\s+")


def _clean_name(text: str) -> str | None:
    """Collapses a link's inner text into a short page name — "Unstitched",
    "New Arrivals", "Ready to Wear" — or None when the link had no usable
    text (an icon-only link, an empty anchor) so the caller can fall back
    to something else instead of storing a blank name. Strips a leading
    "View all " (a mega-menu's link to its own umbrella category, e.g.
    "View all SALE") since that's the same page as the "SALE" link
    elsewhere in the same menu — kept as one name so the two collapse
    together in the dedup below instead of appearing as separate pages.
    """
    collapsed = " ".join(text.split())
    collapsed = _VIEW_ALL_PREFIX.sub("", collapsed).strip()
    if not collapsed:
        return None
    if len(collapsed) > _MAX_NAME_LENGTH:
        collapsed = collapsed[:_MAX_NAME_LENGTH].rstrip() + "…"
    return collapsed


def _classify(text: str, path: str) -> SurfaceType:
    lowered = text.strip().lower()
    for surface_type, keywords in _TYPE_KEYWORDS.items():
        if lowered in keywords:
            return surface_type
    for surface_type, keywords in _TYPE_KEYWORDS.items():
        # A real nav link to e.g. a pricing page almost always has the
        # keyword in its URL path too; requiring both keeps a keyword
        # loosely mentioned in unrelated link text from being mistaken
        # for that page.
        if any(kw in lowered for kw in keywords) and any(
            kw.replace(" ", "-") in path or kw.replace(" ", "") in path for kw in keywords
        ):
            return surface_type
    return SurfaceType.other


def normalize_url(href: str) -> str | None:
    """Strips the fragment and trailing slash so `/blog` and `/blog#top`
    collapse to one entry; returns None for anything not worth tracking
    as its own page (mailto/tel links, bare anchors, empty hrefs).
    """
    parts = urlsplit(href)
    if not parts.scheme or parts.scheme in _SKIPPED_SCHEMES or not parts.netloc:
        return None
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def discover_surfaces(homepage_url: str) -> list[tuple[SurfaceType, str | None, str]]:
    """Loads `homepage_url` once and collects every internal link in its
    header/nav/footer — the site's own table of contents — so adding a
    competitor finds all of its pages (about, docs, customers, security,
    pricing, blog, ..., or for a storefront: Sale, New Arrivals,
    Unstitched, Ready to Wear, ...) instead of just the five keyword
    categories this used to special-case. Each link keeps its specific
    SurfaceType when its text/URL matches a known keyword (pricing, blog,
    ...) and falls back to `other` otherwise; its visible link text is kept
    as-is for `name` so the page shows up under the same name it has in
    the site's own nav rather than a generic type label. The homepage
    itself is always included first (named "Home") so a newly added
    competitor always has at least one page watched even when nothing else
    matches. Best-effort and capped at `_MAX_DISCOVERED` pages so a large
    site nav can't turn one click into dozens of scheduled checks.

    Returns (surface_type, name, url) triples — `name` is None when a link
    had no usable text, leaving the caller/frontend to fall back to
    something derived from the URL.
    """

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                try:
                    resp = page.goto(homepage_url, wait_until="commit", timeout=_GOTO_TIMEOUT_MS)
                    logging.info("goto returned %s", resp.status if resp else None)
                except PlaywrightTimeoutError:
                    logging.error("timeout; page.url=%s title=%s",
                                  page.url, page.title())
                    raise
                page.wait_for_timeout(_SETTLE_MS)
                pairs = page.eval_on_selector_all(
                    _NAV_FOOTER_SELECTOR,
                    "els => els.map(el => [(el.innerText || el.textContent || '').trim(), el.href])"
                )
                if not pairs:
                    # No semantic header/nav/footer markup — fall back to
                    # every link on the page rather than finding nothing.
                    pairs = page.eval_on_selector_all(
                        "a",
                        "els => els.map(el => [(el.innerText || el.textContent || '').trim(), el.href])"
                    )
                final_url = page.url
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise SurfaceDiscoveryError(f"Failed to discover pages on {homepage_url}: {exc}") from exc

    home_host = urlsplit(final_url).netloc
    home_normalized = normalize_url(final_url)

    seen_urls: set[str] = {home_normalized} if home_normalized else set()
    # A single mega-menu very often links the *same* category from several
    # panels under different URLs (Sana Safinaz's "READY TO WEAR" shows up
    # as /ready-to-wear, /ready-to-wear-1 and /ready-to-wear-2) — dedup by
    # name too, so those collapse into one entry instead of three near-
    # identical ones each eating a slot under `_MAX_DISCOVERED`.
    seen_names: set[str] = set()
    results: list[tuple[SurfaceType, str | None, str]] = [(SurfaceType.other, "Home", final_url)]

    for text, href in pairs:
        if len(results) >= _MAX_DISCOVERED:
            break
        if not href:
            continue
        normalized = normalize_url(href)
        if not normalized or normalized in seen_urls:
            continue
        if urlsplit(normalized).netloc != home_host:
            continue
        name = _clean_name(text or "")
        name_key = name.lower() if name else None
        if name_key is not None and name_key in seen_names:
            continue
        seen_urls.add(normalized)
        if name_key is not None:
            seen_names.add(name_key)
        surface_type = _classify(text or "", urlsplit(normalized).path.lower())
        results.append((surface_type, name, normalized))

    return results
