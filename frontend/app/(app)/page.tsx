"use client";

import { useEffect, useState, useCallback, useMemo, FormEvent } from "react";
import Link from "next/link";
import { apiFetch, ApiError } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { useCompetitorDiscoveryJobs } from "@/lib/competitor-discovery-jobs-context";
import {
  ApprovalItem,
  Briefing,
  ChangeLog,
  Competitor,
  CompetitorSummary,
  OwnSite,
  Surface,
} from "@/lib/types";
import ClassificationBadge, { classificationColor } from "@/components/ui/ClassificationBadge";
import DonutChart from "@/components/charts/DonutChart";
import DualTrendChart, { DualTrendPoint } from "@/components/charts/DualTrendChart";

const TREND_DAYS = 14;
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

interface CheckRun {
  status: "running" | "success" | "failed";
}

// Own site has no user-facing "name" field (backend stores it as a hidden
// competitor named "Your website") — derive something readable from the
// URL instead so the prominent badge shows the actual site, not a label.
function ownSiteDisplayName(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

interface ComparisonRow {
  id: number;
  name: string;
  isOwnSite: boolean;
  changesDetected: number;
  materialCount: number;
  avgMateriality: number | null;
  dominantClassification: string | null;
  lastChangeAt: string | null;
}

function summarizeLogs(logs: ChangeLog[]): Omit<ComparisonRow, "id" | "name" | "isOwnSite"> {
  const scored = logs.filter((l) => l.materiality_score !== null);
  const material = scored.filter((l) => (l.materiality_score ?? 0) >= 50);
  const classCounts: Record<string, number> = {};
  for (const l of logs) {
    if (l.classification) classCounts[l.classification] = (classCounts[l.classification] ?? 0) + 1;
  }
  const dominant = Object.entries(classCounts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
  const lastChangeAt =
    logs.length > 0
      ? logs.reduce((latest, l) => (l.created_at > latest ? l.created_at : latest), logs[0].created_at)
      : null;

  return {
    changesDetected: logs.length,
    materialCount: material.length,
    avgMateriality:
      scored.length > 0
        ? scored.reduce((sum, l) => sum + (l.materiality_score ?? 0), 0) / scored.length
        : null,
    dominantClassification: dominant,
    lastChangeAt,
  };
}

function dayKey(iso: string) {
  return iso.slice(0, 10);
}

export default function DashboardPage() {
  const { workspaceId, ready: contextReady, canEdit } = useWorkspaceContext();
  const [nowMs] = useState(() => Date.now());

  const { trackDiscoveryJob, discoveringCount, completedCount } =
    useCompetitorDiscoveryJobs();
  const [competitors, setCompetitors] = useState<Competitor[]>([]);
  const [changeLogs, setChangeLogs] = useState<ChangeLog[]>([]);
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [briefings, setBriefings] = useState<Briefing[]>([]);
  const [checkRuns, setCheckRuns] = useState<CheckRun[]>([]);
  const [ownSite, setOwnSite] = useState<OwnSite | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [newCompetitorName, setNewCompetitorName] = useState("");
  const [newCompetitorUrl, setNewCompetitorUrl] = useState("");
  const [creatingCompetitor, setCreatingCompetitor] = useState(false);
  const [competitorAddedNotice, setCompetitorAddedNotice] = useState<string | null>(null);
  const [ownSiteUrl, setOwnSiteUrl] = useState("");
  const [savingOwnSite, setSavingOwnSite] = useState(false);
  const [ownSiteSummary, setOwnSiteSummary] = useState<CompetitorSummary | null>(null);

  const load = useCallback(async (wsId: number) => {
    try {
      const [comps, logs, allApprovals, briefingList, ownSiteData] = await Promise.all([
        apiFetch(`/workspaces/${wsId}/competitors/`),
        apiFetch(`/workspaces/${wsId}/change-logs/`),
        apiFetch(`/workspaces/${wsId}/approvals/`),
        apiFetch(`/workspaces/${wsId}/briefings/`),
        apiFetch(`/workspaces/${wsId}/own-site/`).catch(() => null),
      ]);
      setCompetitors(comps);
      // The change-logs endpoint isn't filtered by is_own_site — exclude
      // your own site's changes here so it never silently inflates the
      // dashboard's competitor-facing aggregate stats (it gets its own row
      // in the comparison table instead).
      const ownSiteCompetitorId = ownSiteData?.competitor_id ?? null;
      setChangeLogs(
        ownSiteCompetitorId === null
          ? logs
          : logs.filter((l: ChangeLog) => l.competitor_id !== ownSiteCompetitorId)
      );
      setApprovals(allApprovals);
      setBriefings(briefingList);
      setOwnSite(ownSiteData);

      if (ownSiteCompetitorId !== null) {
        try {
          const comparison = await apiFetch(
            `/workspaces/${wsId}/competitors/${ownSiteCompetitorId}/comparison`
          );
          setOwnSiteSummary(comparison.change_summary);
        } catch {
          setOwnSiteSummary(null);
        }
      } else {
        setOwnSiteSummary(null);
      }

      const perCompetitorSurfaces = await Promise.all(
        comps.map(async (c: Competitor) => {
          const surfaces: Surface[] = await apiFetch(
            `/workspaces/${wsId}/competitors/${c.id}/surfaces/`
          );
          return { competitor: c, surfaces };
        })
      );

      const runResults = await Promise.all(
        perCompetitorSurfaces.flatMap(({ competitor, surfaces }) =>
          surfaces.map((s: Surface) =>
            apiFetch(
              `/workspaces/${wsId}/competitors/${competitor.id}/surfaces/${s.id}/check-runs`
            ).catch(() => [])
          )
        )
      );
      setCheckRuns(runResults.flat());
    } catch (err) {
      if (err instanceof ApiError) setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!workspaceId) return;
    void (async () => {
      await load(workspaceId);
    })();
  }, [workspaceId, load]);

  // Surfaces only exist once the background discovery job has run, so reload
  // when one resolves — completedCount bumps on every resolve.
  useEffect(() => {
    if (!workspaceId || completedCount === 0) return;
    void load(workspaceId);
  }, [completedCount, workspaceId, load]);

  async function handleAddCompetitor(e: FormEvent) {
    e.preventDefault();
    if (!workspaceId || !newCompetitorName.trim()) return;

    setCreatingCompetitor(true);
    setError(null);
    setCompetitorAddedNotice(null);
    try {
      const created: Competitor = await apiFetch(
        `/workspaces/${workspaceId}/competitors/`,
        {
          method: "POST",
          body: JSON.stringify({
            name: newCompetitorName.trim(),
            website_url: newCompetitorUrl.trim() || undefined,
          }),
        }
      );
      // The add returns as soon as the competitor row exists. Page discovery
      // drives a real browser and now runs as a background job, so report the
      // count from the job when it resolves rather than blocking on it here.
      if (created.discovery_job_id) {
        trackDiscoveryJob(created.id, created.discovery_job_id);
        setCompetitorAddedNotice(
          `Added ${created.name} — discovering its pages in the background...`
        );
      } else {
        setCompetitorAddedNotice(null);
      }
      setNewCompetitorName("");
      setNewCompetitorUrl("");
      await load(workspaceId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add competitor");
    } finally {
      setCreatingCompetitor(false);
    }
  }

  async function handleSaveOwnSite(e: FormEvent) {
    e.preventDefault();
    if (!workspaceId || !ownSiteUrl.trim()) return;

    setSavingOwnSite(true);
    setError(null);
    try {
      const result = await apiFetch(`/workspaces/${workspaceId}/own-site/`, {
        method: "PUT",
        body: JSON.stringify({ url: ownSiteUrl.trim() }),
      });
      setOwnSite(result);
      setOwnSiteUrl("");
      await load(workspaceId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save your website");
    } finally {
      setSavingOwnSite(false);
    }
  }

  const changesLast30d = useMemo(() => {
    const since = nowMs - 30 * 24 * 60 * 60 * 1000;
    return changeLogs.filter((l) => new Date(l.created_at).getTime() >= since);
  }, [changeLogs, nowMs]);

  const materialCount = useMemo(
    () => changeLogs.filter((l) => l.materiality_score !== null && l.materiality_score >= 50).length,
    [changeLogs]
  );

  const crawlSuccessRate = useMemo(() => {
    if (checkRuns.length === 0) return null;
    const finished = checkRuns.filter((r) => r.status !== "running");
    if (finished.length === 0) return null;
    const successes = finished.filter((r) => r.status === "success").length;
    return (successes / finished.length) * 100;
  }, [checkRuns]);

  const briefingApprovalRate = useMemo(() => {
    const decided = briefings.filter(
      (b) => b.status === "approved" || b.status === "rejected" || b.status === "delivered"
    );
    if (decided.length === 0) return null;
    const approved = decided.filter((b) => b.status !== "rejected").length;
    return (approved / decided.length) * 100;
  }, [briefings]);

  const materialityPrecision = useMemo(() => {
    const scored = changeLogs.filter((l) => l.materiality_score !== null);
    if (scored.length === 0) return null;
    return (materialCount / scored.length) * 100;
  }, [changeLogs, materialCount]);

  const trendData: DualTrendPoint[] = useMemo(() => {
    const buckets: Record<string, { detected: number; material: number }> = {};
    const today = new Date(nowMs);
    for (let i = TREND_DAYS - 1; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      buckets[dayKey(d.toISOString())] = { detected: 0, material: 0 };
    }
    for (const log of changeLogs) {
      const key = dayKey(log.created_at);
      if (key in buckets) {
        buckets[key].detected += 1;
        if (log.materiality_score !== null && log.materiality_score >= 50) {
          buckets[key].material += 1;
        }
      }
    }
    return Object.entries(buckets).map(([date, v]) => ({ date, ...v }));
  }, [changeLogs, nowMs]);

  const classificationData = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const log of changeLogs) {
      if (!log.classification) continue;
      counts[log.classification] = (counts[log.classification] ?? 0) + 1;
    }
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([label, count]) => ({
        label: CLASS_LABELS[label] ?? label,
        count,
        color: CLASS_COLORS[label] ?? CLASS_COLORS.other,
      }));
  }, [changeLogs]);

  const comparisonTable: ComparisonRow[] = useMemo(() => {
    const rows: ComparisonRow[] = competitors.map((c) => ({
      id: c.id,
      name: c.name,
      isOwnSite: false,
      ...summarizeLogs(changeLogs.filter((l) => l.competitor_id === c.id)),
    }));

    rows.sort((a, b) => b.changesDetected - a.changesDetected);

    if (ownSite && ownSiteSummary) {
      rows.unshift({
        id: ownSite.competitor_id,
        name: "Your website",
        isOwnSite: true,
        changesDetected: ownSiteSummary.total_changes,
        materialCount: ownSiteSummary.material_count,
        avgMateriality: ownSiteSummary.avg_materiality,
        dominantClassification:
          Object.entries(ownSiteSummary.classification_counts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null,
        lastChangeAt: ownSiteSummary.last_change_at,
      });
    }

    return rows;
  }, [changeLogs, competitors, ownSite, ownSiteSummary]);

  const topMoves = useMemo(
    () =>
      [...changeLogs]
        .filter((l) => l.materiality_score !== null)
        .sort((a, b) => (b.materiality_score ?? 0) - (a.materiality_score ?? 0))
        .slice(0, 6),
    [changeLogs]
  );

  function competitorName(id: number) {
    return competitors.find((c) => c.id === id)?.name ?? `#${id}`;
  }

  function handleExportCsv() {
    const header = "competitor,classification,materiality_score,created_at,diff\n";
    const rows = changeLogs
      .map((l) =>
        [
          competitorName(l.competitor_id),
          l.classification ?? "",
          l.materiality_score ?? "",
          l.created_at,
          (l.diff ?? "").replace(/"/g, '""').replace(/\n/g, " "),
        ]
          .map((v) => `"${v}"`)
          .join(",")
      )
      .join("\n");
    const blob = new Blob([header + rows], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "change-log-export.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  if (!contextReady || loading) return null;

  return (
    <div className="flex flex-col gap-[18px] px-[34px] py-[30px] pb-[44px]" style={{ maxWidth: 1320 }}>
      <div className="flex items-end justify-between gap-6">
        <div className="flex flex-col gap-[7px]">
          <h1 className="m-0 text-[26px] font-semibold tracking-[-0.025em]">Intelligence overview</h1>
          <p className="m-0 max-w-[600px] text-[13.5px] text-[var(--text-muted)]">
            {competitors.length} competitor{competitors.length === 1 ? "" : "s"} watched. The
            agent scores materiality, classifies every change, and holds each briefing at the
            approval gate.
          </p>
        </div>
        <div className="flex gap-[7px]">
          <button
            onClick={handleExportCsv}
            className="h-8 rounded-lg border border-[var(--border-input)] bg-[var(--bg-card)] px-3 text-xs font-medium text-[var(--text-secondary)] hover:border-[var(--border-hover)] hover:text-[var(--text-primary)]"
          >
            Export CSV
          </button>
        </div>
      </div>

      {error && (
        <p className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</p>
      )}

      <Card>
        <div className="flex items-center justify-between">
          <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">Competitors</h2>
          <span className="font-mono text-[11px] text-[var(--text-faint)]">
            Add a homepage URL — pricing, blog, changelog and jobs pages are found automatically
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {ownSite && (
            <Link
              href={`/competitors/${ownSite.competitor_id}`}
              className="rounded-lg border border-[var(--accent)] bg-[var(--accent-wash)] px-3 py-1.5 text-[14px] font-bold tracking-tight text-[var(--accent)] hover:brightness-110"
            >
              {ownSiteDisplayName(ownSite.url)}
              <span className="ml-1.5 font-mono text-[9px] font-normal uppercase tracking-[.1em] opacity-70">
                you
              </span>
            </Link>
          )}
          {competitors.length === 0 ? (
            !ownSite && (
              <p className="text-sm text-[var(--text-faint)]">
                No competitors yet — add one below to get started.
              </p>
            )
          ) : (
            competitors.map((c) => (
              <Link
                key={c.id}
                href={`/competitors/${c.id}`}
                className="rounded-lg border border-[var(--border-input)] bg-[var(--bg-nested)] px-3 py-1.5 text-[12.5px] font-medium text-[var(--text-secondary)] hover:border-[var(--border-hover)] hover:text-[var(--text-primary)]"
              >
                {c.name}
              </Link>
            ))
          )}
        </div>
        {canEdit && (
          <form onSubmit={handleAddCompetitor} className="flex items-center gap-2">
            <input
              value={newCompetitorName}
              onChange={(e) => setNewCompetitorName(e.target.value)}
              placeholder="Competitor name"
              className="h-8 w-40 rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 text-xs text-[var(--text-primary)]"
            />
            <input
              value={newCompetitorUrl}
              onChange={(e) => setNewCompetitorUrl(e.target.value)}
              placeholder="https://theircompany.com"
              className="h-8 flex-1 max-w-xs rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 text-xs text-[var(--text-primary)]"
            />
            <button
              type="submit"
              disabled={creatingCompetitor || !newCompetitorName.trim()}
              className="h-8 rounded-lg bg-[var(--accent)] px-3 text-xs font-semibold text-[var(--accent-on)] disabled:opacity-50"
            >
              {creatingCompetitor ? "Adding..." : "Add competitor"}
            </button>
          </form>
        )}
        {discoveringCount > 0 && (
          <p className="text-xs text-[var(--text-muted)]">
            Discovering pages for {discoveringCount} competitor
            {discoveringCount === 1 ? "" : "s"}... you can keep working, this
            finishes on its own.
          </p>
        )}
        {competitorAddedNotice && (
          <p className="text-xs text-[var(--accent)]">{competitorAddedNotice}</p>
        )}

        <div className="flex flex-col gap-2 border-t border-[var(--border-subtle)] pt-4">
          <span className="font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dim)]">
            Your website — the benchmark every competitor is measured against
          </span>
          {canEdit ? (
            <form onSubmit={handleSaveOwnSite} className="flex items-center gap-2">
              <input
                value={ownSiteUrl}
                onChange={(e) => setOwnSiteUrl(e.target.value)}
                placeholder={ownSite ? ownSite.url : "https://yourcompany.com"}
                className="h-8 flex-1 max-w-xs rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 text-xs text-[var(--text-primary)]"
              />
              <button
                type="submit"
                disabled={savingOwnSite || !ownSiteUrl.trim()}
                className="h-8 rounded-lg bg-[var(--accent)] px-3 text-xs font-semibold text-[var(--accent-on)] disabled:opacity-50"
              >
                {savingOwnSite ? "Saving..." : ownSite ? "Update" : "Set your website"}
              </button>
            </form>
          ) : (
            <span className="text-sm text-[var(--text-faint)]">
              {ownSite ? ownSite.url : "Not set"}
            </span>
          )}
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-[14px] lg:grid-cols-4">
        <StatTile
          label="Changes detected"
          value={changeLogs.length}
          sub={`${changesLast30d.length} in last 30d`}
        />
        <StatTile
          label="Judged material"
          value={materialCount}
          sub={
            changeLogs.length > 0
              ? `${Math.round((materialCount / changeLogs.length) * 100)}% of feed`
              : "—"
          }
          barPct={changeLogs.length > 0 ? (materialCount / changeLogs.length) * 100 : 0}
        />
        <StatTile
          label="Awaiting approval"
          value={approvals.filter((a) => a.status === "pending").length}
          sub="Nothing sends without a reviewer."
          valueColor="var(--accent)"
          href="/approvals"
        />
        <StatTile
          label="Crawl success"
          value={crawlSuccessRate !== null ? `${crawlSuccessRate.toFixed(1)}%` : "—"}
          sub="target 95%"
          barPct={crawlSuccessRate ?? 0}
          barColor="var(--teal)"
        />
      </div>

      <div className="grid grid-cols-1 gap-[14px] lg:grid-cols-[1.45fr_1fr]">
        <Card>
          <div className="flex items-start justify-between gap-4">
            <div className="flex flex-col gap-[5px]">
              <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">
                Detection vs. materiality
              </h2>
              <p className="m-0 text-[11.5px] text-[var(--text-faint)]">
                Daily volume, past {TREND_DAYS} days
              </p>
            </div>
            <div className="flex gap-[14px] pt-[3px]">
              <LegendDot color="var(--blue)" label="Detected" />
              <LegendDot color="var(--accent)" label="Material" />
            </div>
          </div>
          <DualTrendChart data={trendData} />
        </Card>

        <Card>
          <div className="flex flex-col gap-[5px]">
            <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">
              Change classification
            </h2>
            <p className="m-0 text-[11.5px] text-[var(--text-faint)]">
              How the agent tagged {changeLogs.length} detected changes
            </p>
          </div>
          <DonutChart data={classificationData} centerValue={changeLogs.length} centerLabel="changes" />
        </Card>
      </div>

      <Card>
        <div className="flex flex-col gap-[5px]">
          <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">
            Success metrics vs. target
          </h2>
          <p className="m-0 text-[11.5px] text-[var(--text-faint)]">Rolling window, this workspace</p>
        </div>
        <div className="flex flex-col gap-[15px]">
          <MetricBar
            label="Crawl success"
            value={crawlSuccessRate}
            target={95}
            color="var(--teal)"
          />
          <MetricBar
            label="Materiality precision"
            value={materialityPrecision}
            target={70}
            color="var(--teal)"
          />
          <MetricBar
            label="Briefing approval rate"
            value={briefingApprovalRate}
            target={50}
            color="var(--accent)"
          />
        </div>
      </Card>

      <Card>
        <div className="flex items-center justify-between">
          <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">
            Competitor comparison
          </h2>
          <p className="m-0 text-[11.5px] text-[var(--text-faint)]">
            {ownSite ? "Your website pinned at top" : "All time"}
          </p>
        </div>
        {comparisonTable.length === 0 ? (
          <p className="text-sm text-[var(--text-faint)]">
            No competitors yet — add one above to get started.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] border-collapse">
              <thead>
                <tr className="border-b border-[var(--border-subtle)] font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dimmer)]">
                  <th className="px-1 pb-[9px] text-left font-normal">Name</th>
                  <th className="px-1 pb-[9px] text-right font-normal">Changes</th>
                  <th className="px-1 pb-[9px] text-right font-normal">Material</th>
                  <th className="px-1 pb-[9px] text-right font-normal">Avg materiality</th>
                  <th className="px-1 pb-[9px] text-left font-normal">Dominant type</th>
                  <th className="px-1 pb-[9px] text-right font-normal">Last change</th>
                </tr>
              </thead>
              <tbody>
                {comparisonTable.map((row, i) => (
                  <tr
                    key={row.id}
                    style={{
                      borderBottom:
                        i < comparisonTable.length - 1 ? "1px solid var(--border-subtler)" : undefined,
                      background: row.isOwnSite ? "var(--accent-wash)" : undefined,
                    }}
                  >
                    <td className="px-1 py-[11px] text-[12.5px] font-medium">
                      <Link
                        href={`/competitors/${row.id}`}
                        className={
                          row.isOwnSite
                            ? "text-[var(--accent)] hover:underline"
                            : "hover:text-[var(--accent)] hover:underline"
                        }
                      >
                        {row.name}
                      </Link>
                    </td>
                    <td className="px-1 py-[11px] text-right font-mono text-[12px] text-[var(--text-secondary)]">
                      {row.changesDetected}
                    </td>
                    <td className="px-1 py-[11px] text-right font-mono text-[12px] text-[var(--text-secondary)]">
                      {row.materialCount}
                    </td>
                    <td className="px-1 py-[11px] text-right font-mono text-[12px] text-[var(--text-secondary)]">
                      {row.avgMateriality !== null ? (row.avgMateriality / 100).toFixed(2) : "—"}
                    </td>
                    <td className="px-1 py-[11px] text-[12px]">
                      {row.dominantClassification ? (
                        <ClassificationBadge classification={row.dominantClassification} />
                      ) : (
                        <span className="text-[var(--text-faint)]">—</span>
                      )}
                    </td>
                    <td className="px-1 py-[11px] text-right font-mono text-[11px] text-[var(--text-faint)]">
                      {row.lastChangeAt ? new Date(row.lastChangeAt).toLocaleDateString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <div className="flex items-center justify-between">
          <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">
            Highest-materiality moves
          </h2>
          <Link href="/feed" className="text-xs font-medium text-[var(--accent)] hover:text-[var(--accent-hover)]">
            View all {changeLogs.length} &rarr;
          </Link>
        </div>
        {topMoves.length === 0 ? (
          <p className="text-sm text-[var(--text-faint)]">
            No scored changes yet — add a competitor, add a surface, and run a check.
          </p>
        ) : (
          <div className="flex flex-col">
            <div className="grid grid-cols-[112px_1fr_132px_78px] gap-[14px] border-b border-[var(--border-subtle)] px-1 pb-[9px] font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dimmer)]">
              <span>Competitor</span>
              <span>What changed</span>
              <span>Class</span>
              <span>Score</span>
            </div>
            {topMoves.map((log, i) => (
              <div
                key={log.id}
                className="grid grid-cols-[112px_1fr_132px_78px] items-center gap-[14px] px-1 py-[13px]"
                style={{ borderBottom: i < topMoves.length - 1 ? "1px solid var(--border-subtler)" : undefined }}
              >
                <span className="truncate text-[12.5px] font-medium">{competitorName(log.competitor_id)}</span>
                <span className="truncate text-[12.5px] text-[var(--text-secondary)]">
                  {log.rationale ?? log.diff ?? "—"}
                </span>
                <ClassificationBadge classification={log.classification} />
                <span
                  className="font-mono text-xs"
                  style={{ color: classificationColor(log.classification) }}
                >
                  {((log.materiality_score ?? 0) / 100).toFixed(2)}
                </span>
              </div>
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

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="h-2 w-2 rounded-sm" style={{ background: color }} />
      <span className="text-[11px] text-[var(--text-muted)]">{label}</span>
    </div>
  );
}

function StatTile({
  label,
  value,
  sub,
  barPct,
  barColor = "var(--accent)",
  valueColor,
  href,
}: {
  label: string;
  value: number | string;
  sub?: string;
  barPct?: number;
  barColor?: string;
  valueColor?: string;
  href?: string;
}) {
  const content = (
    <div className="flex flex-col gap-3 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-5 py-[18px]">
      <span className="font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-faint)]">
        {label}
      </span>
      <div className="flex items-baseline gap-[9px]">
        <span className="text-[30px] font-semibold tracking-[-0.03em]" style={{ color: valueColor }}>
          {value}
        </span>
      </div>
      {sub && <span className="font-mono text-[11.5px] text-[var(--text-muted)]">{sub}</span>}
      {barPct !== undefined && (
        <div className="h-1 overflow-hidden rounded-full bg-[var(--bg-track)]">
          <div className="h-full" style={{ width: `${Math.min(barPct, 100)}%`, background: barColor }} />
        </div>
      )}
    </div>
  );

  if (href) {
    return (
      <Link href={href} className="block transition-opacity hover:opacity-90">
        {content}
      </Link>
    );
  }

  return content;
}

function MetricBar({
  label,
  value,
  target,
  color,
}: {
  label: string;
  value: number | null;
  target: number;
  color: string;
}) {
  const display = value !== null ? Math.round(value) : null;

  return (
    <div className="flex flex-col gap-[7px]">
      <div className="flex items-baseline justify-between">
        <span className="text-[12.5px] text-[var(--text-secondary)]">{label}</span>
        <span className="font-mono text-[11.5px]">
          {display !== null ? `${display}%` : "—"}{" "}
          <span className="text-[var(--text-dim)]">/ {target}</span>
        </span>
      </div>
      <div className="relative h-[7px] overflow-hidden rounded-full bg-[var(--bg-track)]">
        <div
          className="h-full rounded-full"
          style={{ width: `${Math.min(display ?? 0, 100)}%`, background: color }}
        />
        <div
          className="absolute top-0 bottom-0 w-[1.5px] bg-white/60"
          style={{ left: `${Math.min(target, 100)}%` }}
        />
      </div>
    </div>
  );
}
