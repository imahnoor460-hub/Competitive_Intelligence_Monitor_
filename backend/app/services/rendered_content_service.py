import json
import logging
import re

from bs4 import BeautifulSoup
from playwright.sync_api import (
    sync_playwright,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from app.services.noise_filter import strip_noise

__all__ = ["capture_rendered_text", "find_category_listing_url", "RenderedContentError"]

_GOTO_TIMEOUT_MS = 60_000
_SETTLE_MS = 5_000

# Some storefronts hydrate their nav/category menu — and separately, their
# hero/promo banner tiles — from JSON data blobs embedded in a <script> tag,
# building the visible DOM only on user interaction (hover) or not as text
# at all (banner tiles are often rendered as an image with the JSON label
# used only for alt text/analytics). Either way, the label text never
# appears in rendered page text. Two distinct field-pair shapes have been
# seen in the wild: a "name" immediately followed by "handle" (category/menu
# nodes — see rendered_content_service tests), and a "label" immediately
# followed by "link" (promo/CTA banner tiles, e.g. "BAREEZE PRET SALE").
# Plain object keys or CSS elsewhere in the page won't have either pair
# side by side, so this stays reasonably well-scoped despite matching two
# key names. Quotes may be backslash-escaped (\") when the JSON is itself
# embedded as a string literal inside a larger hydration payload, hence the
# \* before each quote below.

_MENU_NODE_RE = re.compile(
    r'\\"(?:name|label)\\"\s*:\s*\\"((?:[^"\\]|\\.){1,60}?)\\"\s*,\s*\\"(?:handle|link)\\"'
)

_MAX_EMBEDDED_NAMES = 200


class RenderedContentError(Exception):
    pass


def _extract_embedded_category_names(html: str) -> list[str]:
    names = []
    seen = set()

    for match in _MENU_NODE_RE.finditer(html):
        raw = match.group(1)
        name = raw.replace('\\"', '"').replace("\\\\", "\\").strip()

        if name and name not in seen:
            seen.add(name)
            names.append(name)

            if len(names) >= _MAX_EMBEDDED_NAMES:
                break

    return names


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg", "img", "iframe"]):
        tag.decompose()

    lines = [line.strip() for line in soup.get_text(separator="\n").splitlines()]
    text = "\n".join(line for line in lines if line)

    embedded_names = _extract_embedded_category_names(html)

    if embedded_names:
        text += "\n\nSite navigation menu data (from page structure):\n" + "\n".join(
            embedded_names
        )

    return text


def capture_rendered_text(url: str) -> str:
    """Fetches a URL with a real browser and reads the page after client-side
    JavaScript has had a chance to run.
    """

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )

            try:
                page = browser.new_page()
                try:
                    resp = page.goto(url, wait_until="commit", timeout=_GOTO_TIMEOUT_MS)
                    logging.info("goto returned %s", resp.status if resp else None)
                except PlaywrightTimeoutError:
                    logging.error("timeout; page.url=%s title=%s",
                                  page.url, page.title())
                    raise
                page.wait_for_timeout(_SETTLE_MS)
                html = page.content()
            finally:
                browser.close()

    except PlaywrightError as exc:
        raise RenderedContentError(
            f"Failed to render {url}: {exc}"
        ) from exc

    return strip_noise(_extract_text(html))


def find_category_listing_url(url: str, category: str) -> str | None:
    """Looks for a link on `url` whose visible text matches `category`."""

    normalized = category.strip().lower()

    if not normalized:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )

            try:
                page = browser.new_page()

                try:
                    resp = page.goto(url, wait_until="commit", timeout=_GOTO_TIMEOUT_MS)
                    logging.info("goto returned %s", resp.status if resp else None)
                except PlaywrightTimeoutError:
                    logging.error("timeout; page.url=%s title=%s",
                                  page.url, page.title())
                    raise

                page.wait_for_timeout(_SETTLE_MS)

                # innerText respects CSS visibility, so links inside a
                # currently-closed mega-menu/dropdown can come back empty.
                # Falling back to textContent catches those.

                pairs = page.eval_on_selector_all(
                    "a",
                    "els => els.map(el => [(el.innerText || el.textContent || '').trim(), el.href])",
                )

            finally:
                browser.close()

    except PlaywrightError as exc:
        raise RenderedContentError(
            f"Failed to search {url} for a category link: {exc}"
        ) from exc

    exact_match = None
    partial_match = None

    for text, href in pairs:
        if not text or not href:
            continue

        lowered = text.strip().lower()

        if lowered == normalized and exact_match is None:
            exact_match = href

        elif partial_match is None and (
            normalized in lowered or lowered in normalized
        ):
            partial_match = href

    return exact_match or partial_match