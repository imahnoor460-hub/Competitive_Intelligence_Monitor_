"""Sitemap-first page discovery (services/sitemap_discovery_service.py and
the orchestration in services/surface_discovery_service.discover_surfaces).

The load-bearing assertion in this file is that nothing here launches
Chromium: the deployment runs on a 512MB container where one browser pass
peaked at ~596MB, so the default path has to be browser-free and a missing
sitemap has to fail loudly rather than quietly reaching for Playwright.
"""
import pytest
import requests

import app.services.sitemap_discovery_service as sitemap_service
import app.services.surface_discovery_service as discovery_service
from app.models.surface import SurfaceType
from app.services.surface_discovery_service import (
    discover_surfaces,
    SurfaceDiscoveryError,
)

HOME = "https://shop.example.com"


def _urlset(*urls):
    body = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
    ).encode()


def _index(*urls):
    body = "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</sitemapindex>'
    ).encode()


class _Response:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content

    @property
    def text(self):
        return self.content.decode()


@pytest.fixture()
def http(monkeypatch):
    """Routes sitemap_discovery_service's requests.get at an in-memory map of
    URL -> body. Anything not in the map answers 404, which is what a site
    without that file really does.
    """
    class _Routes(dict):
        """A dict that can also carry the call log."""
        calls: list

    routes = _Routes()
    calls: list[str] = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        value = routes.get(url)
        if value is None:
            return _Response(404, b"not found")
        if isinstance(value, Exception):
            raise value
        if isinstance(value, str):
            value = value.encode()
        return _Response(200, value)

    monkeypatch.setattr(sitemap_service.requests, "get", fake_get)
    routes.calls = calls
    return routes


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    """Fails loudly if any test in this file reaches Playwright. A silent
    browser launch is the exact regression this whole module exists to
    prevent, so it must be an error rather than a slow test.
    """
    def _boom(*a, **k):
        raise AssertionError("Chromium was launched — the browser-free path is broken")

    monkeypatch.setattr(discovery_service, "sync_playwright", _boom)


def test_robots_txt_sitemap_directive_is_followed(http):
    http[f"{HOME}/robots.txt"] = "User-agent: *\nSitemap: https://shop.example.com/custom-sitemap.xml\n"
    http[f"{HOME}/custom-sitemap.xml"] = _urlset(f"{HOME}/pages/about-us")

    results = discover_surfaces(HOME)

    urls = [u for _, _, u in results]
    assert f"{HOME}/pages/about-us" in urls
    # The conventional path is never tried once robots.txt names one.
    assert f"{HOME}/sitemap.xml" not in http.calls


def test_falls_back_to_conventional_path_when_robots_is_silent(http):
    http[f"{HOME}/robots.txt"] = "User-agent: *\nDisallow: /admin\n"
    http[f"{HOME}/sitemap.xml"] = _urlset(f"{HOME}/pages/contact")

    urls = [u for _, _, u in discover_surfaces(HOME)]

    assert f"{HOME}/pages/contact" in urls


def test_sitemap_index_is_expanded(http):
    http[f"{HOME}/sitemap.xml"] = _index(f"{HOME}/sitemap_pages_1.xml")
    http[f"{HOME}/sitemap_pages_1.xml"] = _urlset(f"{HOME}/pages/about-us", f"{HOME}/pages/contact")

    urls = [u for _, _, u in discover_surfaces(HOME)]

    assert f"{HOME}/pages/about-us" in urls
    assert f"{HOME}/pages/contact" in urls


def test_nested_index_of_indexes_is_walked(http):
    http[f"{HOME}/sitemap.xml"] = _index(f"{HOME}/outer.xml")
    http[f"{HOME}/outer.xml"] = _index(f"{HOME}/inner_pages.xml")
    http[f"{HOME}/inner_pages.xml"] = _urlset(f"{HOME}/pages/deep-page")

    urls = [u for _, _, u in discover_surfaces(HOME)]

    assert f"{HOME}/pages/deep-page" in urls


def test_collection_page_and_blog_urls_are_named_and_classified(http):
    http[f"{HOME}/sitemap.xml"] = _index(
        f"{HOME}/sitemap_pages_1.xml",
        f"{HOME}/sitemap_collections_1.xml",
        f"{HOME}/sitemap_blogs_1.xml",
    )
    http[f"{HOME}/sitemap_pages_1.xml"] = _urlset(f"{HOME}/pages/about-us")
    http[f"{HOME}/sitemap_collections_1.xml"] = _urlset(f"{HOME}/collections/ready-to-wear")
    http[f"{HOME}/sitemap_blogs_1.xml"] = _urlset(f"{HOME}/blogs/news")

    by_url = {u: (t, n) for t, n, u in discover_surfaces(HOME)}

    # Names come from the URL slug, since a sitemap carries no anchor text.
    assert by_url[f"{HOME}/pages/about-us"][1] == "About Us"
    assert by_url[f"{HOME}/collections/ready-to-wear"][1] == "Ready To Wear"
    # "news" is a blog keyword, so the type survives the trip through the slug.
    assert by_url[f"{HOME}/blogs/news"] == (SurfaceType.blog, "News")


def test_product_urls_never_crowd_out_collections(http):
    """A storefront's product sitemap holds thousands of SKU pages listed
    ahead of the collections sitemap. Products rank last, so every collection
    is reached before the first SKU regardless of file order — but they are
    still real pages and fill leftover budget rather than being discarded.
    """
    http[f"{HOME}/sitemap.xml"] = _index(
        f"{HOME}/sitemap_products_1.xml", f"{HOME}/sitemap_collections_1.xml"
    )
    http[f"{HOME}/sitemap_products_1.xml"] = _urlset(
        *[f"{HOME}/products/sku-{i}" for i in range(60)]
    )
    http[f"{HOME}/sitemap_collections_1.xml"] = _urlset(f"{HOME}/collections/sale")

    urls = [u for _, _, u in discover_surfaces(HOME)]

    assert f"{HOME}/collections/sale" in urls
    assert urls.index(f"{HOME}/collections/sale") < min(
        i for i, u in enumerate(urls) if "/products/" in u
    )


def test_collections_and_pages_fill_the_budget_before_products(http):
    """The realistic storefront shape: enough pages and collections to fill
    the cap, so no SKU page is monitored at all.
    """
    http[f"{HOME}/sitemap.xml"] = _index(
        f"{HOME}/sitemap_products_1.xml",
        f"{HOME}/sitemap_pages_1.xml",
        f"{HOME}/sitemap_collections_1.xml",
    )
    http[f"{HOME}/sitemap_products_1.xml"] = _urlset(
        *[f"{HOME}/products/sku-{i}" for i in range(200)]
    )
    http[f"{HOME}/sitemap_pages_1.xml"] = _urlset(
        *[f"{HOME}/pages/p-{i}" for i in range(30)]
    )
    http[f"{HOME}/sitemap_collections_1.xml"] = _urlset(
        *[f"{HOME}/collections/c-{i}" for i in range(30)]
    )

    urls = [u for _, _, u in discover_surfaces(HOME)]

    assert not any("/products/" in u for u in urls)
    # Both kinds are represented rather than one starving the other.
    assert sum("/pages/" in u for u in urls) > 5
    assert sum("/collections/" in u for u in urls) > 5


def test_image_locs_are_ignored(http):
    """Shopify interleaves <image:loc> CDN URLs inside every <url> entry.
    Treating those as pages fills the result with asset URLs.
    """
    http[f"{HOME}/sitemap.xml"] = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">'
        f"<url><loc>{HOME}/collections/sale</loc>"
        "<image:image><image:loc>https://cdn.example.net/a.png</image:loc></image:image>"
        "</url></urlset>"
    ).encode()

    urls = [u for _, _, u in discover_surfaces(HOME)]

    assert f"{HOME}/collections/sale" in urls
    assert not any("cdn.example.net" in u for u in urls)


def test_apex_and_www_are_treated_as_one_site(http):
    """robots.txt on www often points at an apex-host sitemap whose URLs then
    all carry the other netloc. A strict host match discards everything.
    """
    www = "https://www.example.com"
    http[f"{www}/robots.txt"] = "Sitemap: https://example.com/sitemap.xml\n"
    http["https://example.com/sitemap.xml"] = _urlset("https://example.com/pages/about-us")

    urls = [u for _, _, u in discover_surfaces(www)]

    assert "https://example.com/pages/about-us" in urls


def test_offsite_urls_are_dropped(http):
    http[f"{HOME}/sitemap.xml"] = _urlset(
        f"{HOME}/pages/about-us", "https://someone-else.example.org/pages/spam"
    )

    urls = [u for _, _, u in discover_surfaces(HOME)]

    assert not any("someone-else" in u for u in urls)


def test_homepage_is_always_first(http):
    http[f"{HOME}/sitemap.xml"] = _urlset(f"{HOME}/pages/about-us")

    results = discover_surfaces(HOME)

    assert results[0][1] == "Home"


def test_malformed_sitemap_fails_without_touching_the_browser(http):
    http[f"{HOME}/sitemap.xml"] = b"<urlset><url><loc>oops"

    with pytest.raises(SurfaceDiscoveryError) as exc:
        discover_surfaces(HOME)

    assert "ENABLE_BROWSER_DISCOVERY" in str(exc.value)


def test_missing_sitemap_fails_without_touching_the_browser(http):
    # Every candidate 404s — the `http` fixture's default.
    with pytest.raises(SurfaceDiscoveryError):
        discover_surfaces(HOME)


def test_network_error_fails_without_touching_the_browser(http):
    http[f"{HOME}/robots.txt"] = requests.ConnectionError("dns failure")
    http[f"{HOME}/sitemap.xml"] = requests.ConnectionError("dns failure")
    http[f"{HOME}/sitemap_index.xml"] = requests.ConnectionError("dns failure")

    with pytest.raises(SurfaceDiscoveryError):
        discover_surfaces(HOME)


def test_browser_fallback_runs_only_when_explicitly_enabled(http, monkeypatch):
    """The one path that may reach Chromium, and only with the flag on."""
    monkeypatch.setattr("app.core.config.settings.enable_browser_discovery", True)
    called: list[str] = []
    monkeypatch.setattr(
        discovery_service,
        "_discover_via_browser",
        lambda url: called.append(url) or [(SurfaceType.other, "Home", url)],
    )

    results = discover_surfaces(HOME)  # no sitemap in the fixture -> 404s

    assert called == [HOME]
    assert results == [(SurfaceType.other, "Home", HOME)]


def test_browser_fallback_is_not_reached_when_the_sitemap_works(http, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.enable_browser_discovery", True)
    monkeypatch.setattr(
        discovery_service,
        "_discover_via_browser",
        lambda url: pytest.fail("browser used despite a working sitemap"),
    )
    http[f"{HOME}/sitemap.xml"] = _urlset(f"{HOME}/pages/about-us")

    assert len(discover_surfaces(HOME)) == 2


def test_result_is_capped(http):
    http[f"{HOME}/sitemap.xml"] = _urlset(*[f"{HOME}/pages/p-{i}" for i in range(200)])

    assert len(discover_surfaces(HOME)) == discovery_service._MAX_DISCOVERED


def test_oversized_sitemap_is_skipped(http):
    """A retailer's product sitemap can be tens of megabytes; parsing one in a
    512MB container is the thing this budget exists to prevent.
    """
    http[f"{HOME}/sitemap.xml"] = b"<x>" + b"a" * sitemap_service._MAX_SITEMAP_BYTES

    with pytest.raises(SurfaceDiscoveryError):
        discover_surfaces(HOME)


def test_gzipped_sitemap_is_decompressed(http):
    import gzip

    http[f"{HOME}/sitemap.xml"] = gzip.compress(_urlset(f"{HOME}/pages/about-us"))

    urls = [u for _, _, u in discover_surfaces(HOME)]

    assert f"{HOME}/pages/about-us" in urls


def test_slug_digits_survive_naming(http):
    """Regression: a character-class split on "%20" ate the digits 2 and 0,
    turning "diffusion-2-0" into "Diffusion".
    """
    http[f"{HOME}/sitemap.xml"] = _urlset(f"{HOME}/pages/diffusion-2-0")

    names = [n for _, n, _ in discover_surfaces(HOME)]

    assert "Diffusion 2 0" in names


def test_homepage_named_page_does_not_duplicate_home(http):
    http[f"{HOME}/sitemap.xml"] = _urlset(f"{HOME}/pages/home")

    names = [n for _, n, _ in discover_surfaces(HOME)]

    assert names.count("Home") == 1
