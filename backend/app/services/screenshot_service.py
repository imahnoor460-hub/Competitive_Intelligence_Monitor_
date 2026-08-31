import logging
from datetime import datetime
from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from app.core.config import settings

__all__ = ["capture_screenshot", "ScreenshotError"]

_GOTO_TIMEOUT_MS = 60_000

# `wait_until="commit"` returns as soon as the response headers arrive, well
# before anything has painted, so unlike the other two call sites (which
# already settle before reading the DOM) this one needs its own settle —
# without it the PNG is a blank viewport.
_SETTLE_MS = 5_000


class ScreenshotError(Exception):
    pass


def capture_screenshot(url: str, surface_id: int) -> str:
    """Captures a full-page PNG screenshot and saves it under
    {snapshot_storage_path}/screenshots/{surface_id}/, returning the saved
    path so it can be stored on the Snapshot row.
    """

    directory = Path(settings.snapshot_storage_path) / "screenshots" / str(surface_id)
    directory.mkdir(parents=True, exist_ok=True)

    filename = f"{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}.png"
    path = directory / filename

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
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
                page.screenshot(path=str(path), full_page=True)
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise ScreenshotError(f"Failed to capture screenshot of {url}: {exc}") from exc

    return str(path)
