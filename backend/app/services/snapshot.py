import logging
import time

import requests
from bs4 import BeautifulSoup

from app.core.config import settings

logger = logging.getLogger(__name__)

USER_AGENT = "CompetitiveIntelligenceMonitor/1.0"

# Read in pieces rather than in one .content read, so the wall-clock deadline
# below is checked while the body is still arriving instead of after it.
_CHUNK_BYTES = 64 * 1024


class FetchError(Exception):
    pass


def fetch_html(url: str) -> str:
    """Fetch one page, with a ceiling on how long it can possibly take.

    `requests`' `timeout` is per socket operation, not per request: a server
    that sends one byte just inside every read window keeps a connection open
    indefinitely without ever tripping it. That is the shape of hang that left
    a check sitting at `running` until the 15-minute stale reclaim, and with
    checks running two at a time on the worker it stalls everything behind it.

    So three bounds, not one:

    * connect/read timeouts catch a dead host and a stalled response;
    * `http_total_timeout` is the wall-clock ceiling, checked between chunks,
      which is what makes a slow drip terminate;
    * `http_max_bytes` stops a body far larger than any real page from being
      read into a 512MB container.

    All three raise `FetchError`, which the check pipeline already records as
    a failed CheckRun — so a page like this is skipped and the rest of the
    sweep carries on, rather than blocking it.
    """

    deadline = time.monotonic() + settings.http_total_timeout
    chunks: list[bytes] = []
    total = 0

    try:
        with requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=(settings.http_connect_timeout, settings.http_read_timeout),
            stream=True,
        ) as response:
            response.raise_for_status()

            for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
                if time.monotonic() > deadline:
                    raise FetchError(
                        f"Timed out fetching {url}: still receiving data after "
                        f"{settings.http_total_timeout:.0f}s"
                    )

                total += len(chunk)
                if total > settings.http_max_bytes:
                    raise FetchError(
                        f"Refused {url}: response exceeded the "
                        f"{settings.http_max_bytes} byte limit"
                    )

                chunks.append(chunk)

            # Header charset only. requests' apparent_encoding sniffs
            # .content, which a streamed-and-consumed response can no longer
            # hand back — and chardet on a 3MB body is not free either.
            encoding = response.encoding or "utf-8"
    except requests.RequestException as exc:
        raise FetchError(f"Failed to fetch {url}: {exc}") from exc

    # errors="replace" rather than a raise: a page with a couple of bad bytes
    # under a mislabelled charset is still perfectly diffable text, and this
    # is the only place that would turn it into a failed check.
    return b"".join(chunks).decode(encoding, errors="replace")


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg", "img", "iframe"]):
        tag.decompose()

    lines = [
        line.strip()
        for line in soup.get_text(separator="\n").splitlines()
    ]

    return "\n".join(line for line in lines if line)
