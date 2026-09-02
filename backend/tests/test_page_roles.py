"""What counts as a main business page.

Every case here is either a rule the product asked for or a URL taken from the
live database that the previous ranking got wrong. The old sort was
`(homepage, SurfaceType, id)`, and since 363 of 365 real surfaces were typed
`other`, it decided by sitemap insertion order — which is how competitors ended
up being checked daily on a login redirect and two test collections.
"""

from app.models.surface import Surface, SurfaceType
from app.services.surface_selection import (
    PageRole,
    classify_page_role,
    partition_by_cap,
    surface_rank,
)


def _surface(id, url, name=None, surface_type=SurfaceType.other):
    return Surface(id=id, competitor_id=1, surface_type=surface_type, url=url, name=name)


# --- the requested priority order -------------------------------------------

def test_the_five_requested_roles_rank_in_the_requested_order():
    assert (
        PageRole.homepage
        < PageRole.pricing
        < PageRole.products
        < PageRole.features
        < PageRole.sale
    )


def test_each_requested_role_is_recognised():
    cases = {
        "https://rival.com": PageRole.homepage,
        "https://rival.com/": PageRole.homepage,
        "https://rival.com/pricing": PageRole.pricing,
        "https://rival.com/plans": PageRole.pricing,
        "https://rival.com/products": PageRole.products,
        "https://rival.com/shop": PageRole.products,
        "https://rival.com/store": PageRole.products,
        "https://rival.com/services": PageRole.products,
        "https://rival.com/features": PageRole.features,
        "https://rival.com/solutions": PageRole.features,
        "https://rival.com/features/how-it-works": PageRole.features,
        "https://rival.com/collections/sale": PageRole.sale,
        "https://rival.com/offers": PageRole.sale,
        "https://rival.com/collections/clearance": PageRole.sale,
    }

    for url, expected in cases.items():
        assert classify_page_role(url) == expected, url


def test_seasonal_campaign_pages_are_still_sale_pages():
    """"sale26" is a 2026 sale, not an unknown word — trailing digits are
    stripped when tokenizing or every seasonal page reads as unclassified."""

    for url in (
        "https://rival.com/collections/sale26",
        "https://rival.com/collections/men-sale26",
        "https://rival.com/collections/unstitched-sale25",
        "https://rival.com/collections/men-sale",
    ):
        assert classify_page_role(url) == PageRole.sale, url


# --- the exclusions ---------------------------------------------------------

def test_the_pages_that_used_to_be_checked_daily_are_excluded():
    """Every one of these was in a real competitor's watched set."""

    for url in (
        "https://rival.com/customer_authentication/redirect",
        "https://rival.com/collections/test-coll-1",
        "https://rival.com/collections/test-col-3",
        "https://rival.com/pages/api",
        "https://rival.com/collections/frontpage",
    ):
        assert classify_page_role(url) == PageRole.excluded, url


def test_plumbing_legal_and_scaffolding_are_excluded():
    for url in (
        "https://rival.com/account/login",
        "https://rival.com/cart",
        "https://rival.com/checkout",
        "https://rival.com/search",
        "https://rival.com/pages/privacy-policy",
        "https://rival.com/pages/terms",
        "https://rival.com/pages/refund-policy",
        "https://rival.com/pages/size-guide",
        "https://rival.com/pages/demo",
        "https://rival.com/products/gift-card",
        "https://rival.com/sitemap.xml",
    ):
        assert classify_page_role(url) == PageRole.excluded, url


def test_one_product_is_excluded_but_the_catalogue_is_not():
    assert classify_page_role("https://rival.com/products/blue-lawn-shirt") == (
        PageRole.excluded
    )
    assert classify_page_role("https://rival.com/products") == PageRole.products


# --- compound names whose parts mislead -------------------------------------

def test_a_store_locator_is_a_company_page_not_a_product_page():
    """"store-locator" contains "store"; "how-to-shop-online" contains "shop".
    Both were being watched as product pages."""

    assert classify_page_role("https://rival.com/pages/store-locator") == (
        PageRole.company
    )
    assert classify_page_role("https://rival.com/pages/shopify-store-locator") == (
        PageRole.company
    )
    assert classify_page_role("https://rival.com/pages/how-to-shop-online") == (
        PageRole.company
    )


def test_the_url_beats_the_nav_label():
    """A mega-menu heading describes the menu, not the page: the real
    /collections/sale26 is named "SHOP BY CATEGORY", which read as a product
    page and outranked the actual sale page."""

    assert classify_page_role(
        "https://rival.com/collections/sale26", "SHOP BY CATEGORY"
    ) == PageRole.sale


def test_the_name_still_decides_a_url_that_says_nothing():
    """Browser-discovered surfaces can have opaque URLs, and there the nav
    text is the only signal available."""

    assert classify_page_role("https://rival.com/c/9f2a", "Pricing") == (
        PageRole.pricing
    )


# --- selection ---------------------------------------------------------------

def test_selection_prefers_business_pages_over_discovery_order():
    """The tail is added first, so an insertion-order sort would take it."""

    surfaces = [
        _surface(1, "https://rival.com/collections/test-coll-1"),
        _surface(2, "https://rival.com/customer_authentication/redirect"),
        _surface(3, "https://rival.com/pages/about-us"),
        _surface(4, "https://rival.com/collections/winter-shawls"),
        _surface(5, "https://rival.com/"),
        _surface(6, "https://rival.com/collections/sale"),
        _surface(7, "https://rival.com/products"),
    ]

    watched, unwatched = partition_by_cap(surfaces, limit=3)

    assert [s.id for s in watched] == [5, 7, 6]  # homepage, products, sale
    assert {s.id for s in unwatched} == {1, 2, 3, 4}


def test_the_homepage_always_leads():
    surfaces = [
        _surface(1, "https://rival.com/pricing"),
        _surface(2, "https://rival.com/"),
        _surface(3, "https://rival.com/collections/sale"),
    ]

    watched, _ = partition_by_cap(surfaces, limit=3)

    assert watched[0].id == 2


def test_a_missing_role_is_replaced_by_the_next_best_one():
    """A storefront has no pricing page and often no features page; the ladder
    just moves down rather than leaving a slot empty."""

    surfaces = [
        _surface(1, "https://rival.com/"),
        _surface(2, "https://rival.com/collections/new-arrivals"),
        _surface(3, "https://rival.com/collections/lawn"),
        _surface(4, "https://rival.com/blog"),
    ]

    watched, _ = partition_by_cap(surfaces, limit=3)

    assert [s.id for s in watched] == [1, 2, 3]


def test_excluded_pages_are_never_watched_even_below_the_cap():
    """Three good pages and a login redirect is two good pages and a wasted
    daily fetch."""

    surfaces = [
        _surface(1, "https://rival.com/"),
        _surface(2, "https://rival.com/account/login"),
        _surface(3, "https://rival.com/cart"),
    ]

    watched, unwatched = partition_by_cap(surfaces, limit=3)

    assert [s.id for s in watched] == [1]
    assert {s.id for s in unwatched} == {2, 3}


def test_the_umbrella_page_wins_over_its_variants():
    """Same role, so depth and length break the tie — /collections/sale before
    /collections/sale-men-eastern-summer."""

    shallow = _surface(9, "https://rival.com/collections/sale")
    deep = _surface(2, "https://rival.com/collections/sale-men-eastern-summer")

    assert surface_rank(shallow) < surface_rank(deep)


def test_the_order_is_total():
    """Two pages alike in role, depth and length still order by id, so the
    scheduler, the sweep and the migrations agree every time."""

    a = _surface(7, "https://rival.com/collections/aaa")
    b = _surface(8, "https://rival.com/collections/bbb")

    assert surface_rank(a) < surface_rank(b)


def test_a_duplicated_page_does_not_take_two_slots():
    """A competitor created with a website_url gets a homepage surface, and
    discovery then finds the homepage again from the sitemap. One real
    competitor was spending two of its three slots on the same page."""

    surfaces = [
        _surface(1, "https://rival.com/"),
        _surface(2, "https://www.rival.com"),
        _surface(3, "https://rival.com/collections/sale"),
        _surface(4, "https://rival.com/products"),
    ]

    watched, unwatched = partition_by_cap(surfaces, limit=3)

    assert [s.id for s in watched] == [1, 4, 3]
    assert [s.id for s in unwatched] == [2]


def test_a_collection_url_is_a_category_whatever_its_nav_label_says():
    """The label belongs to the menu the link sat in, not the page: "SHOP BY
    CATEGORY" on /collections/boys-character was reading as the catalogue."""

    assert classify_page_role(
        "https://rival.com/collections/boys-character", "SHOP BY CATEGORY"
    ) == PageRole.category
    assert classify_page_role("https://rival.com/collections/shop-by-scent") == (
        PageRole.category
    )
