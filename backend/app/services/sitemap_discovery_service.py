"""Sitemap-first page discovery over plain HTTP.

This exists because the browser-based discovery in surface_discovery_service
cannot run on the deployment's memory budget. Measured against a real target
(a Shopify storefront), one Chromium pass peaked at ~596MB resident and took
22s; fetching robots.txt and walking that same site's sitemaps returned the
same category pages in ~3s using ~45MB. On a 512MB container that is the
difference between working and being OOM-killed.

It is also simply better discovery for most sites: a sitemap is the site's own
declaration of its pages, whereas scraping the rendered nav finds only what a
mega-menu happens to link and depends on JavaScript having painted in time.

Scope: this module does HTTP and XML only. It returns raw URL strings in
priority order and knows nothing about SurfaceType, naming or deduplication —
surface_discovery_service owns that, for both this path and the browser one.
"""

import gzip
import logging
import re
from urllib.parse import urljoin, urlsplit

import requests
from lxml import etree

__all__ = ["collect_sitemap_urls", "SitemapUnavailable"]

logger = logging.getLogger(__name__)

USER_AGENT = "CompetitiveIntelligenceMonitor/1.0"

_ROBOTS_TIMEOUT = 10
_SITEMAP_TIMEOUT = 15

# Budgets, all three of which exist for the same reason: this runs in a
# 512MB container and a sitemap is attacker-adjacent input (an arbitrary
# third-party URL the user asked us to look at). A retailer's product sitemap
# can be tens of megabytes, and a sitemap index can point at hundreds more.
_MAX_SITEMAP_BYTES = 5_000_000
_MAX_SITEMAPS_FETCHED = 12
_MAX_URLS = 500

# Deliberately strict: this parses XML fetched from an arbitrary remote host,
# so entity resolution and network access during parse are both off. Without
# resolve_entities=False a malicious sitemap can read local files through an
# XXE payload; without no_network it can make the parser fetch a DTD.
# recover=False so a malformed document raises rather than yielding a
# half-parsed tree we would then treat as authoritative.
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)

_SITEMAP_DIRECTIVE = re.compile(r"(?im)^\s*sitemap:\s*(\S+)\s*$")

# Order in which nested sitemaps are walked. A storefront's index typically
# lists six product sitemaps holding thousands of individual product URLs
# alongside one collections sitemap holding the category pages a competitor
# analyst actually wants. Walking in file order would fill the caller's entire
# page budget with individual products, so the useful kinds are visited first
# and products are visited last.
# Static pages and collections share tier 0 deliberately. They are equally
# useful to an analyst ("About us"/"Contact" and "Sale"/"Ready to wear"), and
# a storefront's pages sitemap alone holds more URLs than the whole page
# budget — so ranking pages above collections means no category page ever
# appears. Sharing a tier makes them round-robin against each other instead.
_SITEMAP_PRIORITY: list[tuple[tuple[str, ...], int]] = [
    (("page", "collection", "categor"), 0),
    (("blog", "news", "article", "post"), 1),
    (("product", "item"), 3),
]
_DEFAULT_PRIORITY = 2


class SitemapUnavailable(Exception):
    """No usable sitemap could be found or parsed for this site."""


def _localname(tag) -> str:
    # Comments and processing instructions have a callable .tag rather than a
    # string one, hence the isinstance guard at every call site.
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _origin(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise SitemapUnavailable(f"Not an absolute URL: {url!r}")
    return f"{parts.scheme}://{parts.netloc}"


def same_site(a: str, b: str) -> bool:
    """Host comparison that tolerates the www/apex split.

    Necessary, not cosmetic: robots.txt on www.sanasafinaz.com points at
    https://sanasafinaz.com/sitemap.xml, so every URL in that sitemap has a
    different netloc from the homepage the user typed. A strict netloc
    equality check discards the entire result set.
    """

    def host(u: str) -> str:
        netloc = urlsplit(u).netloc.lower()
        netloc = netloc.rsplit("@", 1)[-1].split(":", 1)[0]
        return netloc[4:] if netloc.startswith("www.") else netloc

    return host(a) == host(b) and bool(host(a))


def _priority(sitemap_url: str) -> int:
    lowered = sitemap_url.lower()
    for keywords, score in _SITEMAP_PRIORITY:
        if any(keyword in lowered for keyword in keywords):
            return score
    return _DEFAULT_PRIORITY


def _fetch(url: str, timeout: int) -> bytes | None:
    """Returns the body, or None for anything not worth raising over — a 404,
    a connection error, a body past the size budget. Discovery is best-effort
    across several candidate URLs, so an individual miss is normal control
    flow rather than an error.
    """

    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=timeout
        )
    except requests.RequestException as exc:
        logger.info("Sitemap fetch failed for %s: %s", url, exc)
        return None

    if response.status_code != 200:
        logger.info("Sitemap fetch for %s returned %s", url, response.status_code)
        return None

    content = response.content
    if len(content) > _MAX_SITEMAP_BYTES:
        logger.warning(
            "Sitemap %s is %s bytes, past the %s budget — skipped",
            url, len(content), _MAX_SITEMAP_BYTES,
        )
        return None

    # .xml.gz is common and requests only auto-decompresses Content-Encoding,
    # not a gzipped body served as an ordinary file.
    if content[:2] == b"\x1f\x8b":
        try:
            content = gzip.decompress(content)
        except OSError as exc:
            logger.info("Sitemap %s is not valid gzip: %s", url, exc)
            return None

    return content


def _entries(content: bytes) -> list[tuple[str, str]]:
    """Parses one sitemap into (kind, url) pairs where kind is "url" or
    "sitemap". Returns [] for anything unparseable so the caller can move on
    to the next candidate.

    Only a <loc> that is a direct child of <url> or <sitemap> counts. This is
    what keeps Shopify's <image:image><image:loc> entries — the CDN asset URLs
    interleaved with every collection — out of the results.
    """

    try:
        root = etree.fromstring(content, parser=_PARSER)
    except etree.XMLSyntaxError as exc:
        logger.info("Malformed sitemap XML: %s", exc)
        return []

    if root is None:
        return []

    found: list[tuple[str, str]] = []
    for node in root:
        kind = _localname(node.tag)
        if kind not in ("url", "sitemap"):
            continue
        for child in node:
            if _localname(child.tag) == "loc" and child.text and child.text.strip():
                found.append((kind, child.text.strip()))
                break
    return found


def _sitemap_candidates(origin: str) -> list[str]:
    """robots.txt first — it is the site's own pointer and is frequently the
    only way to find a sitemap that isn't at the conventional path. Falls back
    to the two conventional locations when robots.txt is missing or silent.
    """

    body = _fetch(urljoin(origin + "/", "robots.txt"), _ROBOTS_TIMEOUT)
    if body is not None:
        try:
            declared = _SITEMAP_DIRECTIVE.findall(body.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 — a undecodable robots.txt is just a miss
            declared = []
        if declared:
            logger.info("robots.txt at %s declared %s sitemap(s)", origin, len(declared))
            return declared

    return [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"]


def _interleave(groups: list[tuple[int, list[str]]]) -> list[str]:
    """Round-robins *within* each priority tier, then concatenates the tiers
    in order.

    Both halves matter. Round-robin inside a tier stops one large sitemap
    starving its peers. Keeping the tiers separate stops the round-robin
    itself defeating the priority ordering: a flat merge across every group
    hands individual product SKUs a quarter of the caller's budget, so a
    storefront's results come back padded with pages named after part
    numbers instead of its remaining categories.
    """

    merged: list[str] = []
    for priority in sorted({p for p, _ in groups}):
        tier = [urls for p, urls in groups if p == priority]
        for column in range(max((len(u) for u in tier), default=0)):
            for urls in tier:
                if column < len(urls):
                    merged.append(urls[column])
    return merged


def collect_sitemap_urls(homepage_url: str) -> list[str]:
    """Walks this site's sitemaps and returns page URLs in priority order.

    Handles a plain urlset, a sitemap index, and an index of indexes, bounded
    by `_MAX_SITEMAPS_FETCHED` fetches so a pathological index cannot turn one
    "add competitor" click into hundreds of requests.

    Raises SitemapUnavailable when nothing usable was found — no sitemap, all
    candidates 404, or every document malformed. The caller decides what to do
    about that; this module never falls back to a browser.
    """

    origin = _origin(homepage_url)

    queue: list[tuple[int, str]] = [(_priority(u), u) for u in _sitemap_candidates(origin)]
    seen_sitemaps: set[str] = set()
    # URLs are kept grouped by the sitemap they came from, then interleaved
    # at the end. Returning them concatenated instead starves whole
    # categories: a storefront's /pages sitemap alone holds 60+ URLs, which
    # is more than the caller's entire page budget, so every collection page
    # — the category pages an analyst actually wants — would be cut.
    groups: list[tuple[int, list[str]]] = []
    seen_pages: set[str] = set()
    total = 0
    fetches = 0
    parsed_any = False

    while queue and fetches < _MAX_SITEMAPS_FETCHED and total < _MAX_URLS:
        # Re-sorted every pass rather than once up front: children discovered
        # inside an index have to compete with the queue on their own
        # priority, or a low-value index enqueued early would be walked before
        # a high-value one found later.
        queue.sort(key=lambda item: item[0])
        priority, sitemap_url = queue.pop(0)

        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)

        body = _fetch(sitemap_url, _SITEMAP_TIMEOUT)
        fetches += 1
        if body is None:
            continue

        entries = _entries(body)
        if entries:
            parsed_any = True

        group: list[str] = []
        for kind, url in entries:
            if kind == "sitemap":
                if url not in seen_sitemaps:
                    queue.append((_priority(url), url))
                continue
            if not same_site(url, homepage_url):
                continue
            if url in seen_pages:
                continue
            seen_pages.add(url)
            group.append(url)
            total += 1
            if total >= _MAX_URLS:
                break
        if group:
            groups.append((priority, group))

    page_urls = _interleave(groups)
    if not page_urls:
        raise SitemapUnavailable(
            "no sitemap could be read for this site"
            if not parsed_any
            else "sitemaps were read but contained no pages on this site"
        )

    logger.info(
        "Sitemap discovery for %s: %s URLs from %s sitemap fetch(es)",
        homepage_url, len(page_urls), fetches,
    )
    return page_urls
