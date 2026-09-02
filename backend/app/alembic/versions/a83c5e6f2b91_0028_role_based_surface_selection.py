"""0028_role_based_surface_selection

Re-selects which surfaces are watched, using a page's *role* rather than the
order a sitemap happened to list it in, and lowers the cap to 3.

`0026`/`0027` capped the count but kept the old ranking, which sorted by
`SurfaceType` and then by id. On real data 363 of 365 surfaces were typed
`other`, so the type term was constant and the tiebreak — insertion order —
decided everything. Competitors ended up watching `/collections/test-coll-1`,
`/customer_authentication/redirect` and `/pages/api` daily.

This revision re-runs the selection over every competitor with the role rules,
so existing installs get the same three pages a freshly discovered competitor
would: homepage first, then the two highest-ranked business pages.

Rows are only ever flipped, never deleted, and nothing is scheduled by this:
what comes out inactive is watched on no cadence at all. A surface switched
back on re-baselines on its next check; its history is keyed to the row and is
not lost either way.

The rules below are a frozen copy of services/surface_selection.py as of this
revision — a migration has to keep doing the same thing years later, whatever
that module goes on to become.

Revision ID: a83c5e6f2b91
Revises: f1b6c30d9a77
Create Date: 2026-09-02 00:00:00.000000

"""
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a83c5e6f2b91'
down_revision: Union[str, Sequence[str], None] = 'f1b6c30d9a77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CAP = 3

_HOMEPAGE, _EXCLUDED = 0, 99

_EXCLUDED_TOKENS = {
    "login", "signin", "signup", "register", "account", "accounts", "auth",
    "customer", "customer_authentication", "redirect", "cart", "checkout",
    "order", "orders", "wishlist", "compare", "search",
    "api", "sitemap", "feed", "rss", "cdn", "assets", "admin",
    "privacy", "terms", "tos", "legal", "policy", "policies", "refund",
    "refunds", "returns", "cookie", "cookies", "disclaimer", "copyright",
    "test", "tests", "demo", "sample", "staging", "dev", "placeholder",
    "frontpage", "glossary", "sizing", "size-guide", "sizechart",
    "giftcard", "giftcards", "gift-card", "gift-cards", "voucher",
}

_ROLE_TOKENS = [
    (1, {"pricing", "price", "prices", "plans", "packages", "tariff"}),
    (2, {"products", "shop", "catalog", "catalogue", "services", "merchandise"}),
    (3, {"features", "solutions", "platform", "product", "capabilities",
         "overview"}),
    (4, {"sale", "sales", "offer", "offers", "deal", "deals", "discount",
         "discounts", "promo", "promotion", "promotions", "clearance",
         "outlet", "special", "specials", "bogo"}),
    (5, {"new", "arrivals", "latest", "featured", "trending", "bestseller",
         "bestsellers", "best-sellers", "top"}),
    (7, {"blog", "blogs", "news", "article", "articles", "insights", "press",
         "stories"}),
    (8, {"about", "about-us", "contact", "contacts", "store", "stores",
         "locator", "locations", "store-locator", "careers", "career", "jobs",
         "faq", "faqs", "support", "help"}),
]

_PHRASE_ROLES = [
    ("how-it-works", 3),
    ("shop-by", 6),
    ("shop-the-look", 6),
    ("store-locator", 8),
    ("store-locations", 8),
    ("how-to", 8),
    ("size-guide", _EXCLUDED),
    ("track-order", _EXCLUDED),
    ("order-tracking", _EXCLUDED),
]

_LISTING_SEGMENTS = {"collections", "collection", "category", "categories",
                     "shop", "catalog"}
_ITEM_SEGMENTS = {"product", "products", "item", "items", "p", "sku"}

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_TRAILING_DIGITS = re.compile(r"\d+$")


def _tokens(text: str) -> set:
    out = set()
    for raw in _TOKEN_SPLIT.split(text.lower()):
        if not raw:
            continue
        out.add(raw)
        stripped = _TRAILING_DIGITS.sub("", raw)
        if stripped and stripped != raw:
            out.add(stripped)
    return out


def _path_of(url: str) -> str:
    """Path only, without urlsplit: a migration should not depend on how a
    library version parses a malformed stored URL."""

    without_scheme = (url or "").split("://", 1)[-1]
    slash = without_scheme.find("/")
    if slash == -1:
        return ""
    path = without_scheme[slash:].split("?", 1)[0].split("#", 1)[0]
    return path.strip("/").lower()


def _identity(url: str) -> str:
    """Host and path, for spotting two rows that are the same page — a
    competitor's homepage is commonly stored twice, once by create_competitor
    and once by discovery, and both would take a slot."""

    without_scheme = (url or "").split("://", 1)[-1]
    host = without_scheme.split("/", 1)[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}/{_path_of(url)}"


def _role(url: str, name: str | None) -> int:
    path = _path_of(url)
    if path == "":
        return _HOMEPAGE

    segments = [seg for seg in path.split("/") if seg]
    path_tokens = _tokens(path)

    if path_tokens & _EXCLUDED_TOKENS:
        return _EXCLUDED

    for index, segment in enumerate(segments[:-1]):
        if segment in _ITEM_SEGMENTS and index + 1 < len(segments):
            return _EXCLUDED

    for phrase, role in _PHRASE_ROLES:
        if phrase in path:
            return role

    if segments[-1] == "store":
        return 2

    candidates = path_tokens | {segments[-1]}
    for role, tokens in _ROLE_TOKENS:
        if candidates & tokens:
            return role

    if segments[0] in _LISTING_SEGMENTS:
        return 6

    if name:
        name_tokens = _tokens(name)
        for role, tokens in _ROLE_TOKENS:
            if name_tokens & tokens:
                return role

    return 9


def _rank(row) -> tuple:
    path = _path_of(row.url)
    return (
        _role(row.url, row.name),
        len([seg for seg in path.split("/") if seg]),
        len(path),
        row.id,
    )


def upgrade() -> None:
    bind = op.get_bind()

    rows = bind.execute(
        sa.text("SELECT id, competitor_id, name, url, is_active FROM surfaces")
    ).fetchall()

    by_competitor: dict = {}
    for row in rows:
        by_competitor.setdefault(row.competitor_id, []).append(row)

    activate: list = []
    deactivate: list = []

    for competitor_surfaces in by_competitor.values():
        ordered = sorted(competitor_surfaces, key=_rank)

        watchable = []
        seen = set()
        for row in ordered:
            identity = _identity(row.url)
            if _role(row.url, row.name) == _EXCLUDED or identity in seen:
                continue
            seen.add(identity)
            watchable.append(row)

        watched_ids = {r.id for r in watchable[:_CAP]}
        for row in competitor_surfaces:
            if row.id in watched_ids and not row.is_active:
                activate.append(row.id)
            elif row.id not in watched_ids and row.is_active:
                deactivate.append(row.id)

    for ids, value in ((activate, True), (deactivate, False)):
        # Chunked: some drivers cap how many bind parameters one statement may
        # carry, and this list is thousands of ids on a busy install.
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            bind.execute(
                sa.text(
                    "UPDATE surfaces SET is_active = :value WHERE id IN :ids"
                ).bindparams(
                    sa.bindparam("value", value=value),
                    sa.bindparam("ids", value=tuple(chunk), expanding=True),
                )
            )


def downgrade() -> None:
    """Deliberately a no-op, as in 0026 and 0027.

    There is no previous per-row state to restore — `is_active` carries no
    history — and reactivating rows wholesale would re-arm daily checks for
    pages nobody wants watched. Nothing was deleted.
    """
