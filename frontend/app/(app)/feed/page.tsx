"use client";

import { Fragment, useEffect, useState, useCallback, useMemo, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { useBriefingJobs } from "@/lib/briefing-jobs-context";
import { ChangeLog, Competitor, Surface } from "@/lib/types";
import ClassificationBadge from "@/components/ui/ClassificationBadge";
import { materialityStyle } from "@/components/ui/MaterialityBar";
import { surfaceDisplayName } from "@/lib/surface-name";
import { ChangeCard } from "@/components/changelog/SurfaceChangeCards";

const NOISE_STORAGE_PREFIX = "ci-noise-dismissed:";

function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

export default function ChangeFeedPage() {
  const { workspaceId, ready: contextReady } = useWorkspaceContext();
  const { startBriefingJob } = useBriefingJobs();
  const searchParams = useSearchParams();
  const highlightId = Number(searchParams.get("highlight")) || null;
  const rowRefs = useRef<Record<number, HTMLElement | null>>({});
  const cardRefs = useRef<Record<number, HTMLElement | null>>({});

  const [competitors, setCompetitors] = useState<Competitor[]>([]);
  const [surfacesById, setSurfacesById] = useState<Record<number, Surface>>({});
  const [changeLogs, setChangeLogs] = useState<ChangeLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [materialOnly, setMaterialOnly] = useState(false);
  const [noiseIds, setNoiseIds] = useState<Set<number>>(new Set());
  const [showFiltered, setShowFiltered] = useState(false);
  // Rows are collapsed by default so the feed reads as a scannable table;
  // opening a row reveals its diff/rationale/actions below it.
  const [openRows, setOpenRows] = useState<Set<number>>(new Set());
  const [queued, setQueued] = useState<Record<number, boolean>>({});

  const storageKey = workspaceId ? `${NOISE_STORAGE_PREFIX}${workspaceId}` : null;

  useEffect(() => {
    if (!storageKey) return;
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (raw) setNoiseIds(new Set(JSON.parse(raw)));
    } catch {
      // Corrupt or missing localStorage entry — start with an empty dismiss set.
    }
  }, [storageKey]);

  function persistNoise(next: Set<number>) {
    setNoiseIds(next);
    if (storageKey) {
      window.localStorage.setItem(storageKey, JSON.stringify(Array.from(next)));
    }
  }

  const load = useCallback(async (wsId: number) => {
    try {
      const [comps, logs] = await Promise.all([
        apiFetch(`/workspaces/${wsId}/competitors/`),
        apiFetch(`/workspaces/${wsId}/change-logs/`),
      ]);
      setCompetitors(comps);
      setChangeLogs(
        [...logs].sort(
          (a: ChangeLog, b: ChangeLog) =>
            new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        )
      );

      const perCompetitorSurfaces = await Promise.all(
        comps.map((c: Competitor) =>
          apiFetch(`/workspaces/${wsId}/competitors/${c.id}/surfaces/`).catch(() => [])
        )
      );
      const map: Record<number, Surface> = {};
      perCompetitorSurfaces.flat().forEach((s: Surface) => {
        map[s.id] = s;
      });
      setSurfacesById(map);
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

  useEffect(() => {
    if (!highlightId || loading) return;
    setOpenRows((prev) => (prev.has(highlightId) ? prev : new Set(prev).add(highlightId)));
    // `offsetParent` is null for the copy its breakpoint has hidden, so this
    // scrolls whichever of the two layouts is actually on screen.
    const el = [cardRefs.current[highlightId], rowRefs.current[highlightId]].find(
      (node) => node && node.offsetParent !== null
    );
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlightId, loading, changeLogs]);

  function toggleRow(id: number) {
    setOpenRows((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function competitorName(id: number) {
    return competitors.find((c) => c.id === id)?.name ?? `#${id}`;
  }

  const filteredCount = useMemo(
    () => changeLogs.filter((l) => noiseIds.has(l.id)).length,
    [changeLogs, noiseIds]
  );

  const visibleLogs = useMemo(() => {
    return changeLogs.filter((l) => {
      if (l.id === highlightId) return true;
      if (!showFiltered && noiseIds.has(l.id)) return false;
      if (materialOnly && !(l.materiality_score !== null && l.materiality_score >= 50)) return false;
      return true;
    });
  }, [changeLogs, noiseIds, showFiltered, materialOnly, highlightId]);

  function markAsNoise(id: number) {
    const next = new Set(noiseIds);
    next.add(id);
    persistNoise(next);
  }

  function unmarkNoise(id: number) {
    const next = new Set(noiseIds);
    next.delete(id);
    persistNoise(next);
  }

  async function draftBriefing(log: ChangeLog) {
    if (!workspaceId) return;
    const ok = await startBriefingJob({ change_log_ids: [log.id] });
    if (ok) {
      setQueued((prev) => ({ ...prev, [log.id]: true }));
    }
  }

  // The expanded body is identical in both layouts, so it is built once here
  // rather than kept in step in two places.
  function renderDetail(log: ChangeLog, surface: Surface | undefined, isNoise: boolean) {
    return (
      <div className="flex flex-col gap-3" onClick={(e) => e.stopPropagation()}>
        <ChangeCard log={log} surface={surface} />

        <div className="flex items-center gap-3 max-sm:flex-wrap">
          {isNoise ? (
            <button
              onClick={() => unmarkNoise(log.id)}
              className="h-7 rounded-md border border-[var(--border-input)] px-2.5 text-[11.5px] font-medium text-[var(--text-secondary)] hover:border-[var(--border-hover)]"
            >
              Restore to feed
            </button>
          ) : (
            <button
              onClick={() => markAsNoise(log.id)}
              className="h-7 rounded-md border border-[var(--border-input)] px-2.5 text-[11.5px] font-medium text-[var(--text-faint)] hover:border-[var(--border-hover)] hover:text-[var(--text-secondary)]"
            >
              Mark as noise
            </button>
          )}
          {queued[log.id] ? (
            <span className="text-[11.5px] font-medium text-[var(--teal)]">
              Queued — you&apos;ll get a notification when it&apos;s ready
            </span>
          ) : (
            <button
              onClick={() => draftBriefing(log)}
              className="h-7 rounded-md bg-[var(--accent)] px-2.5 text-[11.5px] font-semibold text-[var(--accent-on)] disabled:opacity-50"
            >
              Draft briefing
            </button>
          )}
        </div>
      </div>
    );
  }

  if (!contextReady || loading) return null;

  return (
    <div className="flex flex-col gap-[18px] px-4 py-5 pb-10 sm:px-[34px] sm:py-[30px] sm:pb-[44px]" style={{ maxWidth: 1100 }}>
      <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-end sm:justify-between sm:gap-6">
        <div className="flex flex-col gap-[7px]">
          <h1 className="m-0 text-[22px] font-semibold tracking-[-0.025em] sm:text-[26px]">Change feed</h1>
          <p className="m-0 max-w-[560px] text-[13.5px] text-[var(--text-muted)]">
            Every detected change, newest first. Draft a briefing straight from any moment that
            matters.
          </p>
        </div>
        <label className="flex h-8 items-center gap-2 max-sm:flex-shrink-0 rounded-lg border border-[var(--border-input)] bg-[var(--bg-card)] px-3 text-xs font-medium text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={materialOnly}
            onChange={(e) => setMaterialOnly(e.target.checked)}
            className="accent-[var(--accent)]"
          />
          Material only
        </label>
      </div>

      {error && (
        <p className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</p>
      )}

      {filteredCount > 0 && (
        <div className="flex items-center justify-between rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-nested)] px-4 py-2.5 text-xs text-[var(--text-muted)] max-sm:flex-wrap max-sm:gap-2">
          <span>
            {filteredCount} change{filteredCount === 1 ? "" : "s"} filtered as noise
          </span>
          <button
            onClick={() => setShowFiltered((v) => !v)}
            className="font-medium text-[var(--accent)] hover:text-[var(--accent-hover)]"
          >
            {showFiltered ? "Hide filtered" : "Show filtered"}
          </button>
        </div>
      )}

      {visibleLogs.length === 0 ? (
        <p className="text-sm text-[var(--text-faint)]">No changes match this view yet.</p>
      ) : (
        <>
        {/* Seven columns need 760px. Portrait gets the same rows — every
            column included — as tappable cards instead. */}
        <div className="flex flex-col gap-2.5 sm:hidden">
          {visibleLogs.map((log) => {
            const surface = surfacesById[log.surface_id];
            const isNoise = noiseIds.has(log.id);
            const isOpen = openRows.has(log.id);
            const score = log.materiality_score;
            const { color: scoreColor } = materialityStyle(score ?? 0);

            return (
              <div
                key={log.id}
                ref={(el) => {
                  cardRefs.current[log.id] = el;
                }}
                className="rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] p-3.5"
                style={{
                  background: log.id === highlightId ? "var(--accent-wash)" : undefined,
                  opacity: isNoise ? 0.55 : 1,
                }}
              >
                <button
                  onClick={() => toggleRow(log.id)}
                  aria-expanded={isOpen}
                  className="flex w-full flex-col gap-2 text-left"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="min-w-0 flex-1 text-[13px] font-medium">
                      {competitorName(log.competitor_id)}
                    </span>
                    <span
                      className="font-mono text-[12px]"
                      style={{ color: score !== null ? scoreColor : "var(--text-dim)" }}
                    >
                      {score !== null ? (score / 100).toFixed(2) : "—"}
                    </span>
                    <span className="text-[var(--text-faint)]">{isOpen ? "▾" : "▸"}</span>
                  </div>
                  <p className="m-0 text-[12.5px] leading-[1.45] text-[var(--text-secondary)]">
                    {log.headline || log.rationale || (log.diff ? "Content change detected" : "—")}
                  </p>
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
                    <ClassificationBadge classification={log.classification} />
                    <span className="min-w-0 truncate font-mono text-[11px] text-[var(--text-dim)]">
                      {surface ? surfaceDisplayName(surface) : "—"}
                    </span>
                    <span className="ml-auto font-mono text-[11px] text-[var(--text-faint)]">
                      {relativeTime(log.created_at)}
                    </span>
                  </div>
                </button>
                {isOpen && (
                  <div className="mt-3 border-t border-[var(--border-subtler)] pt-3">
                    {renderDetail(log, surface, isNoise)}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div className="hidden overflow-x-auto rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] sm:block">
          <table className="w-full min-w-[760px] border-collapse text-[13px]">
            <thead>
              <tr className="border-b border-[var(--border-subtle)] font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dimmer)]">
                <th className="px-[22px] py-3 text-left font-normal">Competitor</th>
                <th className="px-1 py-3 text-left font-normal">Page</th>
                <th className="px-1 py-3 text-left font-normal">Class</th>
                <th className="px-1 py-3 text-left font-normal">What changed</th>
                <th className="px-1 py-3 text-right font-normal">Materiality</th>
                <th className="px-1 py-3 text-right font-normal">When</th>
                <th className="w-8 px-[22px] py-3"></th>
              </tr>
            </thead>
            <tbody>
              {visibleLogs.map((log, i) => {
                const surface = surfacesById[log.surface_id];
                const isNoise = noiseIds.has(log.id);
                const isHighlighted = log.id === highlightId;
                const isOpen = openRows.has(log.id);
                const score = log.materiality_score;
                const { color: scoreColor } = materialityStyle(score ?? 0);
                const isLast = i === visibleLogs.length - 1;

                return (
                  <Fragment key={log.id}>
                    <tr
                      ref={(el) => {
                        rowRefs.current[log.id] = el;
                      }}
                      onClick={() => toggleRow(log.id)}
                      className="cursor-pointer transition-colors hover:bg-[var(--bg-nested)]"
                      style={{
                        borderBottom: isOpen || isLast ? undefined : "1px solid var(--border-subtler)",
                        background: isHighlighted ? "var(--accent-wash)" : undefined,
                        opacity: isNoise ? 0.55 : 1,
                      }}
                    >
                      <td className="px-[22px] py-[11px] font-medium">{competitorName(log.competitor_id)}</td>
                      <td className="max-w-[160px] truncate px-1 py-[11px] text-[var(--text-secondary)]" title={surface?.url}>
                        {surface ? surfaceDisplayName(surface) : "—"}
                      </td>
                      <td className="px-1 py-[11px]">
                        <ClassificationBadge classification={log.classification} />
                      </td>
                      <td className="max-w-[280px] truncate px-1 py-[11px] text-[var(--text-secondary)]">
                        {log.headline || log.rationale || (log.diff ? "Content change detected" : "—")}
                      </td>
                      <td className="px-1 py-[11px] text-right font-mono text-[12px]" style={{ color: score !== null ? scoreColor : "var(--text-dim)" }}>
                        {score !== null ? (score / 100).toFixed(2) : "—"}
                      </td>
                      <td className="px-1 py-[11px] text-right font-mono text-[11px] text-[var(--text-faint)] whitespace-nowrap">
                        {relativeTime(log.created_at)}
                      </td>
                      <td className="px-[22px] py-[11px] text-right text-[var(--text-faint)]">
                        {isOpen ? "▾" : "▸"}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr
                        style={{
                          borderBottom: isLast ? undefined : "1px solid var(--border-subtler)",
                          background: "var(--bg-nested)",
                        }}
                      >
                        <td colSpan={7} className="px-[22px] py-4">
                          {renderDetail(log, surface, isNoise)}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        </>
      )}
    </div>
  );
}
