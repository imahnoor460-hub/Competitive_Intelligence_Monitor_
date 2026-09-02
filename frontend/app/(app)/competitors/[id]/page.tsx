"use client";

import { useEffect, useState, useCallback, FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { apiFetch, ApiError } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { useCheckJobs } from "@/lib/check-jobs-context";
import { useCompetitorDiscoveryJobs } from "@/lib/competitor-discovery-jobs-context";
import { useSiteSummaryJobs } from "@/lib/site-summary-jobs-context";
import {
  CategoryPriceStats,
  ChangeLog,
  Competitor,
  CompetitorDiscoveryJob,
  ComparisonResponse,
  SiteSummary,
  Surface,
  SurfaceType,
} from "@/lib/types";
import { surfaceDisplayName } from "@/lib/surface-name";
import { SURFACE_TYPE_STYLES, HeadingDot, ChangeCard } from "@/components/changelog/SurfaceChangeCards";
import DonutChart from "@/components/charts/DonutChart";
import DualTrendChart from "@/components/charts/DualTrendChart";

const SURFACE_TYPES: SurfaceType[] = ["pricing", "product", "changelog", "blog", "jobs", "other"];

// Cycled by row index (not by surface_type) so the Pages list reads as
// colorful even when most pages are the generic "other" type — e.g. an
// e-commerce competitor's category pages (Sale, Unstitched, Ready to
// Wear, ...) rarely match a known type, so tying color to type alone
// would leave the whole list the same dull grey.
const ROW_COLORS = ["var(--accent)", "var(--blue)", "var(--violet)", "var(--teal)", "var(--red)"];

// Cycled in fixed order across category pills — kept distinct from --accent
// (already owns "current offers," the actionable signal on this card) and
// from --red (reserved for destructive/error states elsewhere in the app).
const CATEGORY_HUES = ["#4D9FFF", "#9B7BFF", "#20C997"];

const CLASS_COLORS: Record<string, string> = {
  pricing_move: "#F5A524",
  new_feature: "#4D9FFF",
  positioning_shift: "#9B7BFF",
  hiring_signal: "#20C997",
  promotion: "#F0445E",
  other: "#8B98A8",
};
const CLASS_LABELS: Record<string, string> = {
  pricing_move: "Pricing move",
  new_feature: "New feature",
  positioning_shift: "Positioning",
  hiring_signal: "Hiring signal",
  promotion: "Promotion",
  other: "Other",
};

function classificationDonutData(counts: Record<string, number>) {
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([label, count]) => ({
      label: CLASS_LABELS[label] ?? label,
      count,
      color: CLASS_COLORS[label] ?? CLASS_COLORS.other,
    }));
}

function formatPrice(n: number, currency: string | null) {
  const rounded = Math.round(n).toLocaleString();
  return currency ? `${currency} ${rounded}` : rounded;
}

const inputClass =
  "w-full rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-xs text-[var(--text-secondary)]";
const labelClass = "font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dim)]";

export default function CompetitorDetailPage() {
  const params = useParams();
  const router = useRouter();
  const competitorId = Number(params.id);
  const { workspaceId, workspace, ready: contextReady, canEdit } = useWorkspaceContext();
  // Check state lives in the provider, not here: with a queue configured a
  // check outlives this page, and a reload has to be able to pick it back up.
  const { startSurfaceCheck, surfaceCheckState, completedCount } = useCheckJobs();
  const {
    startSiteSummaryJob,
    runningFor: summaryRunningFor,
    completedCount: summaryCompletedCount,
  } = useSiteSummaryJobs();
  const {
    trackDiscoveryJob,
    completedCount: discoveryCompletedCount,
  } = useCompetitorDiscoveryJobs();

  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const [data, setData] = useState<ComparisonResponse | null>(null);
  const [surfaces, setSurfaces] = useState<Surface[]>([]);
  const [changeLogs, setChangeLogs] = useState<ChangeLog[]>([]);
  const [otherCompetitors, setOtherCompetitors] = useState<Competitor[]>([]);
  const [compareTo, setCompareTo] = useState<string>("");
  const [siteSummary, setSiteSummary] = useState<SiteSummary | null>(null);
  // Owned by the provider rather than local state: the job outlives this
  // page, so a reload has to be able to re-attach and still show a spinner.
  const refreshingSummary = summaryRunningFor.includes(Number(competitorId));
  const [siteSummaryOpen, setSiteSummaryOpen] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [priceCache, setPriceCache] = useState<Record<string, CategoryPriceStats>>({});
  const [priceLoadingFor, setPriceLoadingFor] = useState<string | null>(null);
  const [priceError, setPriceError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [surfaceForm, setSurfaceForm] = useState({
    surface_type: "pricing" as SurfaceType,
    name: "",
    url: "",
    check_frequency: "daily",
  });
  const [discovering, setDiscovering] = useState(false);
  const [discoverMessage, setDiscoverMessage] = useState<string | null>(null);
  const [pagesOpen, setPagesOpen] = useState(false);

  const load = useCallback(
    async (wsId: number, compareToId?: string) => {
      try {
        const query = compareToId ? `?compare_to=${compareToId}` : "";
        const [comparison, comps, surfs, logs, summary] = await Promise.all([
          apiFetch(`/workspaces/${wsId}/competitors/${competitorId}/comparison${query}`),
          apiFetch(`/workspaces/${wsId}/competitors/`),
          apiFetch(`/workspaces/${wsId}/competitors/${competitorId}/surfaces/`),
          apiFetch(`/workspaces/${wsId}/change-logs/`),
          apiFetch(`/workspaces/${wsId}/competitors/${competitorId}/site-summary/`).catch(() => null),
        ]);
        setData(comparison);
        setOtherCompetitors(comps.filter((c: Competitor) => c.id !== competitorId));
        setSurfaces(surfs);
        setSiteSummary(summary);
        const competitorLogs: ChangeLog[] = logs
          .filter((l: ChangeLog) => l.competitor_id === competitorId)
          .sort(
            (a: ChangeLog, b: ChangeLog) =>
              new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
          );
        setChangeLogs(competitorLogs);
      } catch (err) {
        if (err instanceof ApiError) setError(err.message);
      } finally {
        setLoading(false);
      }
    },
    [competitorId]
  );

  useEffect(() => {
    if (!workspaceId) return;
    void (async () => {
      await load(workspaceId);
    })();
  }, [workspaceId, load]);

  // A queued check finishes in a worker, long after the click. Refetch when
  // one settles so the change it found actually appears, rather than leaving
  // the page stale until the user reloads it themselves.
  useEffect(() => {
    if (completedCount === 0 || !workspaceId) return;
    void (async () => {
      await load(workspaceId);
    })();
  }, [completedCount, workspaceId, load]);

  // Same for the two jobs this page starts. Both finish out of band — in a
  // worker, or in a background task after the response — so the page has to
  // refetch on settle rather than on the click that started them.
  useEffect(() => {
    if (summaryCompletedCount === 0 || !workspaceId) return;
    void (async () => {
      await load(workspaceId);
    })();
  }, [summaryCompletedCount, workspaceId, load]);

  useEffect(() => {
    if (discoveryCompletedCount === 0 || !workspaceId) return;
    void (async () => {
      await load(workspaceId);
    })();
  }, [discoveryCompletedCount, workspaceId, load]);

  async function handleCompareToChange(value: string) {
    setCompareTo(value);
    if (!workspaceId) return;
    setLoading(true);
    await load(workspaceId, value || undefined);
  }

  async function handleRefreshSiteSummary() {
    if (!workspaceId) return;
    setSummaryError(null);
    // Queued now — the refresh reads one page per active surface, which is
    // too long to hold a request open. The provider polls the job and this
    // page refetches when summaryCompletedCount bumps.
    await startSiteSummaryJob(Number(competitorId));
  }

  async function handleCategoryClick(category: string) {
    setSelectedCategory(category);
    setPriceError(null);
    if (priceCache[category] || !workspaceId) return;

    setPriceLoadingFor(category);
    try {
      const result = await apiFetch(
        `/workspaces/${workspaceId}/competitors/${competitorId}/category-price/`,
        { method: "POST", body: JSON.stringify({ category }) }
      );
      setPriceCache((prev) => ({ ...prev, [category]: result }));
    } catch (err) {
      setPriceError(err instanceof ApiError ? err.message : "Failed to look up pricing");
    } finally {
      setPriceLoadingFor(null);
    }
  }

  async function handleDiscoverSurfaces() {
    if (!workspaceId) return;
    setDiscovering(true);
    setDiscoverMessage(null);
    setError(null);
    try {
      // Queued now, and it reuses CompetitorDiscoveryJob — so the poller and
      // the "found N pages" toast that already exist for the create path
      // cover this too. 202 for a new job, 200 when one was already running.
      const job: CompetitorDiscoveryJob = await apiFetch(
        `/workspaces/${workspaceId}/competitors/${competitorId}/surfaces/discover`,
        { method: "POST" }
      );
      trackDiscoveryJob(Number(competitorId), job.id);
      setDiscoverMessage("Scanning the site — pages will appear here as they are found.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to discover pages");
    } finally {
      setDiscovering(false);
    }
  }

  async function handleAddSurface(e: FormEvent) {
    e.preventDefault();
    if (!workspaceId || !surfaceForm.url) return;

    try {
      await apiFetch(`/workspaces/${workspaceId}/competitors/${competitorId}/surfaces/`, {
        method: "POST",
        body: JSON.stringify({ ...surfaceForm, name: surfaceForm.name.trim() || null }),
      });
      setSurfaceForm({ surface_type: "pricing", name: "", url: "", check_frequency: "daily" });
      await load(workspaceId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add surface");
    }
  }

  async function handleDeleteCompetitor() {
    if (!workspaceId || !data) return;
    if (
      !window.confirm(
        `Delete ${data.competitor.name}? This permanently removes its surfaces, change history, battlecard, and site summary. This cannot be undone.`
      )
    ) {
      return;
    }

    setDeleting(true);
    setError(null);
    try {
      await apiFetch(`/workspaces/${workspaceId}/competitors/${competitorId}`, {
        method: "DELETE",
      });
      router.push("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete competitor");
      setDeleting(false);
    }
  }

  async function handleDeleteSurface(surfaceId: number) {
    if (!workspaceId) return;
    try {
      await apiFetch(`/workspaces/${workspaceId}/competitors/${competitorId}/surfaces/${surfaceId}`, {
        method: "DELETE",
      });
      await load(workspaceId);
    } catch (err) {
      if (err instanceof ApiError) setError(err.message);
    }
  }

  async function handleCheckSurface(surfaceId: number) {
    await startSurfaceCheck(competitorId, surfaceId);
  }

  if (!contextReady || loading) return null;

  if (!data) {
    return (
      <div className="flex flex-col gap-3 px-[34px] py-[30px]" style={{ maxWidth: 900 }}>
        <p className="text-sm text-[var(--text-faint)]">{error ?? "Competitor not found."}</p>
        <Link href="/" className="text-sm font-medium text-[var(--accent)] hover:text-[var(--accent-hover)]">
          Back to dashboard
        </Link>
      </div>
    );
  }

  const { competitor, battlecard, change_summary, benchmark } = data;
  const benchmarkIsOwnSite = benchmark?.competitor.is_own_site === true;

  return (
    <div className="flex flex-col gap-[18px] px-[34px] py-[30px] pb-[44px]" style={{ maxWidth: 1100 }}>
      <div className="flex items-end justify-between gap-6">
        <div className="flex flex-col gap-[7px]">
          <h1 className="m-0 text-[26px] font-semibold tracking-[-0.025em]">{competitor.name}</h1>
          {workspace && (
            <p className="m-0 text-[13.5px] text-[var(--text-muted)]">{workspace.name}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {canEdit && (
            <button
              onClick={handleDeleteCompetitor}
              disabled={deleting}
              className="h-8 rounded-lg border border-[var(--red)]/40 px-3 text-xs font-medium text-[var(--red)] disabled:opacity-50"
            >
              {deleting ? "Deleting…" : "Delete competitor"}
            </button>
          )}
          <Link
            href="/"
            className="h-8 rounded-lg border border-[var(--border-input)] bg-[var(--bg-card)] px-3 text-xs font-medium leading-8 text-[var(--text-secondary)] hover:border-[var(--border-hover)] hover:text-[var(--text-primary)]"
          >
            Back to dashboard
          </Link>
        </div>
      </div>

      {error && (
        <p className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</p>
      )}

      <div className="grid grid-cols-2 gap-[14px] lg:grid-cols-4">
        <StatTile label="Changes detected" value={change_summary.total_changes} />
        <StatTile label="Significant changes" value={change_summary.material_count} />
        <StatTile
          label="Avg importance"
          value={
            change_summary.avg_materiality !== null
              ? (change_summary.avg_materiality / 100).toFixed(2)
              : "—"
          }
        />
        <StatTile
          label="Last change"
          value={
            change_summary.last_change_at
              ? new Date(change_summary.last_change_at).toLocaleDateString()
              : "—"
          }
        />
      </div>

      <Card>
        <div
          onClick={() => setSiteSummaryOpen((v) => !v)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setSiteSummaryOpen((v) => !v);
            }
          }}
          className="flex cursor-pointer items-center justify-between gap-4"
        >
          <div className="flex items-center gap-2.5">
            <span
              className="text-[11px] text-[var(--text-faint)] transition-transform"
              style={{ transform: siteSummaryOpen ? "rotate(90deg)" : "rotate(0deg)" }}
            >
              ▸
            </span>
            <div className="flex flex-col gap-[5px]">
              <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">
                What&apos;s on their site
              </h2>
              <p className="m-0 text-[11.5px] text-[var(--text-faint)]">
                {siteSummary?.generated_at
                  ? `Generated ${new Date(siteSummary.generated_at).toLocaleString()}`
                  : "Read directly from their current page content — no diff needed"}
              </p>
            </div>
          </div>
          {canEdit && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                handleRefreshSiteSummary();
              }}
              disabled={refreshingSummary}
              className="h-8 flex-shrink-0 rounded-lg bg-[var(--accent)] px-3 text-xs font-semibold text-[var(--accent-on)] disabled:opacity-50"
            >
              {refreshingSummary ? "Analyzing..." : siteSummary ? "Refresh" : "Analyze site"}
            </button>
          )}
        </div>
        {siteSummaryOpen && summaryError && (
          <p className="m-0 text-[12.5px] text-[var(--red)]">{summaryError}</p>
        )}
        {siteSummaryOpen && (!siteSummary ? (
          <p className="text-sm text-[var(--text-faint)]">
            No site summary yet — click &quot;Analyze site&quot; to extract categories and
            current offers from their most recently captured page content.
          </p>
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <span className="font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dim)]">
                Categories
              </span>
              {siteSummary.categories.length === 0 ? (
                <span className="text-[13px] text-[var(--text-faint)]">None detected</span>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {siteSummary.categories.map((category, i) => {
                    const hue = CATEGORY_HUES[i % CATEGORY_HUES.length];
                    const isSelected = selectedCategory === category;
                    return (
                      <button
                        key={category}
                        onClick={() => handleCategoryClick(category)}
                        className="rounded-md px-2 py-0.5 font-mono text-[10.5px] font-medium transition-opacity hover:opacity-75"
                        style={{
                          background: `${hue}1A`,
                          color: hue,
                          outline: isSelected ? `1px solid ${hue}` : "none",
                        }}
                      >
                        {category}
                      </button>
                    );
                  })}
                </div>
              )}
              {selectedCategory && (
                <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-nested)] px-3 py-2.5">
                  {priceLoadingFor === selectedCategory ? (
                    <p className="m-0 text-[12.5px] text-[var(--text-faint)]">
                      Looking up pricing for &quot;{selectedCategory}&quot;…
                    </p>
                  ) : priceError ? (
                    <p className="m-0 text-[12.5px] text-[var(--red)]">{priceError}</p>
                  ) : priceCache[selectedCategory] ? (
                    priceCache[selectedCategory].prices_found === 0 ? (
                      <p className="m-0 text-[12.5px] text-[var(--text-faint)]">
                        No pricing found for &quot;{selectedCategory}&quot; — couldn&apos;t locate
                        or read a listing page for this category.
                      </p>
                    ) : (
                      <div className="flex flex-col gap-1">
                        <span className="text-[13px] font-medium text-[var(--text-primary)]">
                          {selectedCategory}:{" "}
                          {formatPrice(
                            priceCache[selectedCategory].min_price!,
                            priceCache[selectedCategory].currency
                          )}{" "}
                          –{" "}
                          {formatPrice(
                            priceCache[selectedCategory].max_price!,
                            priceCache[selectedCategory].currency
                          )}{" "}
                          <span className="text-[var(--text-faint)]">
                            (avg{" "}
                            {formatPrice(
                              priceCache[selectedCategory].avg_price!,
                              priceCache[selectedCategory].currency
                            )}
                            )
                          </span>
                        </span>
                        <span className="text-[11px] text-[var(--text-faint)]">
                          Based on {priceCache[selectedCategory].prices_found} product
                          {priceCache[selectedCategory].prices_found === 1 ? "" : "s"} visible on
                          their listing page
                          {priceCache[selectedCategory].listing_url && (
                            <>
                              {" · "}
                              <a
                                href={priceCache[selectedCategory].listing_url!}
                                target="_blank"
                                rel="noreferrer"
                                className="text-[var(--accent)] hover:underline"
                              >
                                View listing ↗
                              </a>
                            </>
                          )}
                        </span>
                      </div>
                    )
                  ) : null}
                </div>
              )}
            </div>
            <div className="flex flex-col gap-2">
              <span className="font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dim)]">
                Current offers
              </span>
              {siteSummary.current_offers.length === 0 ? (
                <span className="text-[13px] text-[var(--text-faint)]">None detected</span>
              ) : (
                <ul className="m-0 flex flex-col gap-1.5 pl-4">
                  {siteSummary.current_offers.map((offer) => (
                    <li key={offer} className="text-[13px] font-medium text-[var(--accent)]">
                      {offer}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ))}
      </Card>

      <div className="grid grid-cols-1 gap-[14px] lg:grid-cols-[1.45fr_1fr]">
        <Card>
          <div className="flex flex-col gap-[5px]">
            <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">
              Detection vs. materiality
            </h2>
            <p className="m-0 text-[11.5px] text-[var(--text-faint)]">Last 30 days</p>
          </div>
          <DualTrendChart data={change_summary.trend} />
        </Card>

        <Card>
          <div className="flex flex-col gap-[5px]">
            <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">Classification</h2>
            <p className="m-0 text-[11.5px] text-[var(--text-faint)]">All time</p>
          </div>
          <DonutChart
            data={classificationDonutData(change_summary.classification_counts)}
            centerValue={change_summary.total_changes}
            centerLabel="changes"
          />
        </Card>
      </div>

      <Card>
        <div className="flex items-center justify-between gap-4">
          <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">
            {competitor.name}
            {benchmark ? ` vs. ${benchmarkIsOwnSite ? "your website" : benchmark.competitor.name}` : ""}
          </h2>
          {!benchmarkIsOwnSite && (
            <div className="flex items-center gap-2">
              <span className="font-mono text-[10px] uppercase tracking-[.1em] text-[var(--text-faint)]">
                No website set — compare against
              </span>
              <select
                value={compareTo}
                onChange={(e) => handleCompareToChange(e.target.value)}
                className="h-7 rounded-md border border-[var(--border-input)] bg-[var(--bg-input)] px-2 text-xs text-[var(--text-secondary)]"
              >
                <option value="">Select a competitor…</option>
                {otherCompetitors.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
        {!benchmark ? (
          <p className="text-sm text-[var(--text-faint)]">
            Set your website on the dashboard, or pick another competitor above, to see a
            side-by-side comparison.
          </p>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-[14px] font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dimmer)]">
              <span></span>
              <span className="text-right">{competitor.name}</span>
              <span className="text-right text-[var(--accent)]">
                {benchmarkIsOwnSite ? "Your website" : benchmark.competitor.name}
              </span>
            </div>
            <ComparisonRow
              label="Changes detected"
              left={change_summary.total_changes}
              right={benchmark.change_summary.total_changes}
            />
            <ComparisonRow
              label="Significant changes"
              left={change_summary.material_count}
              right={benchmark.change_summary.material_count}
            />
            <ComparisonRow
              label="Avg importance"
              left={
                change_summary.avg_materiality !== null
                  ? (change_summary.avg_materiality / 100).toFixed(2)
                  : "—"
              }
              right={
                benchmark.change_summary.avg_materiality !== null
                  ? (benchmark.change_summary.avg_materiality / 100).toFixed(2)
                  : "—"
              }
            />
            <ComparisonRow
              label="Last change"
              left={
                change_summary.last_change_at
                  ? new Date(change_summary.last_change_at).toLocaleDateString()
                  : "—"
              }
              right={
                benchmark.change_summary.last_change_at
                  ? new Date(benchmark.change_summary.last_change_at).toLocaleDateString()
                  : "—"
              }
            />
          </>
        )}
      </Card>

      <Card>
        <div className="flex items-center justify-between">
          <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">Battlecard</h2>
          <Link href="/battlecards" className="text-xs font-medium text-[var(--accent)] hover:text-[var(--accent-hover)]">
            Manage →
          </Link>
        </div>
        {battlecard ? (
          <>
            <p className="m-0 font-mono text-[11px] text-[var(--text-faint)]">
              Version {battlecard.version}
            </p>
            <pre className="whitespace-pre-wrap text-[13px] leading-[1.6] text-[var(--text-secondary)]">
              {battlecard.content_markdown || "(empty)"}
            </pre>
          </>
        ) : (
          <p className="text-sm text-[var(--text-faint)]">No battlecard yet for this competitor.</p>
        )}
      </Card>

      <Card>
        <div
          onClick={() => setPagesOpen((v) => !v)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setPagesOpen((v) => !v);
            }
          }}
          className="flex cursor-pointer items-center justify-between gap-3"
        >
          <h2 className="m-0 flex items-center gap-2 text-[14.5px] font-semibold tracking-[-0.01em]">
            <span
              className="text-[11px] text-[var(--text-faint)] transition-transform"
              style={{ transform: pagesOpen ? "rotate(90deg)" : "rotate(0deg)" }}
            >
              ▸
            </span>
            <HeadingDot />
            Pages
            <span className="font-mono text-[11px] font-normal text-[var(--text-faint)]">
              ({surfaces.length})
            </span>
          </h2>
          {canEdit && surfaces.length > 0 && (
            <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
              {discoverMessage && (
                <span className="text-[11.5px] text-[var(--text-muted)]">{discoverMessage}</span>
              )}
              <button
                onClick={handleDiscoverSurfaces}
                disabled={discovering}
                className="h-7 rounded-md border border-[var(--border-input)] px-2.5 text-[11.5px] font-medium text-[var(--text-secondary)] hover:border-[var(--border-hover)] disabled:opacity-50"
              >
                {discovering ? "Scanning site..." : "Discover more pages"}
              </button>
            </div>
          )}
        </div>
        {pagesOpen && (
        <p className="m-0 -mt-2 text-[12px] text-[var(--text-faint)]">
          Every page being monitored on their site — named the same way it is in their own nav
          (Sale, Unstitched, Ready to Wear, ...). Click one to open its change log.
        </p>
        )}
        {pagesOpen && (surfaces.length === 0 ? (
          <p className="text-sm text-[var(--text-faint)]">No surfaces yet.</p>
        ) : (
          <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)]">
            {surfaces.map((s, i) => {
              const status = surfaceCheckState(s.id);
              const typeStyle = SURFACE_TYPE_STYLES[s.surface_type];
              const rowColor = ROW_COLORS[i % ROW_COLORS.length];
              return (
                <Link
                  key={s.id}
                  href={`/competitors/${competitorId}/surfaces/${s.id}`}
                  className="flex items-center justify-between gap-3 px-4 py-3 transition-colors"
                  style={{
                    borderLeft: `3px solid ${rowColor}`,
                    borderBottom: i < surfaces.length - 1 ? "1px solid var(--border-subtler)" : undefined,
                    background: `color-mix(in srgb, ${rowColor} 6%, var(--bg-nested))`,
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = `color-mix(in srgb, ${rowColor} 14%, var(--bg-nested))`;
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = `color-mix(in srgb, ${rowColor} 6%, var(--bg-nested))`;
                  }}
                >
                  <div className="min-w-0">
                    <p className="m-0 flex items-center gap-2 text-[13px] font-medium">
                      <span
                        className="flex-shrink-0 rounded-md px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide"
                        style={{
                          background: `color-mix(in srgb, ${typeStyle.color} 18%, transparent)`,
                          color: typeStyle.color,
                        }}
                      >
                        {s.surface_type}
                      </span>
                      <span className="truncate font-semibold" style={{ color: rowColor }}>
                        {surfaceDisplayName(s)}
                      </span>
                      <span className="truncate font-mono text-[11px] text-[var(--text-dim)]">
                        {s.url.replace(/^https?:\/\//, "")}
                      </span>
                    </p>
                    <p className="m-0 mt-1 font-mono text-[11px] text-[var(--text-faint)]">
                      Checked {s.check_frequency}
                      {s.last_checked_at && ` · last checked ${new Date(s.last_checked_at).toLocaleString()}`}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {status.state === "done" && (
                      <span className="text-[11.5px] text-[var(--text-muted)]">{status.message}</span>
                    )}
                    {status.state === "error" && (
                      <span className="text-[11.5px] text-[var(--red)]">{status.message}</span>
                    )}
                    {canEdit && (
                      <div className="flex items-center gap-2" onClick={(e) => e.preventDefault()}>
                        <button
                          onClick={() => handleCheckSurface(s.id)}
                          disabled={status.state === "checking"}
                          className="h-7 rounded-md border border-[var(--border-input)] px-2.5 text-[11.5px] font-medium text-[var(--text-secondary)] hover:border-[var(--border-hover)] disabled:opacity-50"
                        >
                          {status.state === "checking" ? "Checking..." : "Check now"}
                        </button>
                        <button
                          onClick={() => handleDeleteSurface(s.id)}
                          className="h-7 rounded-md border border-[var(--red)]/40 px-2.5 text-[11.5px] font-medium text-[var(--red)]"
                        >
                          Delete
                        </button>
                      </div>
                    )}
                    <span style={{ color: rowColor }}>›</span>
                  </div>
                </Link>
              );
            })}
          </div>
        ))}

        {pagesOpen && canEdit && (
          <form
            onSubmit={handleAddSurface}
            className="flex flex-col gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-nested)] px-4 py-3.5 sm:flex-row sm:items-end"
          >
            <div className="flex flex-col gap-1.5">
              <label className={labelClass}>Type</label>
              <select
                value={surfaceForm.surface_type}
                onChange={(e) =>
                  setSurfaceForm((prev) => ({ ...prev, surface_type: e.target.value as SurfaceType }))
                }
                className={inputClass}
              >
                {SURFACE_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <label className={labelClass}>Name (optional)</label>
              <input
                value={surfaceForm.name}
                onChange={(e) => setSurfaceForm((prev) => ({ ...prev, name: e.target.value }))}
                placeholder="Unstitched"
                className={inputClass}
              />
            </div>
            <div className="flex-1 flex-col gap-1.5">
              <label className={labelClass}>URL</label>
              <input
                required
                value={surfaceForm.url}
                onChange={(e) => setSurfaceForm((prev) => ({ ...prev, url: e.target.value }))}
                placeholder="https://acme.com/pricing"
                className={inputClass}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className={labelClass}>Frequency</label>
              <select
                value={surfaceForm.check_frequency}
                onChange={(e) =>
                  setSurfaceForm((prev) => ({ ...prev, check_frequency: e.target.value }))
                }
                className={inputClass}
              >
                <option value="hourly">Hourly</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
              </select>
            </div>
            <button
              type="submit"
              className="h-8 w-fit rounded-lg bg-[var(--accent)] px-3 text-xs font-semibold text-[var(--accent-on)]"
            >
              Add surface
            </button>
          </form>
        )}
      </Card>

      <Card>
        <h2 className="m-0 flex items-center gap-2 text-[14.5px] font-semibold tracking-[-0.01em]">
          <HeadingDot />
          Change log
        </h2>
        {changeLogs.length === 0 ? (
          <p className="text-sm text-[var(--text-faint)]">
            No changes detected yet for this competitor — click a page above to see its current
            snapshot.
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            {changeLogs.map((log) => (
              <ChangeCard
                key={log.id}
                log={log}
                surface={surfaces.find((s) => s.id === log.surface_id)}
                competitorName={competitor?.name}
              />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-[16px] rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-[22px] py-5">
      {children}
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="flex flex-col gap-3 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-5 py-[18px]">
      <span className="font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-faint)]">
        {label}
      </span>
      <span className="text-[30px] font-semibold tracking-[-0.03em]">{value}</span>
    </div>
  );
}

function ComparisonRow({
  label,
  left,
  right,
}: {
  label: string;
  left: string | number;
  right: string | number;
}) {
  return (
    <div className="grid grid-cols-3 items-center gap-[14px] border-t border-[var(--border-subtler)] py-2.5">
      <span className="text-[12.5px] text-[var(--text-secondary)]">{label}</span>
      <span className="text-right font-mono text-[13px] font-medium">{left}</span>
      <span className="text-right font-mono text-[13px] font-medium text-[var(--accent)]">{right}</span>
    </div>
  );
}
