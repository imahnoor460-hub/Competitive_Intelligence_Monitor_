"""Which of a competitor's pages are actually watched.

Discovery is deliberately generous — a storefront's sitemap offers hundreds of
URLs and one pass keeps up to 40 of them. Watching is not: every watched page
is a daily scheduled fetch and a slot in every "Run check now" sweep, and this
runs on 0.2 shared CPU with 512MB. So discovery finds everything, and this
module decides which few of the findings are worth checking. The rest keep
their rows with `is_active = false` — not swept, not scheduled on any cadence,
not touched by any job — because "we found this page and chose not to watch it"
is worth keeping, and a user can switch one on by hand.

**Roles, not insertion order.** The first version of this ranked by
`SurfaceType`, which sounded principled and was not: `_classify` in
surface_discovery_service assigns those types from SaaS-shaped nav keywords
("pricing", "plans", "features"), and on a retail storefront essentially
nothing matches. 363 of 365 real surfaces were typed `other`, so the type term
was constant, the sort collapsed to `id` — sitemap insertion order — and the
selection came out as `/collections/test-coll-1`, `/customer_authentication/
redirect` and `/pages/api`.

A page's *role* is therefore read from its URL here, at selection time, rather
than trusted from a column written at discovery time. Two consequences worth
knowing: improving these rules re-selects existing competitors without a
re-crawl, and the rules must stay pure and cheap, because they run over every
surface of every competitor in a sweep.

The ordering is total — role, then path depth, then path length, then id — so
the scheduler, the sweep and the cleanup migrations all pick the same pages.
"""

import re
from urllib.parse import urlsplit

from app.core.config import settings
from app.models.surface import Surface

__all__ = [
    "PageRole",
    "classify_page_role",
    "surface_rank",
    "partition_by_cap",
    "cap_for_competitor",
]


class PageRole(int):
    """Selection priority. An int subclass rather than an Enum so it sorts
    directly and needs no `.value` at every comparison."""

    homepage = 0
    pricing = 1
    products = 2
    features = 3
    sale = 4
    new_arrivals = 5
    category = 6
    blog = 7
    company = 8
    unknown = 9
    excluded = 99


# Checked against path tokens before any role match. These are the pages that
# made the old ranking embarrassing: a login redirect, an API page and two test
# collections were being checked daily for real competitors. None of them can
# ever carry competitive signal, so they are never watched — not ranked last,
# excluded outright.
_EXCLUDED_TOKENS = {
    # auth / session / commerce plumbing
    "login", "signin", "signup", "register", "account", "accounts", "auth",
    "customer", "customer_authentication", "redirect", "cart", "checkout",
    "order", "orders", "wishlist", "compare", "search",
    # machine-facing
    "api", "sitemap", "feed", "rss", "cdn", "assets", "admin",
    # legal / policy boilerplate: changes here are lawyers, not strategy
    "privacy", "terms", "tos", "legal", "policy", "policies", "refund",
    "refunds", "returns", "cookie", "cookies", "disclaimer", "copyright",
    # scaffolding a site left published
    "test", "tests", "demo", "sample", "staging", "dev", "placeholder",
    # duplicates of the homepage, or reference pages with no offer in them
    "frontpage", "glossary", "sizing", "size-guide", "sizechart",
    # gift cards / vouchers: perpetual pages, never a competitive move
    "giftcard", "giftcards", "gift-card", "gift-cards", "voucher",
}

# Ordered: the first role whose tokens appear wins, so "sale" beats the generic
# "category" fallback for /collections/sale.
_ROLE_TOKENS: list[tuple[int, set[str]]] = [
    (PageRole.pricing, {"pricing", "price", "prices", "plans", "packages", "tariff"}),
    (PageRole.products, {"products", "shop", "catalog", "catalogue",
                         "services", "merchandise"}),
    (PageRole.features, {"features", "solutions", "platform", "product",
                         "capabilities", "overview"}),
    (PageRole.sale, {"sale", "sales", "offer", "offers", "deal", "deals",
                     "discount", "discounts", "promo", "promotion",
                     "promotions", "clearance", "outlet", "special",
                     "specials", "bogo"}),
    (PageRole.new_arrivals, {"new", "arrivals", "latest", "featured",
                             "trending", "bestseller", "bestsellers",
                             "best-sellers", "top"}),
    (PageRole.blog, {"blog", "blogs", "news", "article", "articles",
                     "insights", "press", "stories"}),
    (PageRole.company, {"about", "about-us", "contact", "contacts", "store",
                        "stores", "locator", "locations", "store-locator",
                        "careers", "career", "jobs", "faq", "faqs", "support",
                        "help"}),
]

# A category listing is a real business page; one product is not. Watching a
# single product means watching one SKU go out of stock.
_LISTING_SEGMENTS = {"collections", "collection", "category", "categories",
                     "shop", "catalog"}
_ITEM_SEGMENTS = {"product", "products", "item", "items", "p", "sku"}

# Compound names whose parts pull in the wrong direction on their own:
# "store-locator" contains "store", "how-to-shop-online" contains "shop". Both
# were being watched as product pages. Matched against the whole path string
# before any token rule, so the compound always beats its parts.
_PHRASE_ROLES: list[tuple[str, int]] = [
    ("how-it-works", PageRole.features),
    # "shop by scent", "shop the look": a way into the catalogue, not the
    # catalogue — and the bare "shop" token would otherwise rank them as one.
    ("shop-by", PageRole.category),
    ("shop-the-look", PageRole.category),
    ("store-locator", PageRole.company),
    ("store-locations", PageRole.company),
    ("how-to", PageRole.company),
    ("size-guide", PageRole.excluded),
    ("track-order", PageRole.excluded),
    ("order-tracking", PageRole.excluded),
]

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
# Trailing digits are campaign years, not distinct words: "sale26" and
# "summer25" have to tokenize to "sale" and "summer" or a seasonal sale page
# reads as an unknown one.
_TRAILING_DIGITS = re.compile(r"\d+$")


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for raw in _TOKEN_SPLIT.split(text.lower()):
        if not raw:
            continue
        out.add(raw)
        stripped = _TRAILING_DIGITS.sub("", raw)
        if stripped and stripped != raw:
            out.add(stripped)
    return out


def _path_of(url: str) -> str:
    return urlsplit(url or "").path.strip("/").lower()


def _identity(url: str) -> str:
    """Host and path, for spotting two rows that are the same page.

    A competitor created with a website_url gets a homepage surface from
    `create_competitor`, and discovery then finds the homepage again from the
    sitemap — one real competitor is spending two of its three slots on
    duplicate homepages. Deliberately not surface_discovery_service's
    `normalize_url`: importing that module pulls Playwright in, and this
    runs inside every sweep.
    """

    parts = urlsplit(url or "")
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}/{_path_of(url)}"


def classify_page_role(url: str, name: str | None = None) -> int:
    """The business role of one page, from its URL (and its name, when it has
    one that discovery captured from nav text rather than from the URL).

    Pure and side-effect free — it is called for every surface of every
    competitor in a sweep, and it is what a migration freezes a copy of.
    """

    path = _path_of(url)
    if path == "":
        return PageRole.homepage

    segments = [seg for seg in path.split("/") if seg]
    path_tokens = _tokens(path)

    if path_tokens & _EXCLUDED_TOKENS:
        return PageRole.excluded

    # /products/<handle> is one product; /products is the catalogue. Checked on
    # segments rather than tokens because only the *position* distinguishes
    # them.
    for index, segment in enumerate(segments[:-1]):
        if segment in _ITEM_SEGMENTS and index + 1 < len(segments):
            return PageRole.excluded

    for phrase, role in _PHRASE_ROLES:
        if phrase in path:
            return role

    # A bare /store is the storefront; "store" inside a longer name is almost
    # always a locator, which the phrase table above has already caught.
    if segments[-1] == "store":
        return PageRole.products

    # A hyphenated multi-word match ("about-us") never survives tokenization,
    # so the whole last segment is offered alongside the tokens.
    candidates = path_tokens | {segments[-1]}

    for role, tokens in _ROLE_TOKENS:
        if candidates & tokens:
            return role

    # The name only decides a page the URL could not. It is nav text for
    # browser-discovered surfaces, and a mega-menu heading describes the menu
    # rather than the page — /collections/sale26 is named "SHOP BY CATEGORY",
    # which read as a product page and outranked the actual sale page. A URL
    # is written by the site to describe its own content; anchor text is not.
    # It is consulted before the generic listing fallback below, though: an
    # opaque /c/9f2a labelled "Pricing" is a pricing page, not a nameless
    # category.
    # A /collections/ URL is a category listing whatever its nav label claims:
    # the label belongs to the menu the link sat in, so "SHOP BY CATEGORY" on
    # /collections/boys-character was reading as the product catalogue.
    if segments[0] in _LISTING_SEGMENTS:
        return PageRole.category

    if name:
        name_tokens = _tokens(name)
        for role, tokens in _ROLE_TOKENS:
            if name_tokens & tokens:
                return role

    return PageRole.unknown


def surface_rank(surface: Surface) -> tuple[int, int, int, int]:
    """Sort key — lower is watched first.

    Depth and length break ties inside a role so the umbrella page wins over
    its variants: /collections/sale before /collections/sale-men-eastern. `id`
    last keeps the order total, which is what lets the scheduler, the sweep and
    the migrations agree on the same set every time they are asked.
    """

    path = _path_of(surface.url)
    return (
        classify_page_role(surface.url, surface.name),
        len([seg for seg in path.split("/") if seg]),
        len(path),
        surface.id or 0,
    )


def cap_for_competitor() -> int:
    return settings.max_active_surfaces_per_competitor


def partition_by_cap(
    surfaces: list[Surface], limit: int | None = None
) -> tuple[list[Surface], list[Surface]]:
    """Split one competitor's surfaces into (watched, not watched).

    Excluded pages are never watched, even when a competitor has fewer
    surfaces than the cap — three good pages and a login redirect is two good
    pages and a wasted daily fetch. The homepage always leads when one exists,
    since it ranks 0.

    Returns lists rather than mutating, so the same function serves the
    scheduler, the sweep and the cleanup migrations without any of them
    inheriting the others' side effects.
    """

    if limit is None:
        limit = cap_for_competitor()

    ordered = sorted(surfaces, key=surface_rank)

    watchable: list[Surface] = []
    rejected: list[Surface] = []
    seen: set[str] = set()

    for surface in ordered:
        identity = _identity(surface.url)
        if surface_rank(surface)[0] == PageRole.excluded or identity in seen:
            rejected.append(surface)
            continue
        seen.add(identity)
        watchable.append(surface)

    return watchable[:limit], watchable[limit:] + rejected
