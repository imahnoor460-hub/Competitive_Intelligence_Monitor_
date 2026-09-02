# Features Added — Benchmarking, Site Summaries, Traffic, Category Pricing

This document covers the features built on top of the base Competitive Intelligence
Monitor (see `IMPLEMENTATION_PLAN.md` for the original phased build). Everything
below was added in a later session and is live in production use against the real
Churail workspace.

## 1. Own-site benchmarking & competitor comparison

**Problem:** every signal the app produced (materiality, classification, trend) was
per-competitor in isolation — there was no way to see "how are we doing relative to
this competitor" or compare two competitors side by side.

**What was built:**
- A workspace can set one **"own site"** — a hidden singleton `Competitor` row
  (`is_own_site=True`), excluded from the normal competitor list, reusing the exact
  same Surface/Snapshot/check pipeline as any tracked competitor.
- `GET /workspaces/{id}/competitors/{id}/comparison` returns everything the Compare
  page needs in one call: the competitor's profile, battlecard, change-trend summary,
  traffic, and a **benchmark** block — your own site if configured, or another
  competitor (`?compare_to=`) if not.
- New dedicated **Compare page** (`/competitors/[id]/compare`) — stat tiles, a
  dual trend chart (detections vs. materiality), a classification donut, and a
  side-by-side "you vs. them" table.
- Dashboard gained an own-site URL field and an upgraded comparison table.

**Backend:** `own_site_service.py`, `comparison_service.py`, `routers/own_site.py`,
new `comparison` endpoint on `routers/competitor.py`, migration `0014_own_site`.

## 2. Site summaries — "what's on their site right now"

**Problem:** every existing signal was diff-based (something has to *change* to be
noticed). A competitor with zero detected changes showed nothing at all, even though
their current categories, products, and live promotions are useful competitive
intelligence on their own.

**What was built:**
- An LLM reads a competitor's *current* page content (not a diff) and extracts:
  product/service **categories** and any live **promotions/offers**.
- Generated automatically after any check that finds new content (baseline or a
  detected change), and on-demand via an "Analyze site" / "Refresh" button on the
  Compare page.
- Categories render as color-coded pills; clicking one triggers the category-price
  lookup (below). Offers render as a highlighted list.

**Backend:** `site_summary_service.py`, `routers/site_summary.py`,
`CompetitorSiteSummary` model, migration `0015`.

### Real-world rendering problems this uncovered (and fixed)

Modern storefronts render their real content client-side, which broke the naive
approach in several ways — each was found and fixed against real competitor sites,
not synthetic tests:

1. **Plain HTTP fetch sees only the loading skeleton.** The existing change-detection
   scraper (`snapshot_service.py`, plain `requests.get`) never sees anything a page
   renders via JavaScript. Fixed by adding `rendered_content_service.py`, a
   Playwright-based fetch used specifically for site-summary generation (not the
   high-frequency diff pipeline, since launching a browser is much heavier than a
   plain GET).
2. **`networkidle` never resolves on real sites.** Many storefronts keep at least one
   connection open indefinitely (chat widgets, analytics beacons, polling), so
   waiting for full network idle timed out and fell back to the same broken
   plain-HTTP snapshot. Fixed by waiting for `domcontentloaded` plus a fixed settle
   delay instead.
3. **Some category menus never render as visible text at all.** Certain platforms
   hydrate their nav/category menu — and separately, promo/CTA banner tiles — from a
   JSON data blob embedded in a `<script>` tag, building the visible dropdown DOM
   only on hover, or not as text at all (a banner rendered as an image with the
   label used only for internal data). Fixed with a regex extractor that pulls
   `"name"/"handle"` (category-menu nodes) and `"label"/"link"` (promo-banner tiles)
   pairs out of the raw HTML as a fallback signal, handling backslash-escaped quotes
   for JSON embedded as a string literal inside a larger hydration payload.
4. **A fixed short settle delay under-captures on some pages.** The embedded JSON
   payload above wasn't always fully present at a short, fixed wait — tuned the
   delay and the extractor's noise-tolerance until repeated live captures came back
   consistent.
5. **An early "cheap path" optimization for the automatic trigger backfired.** To
   save a browser launch on every automatic check, the automatic trigger was changed
   to reuse the plain-HTTP snapshot instead of re-rendering. This meant an automatic
   run could silently overwrite a good, accurate summary with an empty one. Reverted:
   the automatic trigger always uses the accurate render now (one extra browser
   launch a few times a day per surface is not a real cost concern); the test suite
   is protected from the cost of this via a conftest-level default mock instead.

## 3. Traffic tracking (SimilarWeb)

- `TrafficSnapshot` model — one row per competitor per month per source.
- `POST /workspaces/{id}/competitors/{id}/traffic/refresh` fetches estimated monthly
  visits from SimilarWeb (requires `SIMILARWEB_API_KEY` and a `website_domain` set on
  the competitor's company profile) and upserts the month's snapshot.
- Rendered as a trend on the Compare page and in the benchmark comparison.

**Backend:** `traffic_service.py`, `routers/traffic.py`, migration `0013`.

## 4. Category price lookup

**Problem:** the site summary shows category labels ("Menswear," "Lawn," etc.) but
nothing about what those products actually cost.

**What was built:** clicking a category on the Compare page triggers a best-effort
lookup: find a link on the competitor's site whose visible text matches the category,
render that listing page, and have the LLM read off the prices shown there —
returning min/max/avg.

**Honest limits, by design, not oversight:**
- If no matching link can be found on the page (common for categories pulled from an
  embedded JSON menu with no corresponding visible link), it returns "No pricing
  found" rather than guessing a URL.
- Prices only reflect whatever products are visible on that one page load, not the
  competitor's full catalog.
- Live-verified against a real competitor: Asim Jofa's "Menswear" category returned
  26 real products, PKR 2,279–60,000.

**Backend:** `category_price_service.py`, `routers/category_price.py`,
`rendered_content_service.find_category_listing_url`, migration `0016`.

## 5. Competitor deletion

The delete endpoint existed but did a bare `db.delete(competitor)` with no cleanup of
dependent rows — it would hit a foreign-key violation on any competitor with real
activity (surfaces, change logs, battlecard, site summary, traffic history, company
profile). Rewrote it as a proper child-before-parent cascade
(`competitor_service.py`, mirroring the existing own-site deletion pattern), verified
against real Postgres, and added the missing "Delete competitor" button (with a
confirm prompt) on the competitor detail page. Response-library items — manually
authored by a team member, not derived from checks — survive with their competitor
link cleared rather than being deleted.

## 6. Dashboard/UI fixes

- **Category chips** switched from flat gray pills to a 3-hue rotating palette
  (blue/violet/teal), reusing the same color-coded idiom already used for
  classification badges elsewhere in the app.
- **Compare page "Recent changes" card** — the classification donut and stat tiles
  only ever showed aggregate counts ("1 promotion," "1 new feature") with no way to
  see what the actual change was. Added a list showing each change's classification
  badge, materiality score, full LLM rationale, and raw diff — the same treatment
  already used on the competitor detail page.
- **Battlecards page staleness** — it fetched the competitor list once on page load
  and never refetched, so a competitor deleted from a different page/tab kept
  showing until a manual reload. Now refetches automatically when the tab/window
  regains focus.


## 7. What gets watched: the per-competitor cap

Discovery finds up to 40 pages per pass and a storefront's sitemap offers
hundreds. Nothing limited how many of those were *watched*, so one workspace
had accumulated 282 active surfaces across eight competitors — 282 daily
scheduled checks, and a "Run check now" that queued 282 jobs for a worker
running two at a time. The progress counter sat at `0/173` long enough to read
as broken, which it effectively was.

Now `max_active_surfaces_per_competitor` (3 — the root page plus the two
highest-ranked business pages) decides what is watched, and the choice is made
by a page's **role**, not by the order a sitemap listed it in.

The first attempt ranked by `SurfaceType`, which sounded principled and was
not: `surface_discovery_service._classify` assigns those types from SaaS-shaped
nav keywords, and on a retail storefront almost nothing matches — 363 of 365
real surfaces were typed `other`. The type term was therefore constant, the
sort collapsed to insertion order, and competitors were being checked daily on
`/collections/test-coll-1`, `/customer_authentication/redirect` and
`/pages/api`.

`services/surface_selection.py` now reads a role from the URL at selection
time: homepage → pricing → products/services → features/solutions →
sale/offers → new arrivals → category → blog → company. Auth, cart, search,
API, legal, size guides, gift cards, test/demo pages and individual products
are excluded outright — never watched, even when a competitor has fewer pages
than the cap. Ties inside a role go to the shallower, shorter path, so
`/collections/sale` beats `/collections/sale-men-eastern`, and duplicates of
the same page (a homepage stored once by `create_competitor` and again by
discovery) collapse instead of taking two slots.

Two precedence rules earn their keep. A compound name beats its parts —
`/pages/store-locator` is a company page, not the catalogue — and the URL beats
the nav label, because a mega-menu heading describes the menu rather than the
page: the real `/collections/sale26` is labelled "SHOP BY CATEGORY", which read
as a product page and outranked the actual sale page. The label is consulted
only where the URL says nothing.

Because the role is computed rather than stored, improving these rules
re-selects existing competitors without a re-crawl. Migrations `0026`, `0027`
and `0028` applied each successive rule set to existing rows.

**Nothing is deleted, and nothing past the cap is watched on any cadence.**
Those surfaces keep their rows with `is_active = false`: not swept, not
scheduled daily or weekly, not touched by any job. They exist so a user can
switch one back on by hand.

**A sweep also fetches nothing it did not select.** Every check that found new
content used to regenerate the competitor's site summary, reading up to eight
more pages — a hidden multiplier of ~56 extra fetches on a seven-competitor
click. Sweeps now skip it; scheduled and single-surface checks still refresh
the summary, so it stays fresh without an interactive click paying for it.

Alongside it, three bounds so one bad page can never hold up a sweep:

- **A wall-clock ceiling on every fetch.** `requests`' `timeout` is per socket
  operation, so a server sending one byte inside each read window never trips
  it — the exact shape of hang that left a check `running` until the 15-minute
  stale reclaim. `http_total_timeout` (25s) is checked between chunks, and
  `http_max_bytes` (3MB) stops an oversized body being read into a 512MB
  container. Both raise `FetchError`, which is already recorded as a failed
  check, so the surface is skipped and the sweep continues.
- **A timeout on LLM calls** (90s, against the SDK's 600s default), so a
  provider that goes quiet cannot hold a worker slot for ten minutes.
- **A hard sweep boundary** in the reconciler — see `QUEUE_AND_WORKER.md`.

The UI dropped the `finished/total` counter with this: the button reads
"Checking..." while a sweep is in flight and reports the result when it lands.

## Known operational note

This is a Windows dev setup; killing a `uvicorn --reload` process with
`Stop-Process -Force` frequently leaves the port's listening socket in a
lingering state even though the process is gone (`Get-NetTCPConnection` still shows
`Listen` after the PID disappears). The reliable fix is moving to a fresh port and
updating `frontend/.env.local` to match, not fighting the stale socket.
