"use client";

import { useEffect, useState, useCallback } from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { useBattlecardJobs } from "@/lib/battlecard-jobs-context";
import { Battlecard, ChangeLog, Competitor } from "@/lib/types";

export default function BattlecardsPage() {
  const { workspaceId, ready: contextReady, canEdit } = useWorkspaceContext();
  const { startBattlecardUpdateJob, activeCompetitorIds, completedCount } = useBattlecardJobs();

  const [competitors, setCompetitors] = useState<Competitor[]>([]);
  const [changeLogs, setChangeLogs] = useState<ChangeLog[]>([]);
  const [battlecardsById, setBattlecardsById] = useState<Record<number, Battlecard | null>>({});
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [selected, setSelected] = useState<Record<number, number[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [proposing, setProposing] = useState<Record<number, boolean>>({});

  const load = useCallback(async (wsId: number) => {
    try {
      const [comps, logs] = await Promise.all([
        apiFetch(`/workspaces/${wsId}/competitors/`),
        apiFetch(`/workspaces/${wsId}/change-logs/`),
      ]);
      setCompetitors(comps);
      setChangeLogs(logs);
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

  // The competitor list here is fetched once on mount, so a competitor
  // deleted from a different page/tab wouldn't disappear from an
  // already-open Battlecards tab until the next full reload. Refetching on
  // focus keeps it in sync without polling.
  useEffect(() => {
    if (!workspaceId) return;
    function handleFocus() {
      if (document.visibilityState === "visible") {
        void load(workspaceId!);
      }
    }
    window.addEventListener("focus", handleFocus);
    document.addEventListener("visibilitychange", handleFocus);
    return () => {
      window.removeEventListener("focus", handleFocus);
      document.removeEventListener("visibilitychange", handleFocus);
    };
  }, [workspaceId, load]);

  async function loadBattlecard(competitorId: number) {
    if (!workspaceId) return;
    try {
      const battlecard = await apiFetch(
        `/workspaces/${workspaceId}/competitors/${competitorId}/battlecard/`
      );
      setBattlecardsById((prev) => ({ ...prev, [competitorId]: battlecard }));
    } catch (err) {
      if (err instanceof ApiError && err.message.toLowerCase().includes("no battlecard")) {
        setBattlecardsById((prev) => ({ ...prev, [competitorId]: null }));
      } else if (err instanceof ApiError) {
        setError(err.message);
      }
    }
  }

  async function toggleExpand(competitorId: number) {
    const willExpand = !expanded[competitorId];
    setExpanded((prev) => ({ ...prev, [competitorId]: willExpand }));
    if (willExpand && battlecardsById[competitorId] === undefined) {
      await loadBattlecard(competitorId);
    }
  }

  function toggleSelected(competitorId: number, changeLogId: number) {
    setSelected((prev) => {
      const current = prev[competitorId] ?? [];
      const next = current.includes(changeLogId)
        ? current.filter((id) => id !== changeLogId)
        : [...current, changeLogId];
      return { ...prev, [competitorId]: next };
    });
  }

  // Proposing a battlecard update runs as a background job (LLM generation
  // can take a while) — this only dispatches it and clears the selection;
  // the completedCount effect below reloads the battlecard once the job
  // resolves, and activeCompetitorIds drives the "Generating..." state in
  // the meantime.
  async function handlePropose(competitorId: number) {
    const changeLogIds = selected[competitorId] ?? [];
    if (!workspaceId || changeLogIds.length === 0) return;

    setProposing((prev) => ({ ...prev, [competitorId]: true }));
    setError(null);
    try {
      const ok = await startBattlecardUpdateJob({ competitorId, change_log_ids: changeLogIds });
      if (ok) {
        setSelected((prev) => ({ ...prev, [competitorId]: [] }));
      } else {
        setError("Failed to propose update");
      }
    } finally {
      setProposing((prev) => ({ ...prev, [competitorId]: false }));
    }
  }

  // A proposal job resolving (success or failure) means this competitor's
  // battlecard may have a fresh pending update attached — refresh every
  // expanded card so approval-queue state and the "Generating..." badge
  // clear in sync.
  useEffect(() => {
    if (completedCount === 0) return;
    Object.keys(expanded)
      .filter((id) => expanded[Number(id)])
      .forEach((id) => void loadBattlecard(Number(id)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [completedCount]);

  if (!contextReady || loading) return null;

  return (
    <div className="flex flex-col gap-[18px] px-4 py-5 pb-10 sm:px-[34px] sm:py-[30px] sm:pb-[44px]" style={{ maxWidth: 1000 }}>
      <div className="flex flex-col gap-[7px]">
        <h1 className="m-0 text-[22px] font-semibold tracking-[-0.025em] sm:text-[26px]">Battlecards</h1>
        <p className="m-0 max-w-[560px] text-[13.5px] text-[var(--text-muted)]">
          Live positioning per competitor. Propose updates from detected changes — each revision
          waits for approval before it replaces the current version.
        </p>
      </div>

      {error && (
        <p className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</p>
      )}

      {competitors.length === 0 ? (
        <p className="text-sm text-[var(--text-faint)]">No competitors tracked yet.</p>
      ) : (
        <div className="flex flex-col gap-[14px]">
          {competitors.map((c) => {
            const battlecard = battlecardsById[c.id];
            const competitorChangeLogs = changeLogs.filter((log) => log.competitor_id === c.id);
            const selectedIds = selected[c.id] ?? [];
            const isExpanded = !!expanded[c.id];

            return (
              <div
                key={c.id}
                className="flex flex-col gap-4 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-4 sm:px-[22px] sm:py-5"
              >
                <button
                  onClick={() => toggleExpand(c.id)}
                  className="flex w-full items-center justify-between gap-3 text-left"
                >
                  <span className="text-[14.5px] font-semibold tracking-tight">{c.name}</span>
                  <div className="flex items-center gap-2.5">
                    {battlecard && (
                      <span className="font-mono text-[11px] text-[var(--text-faint)]">
                        v{battlecard.version}
                      </span>
                    )}
                    <span className="text-[13px] text-[var(--text-dim)]">
                      {isExpanded ? "−" : "+"}
                    </span>
                  </div>
                </button>

                {isExpanded && (
                  <div className="flex flex-col gap-4 border-t border-[var(--border-subtle)] pt-4">
                    {battlecard ? (
                      <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-nested)] px-3 py-3 sm:px-4">
                        <p className="whitespace-pre-wrap max-sm:break-words text-[13px] leading-[1.6] text-[var(--text-secondary)]">
                          {battlecard.content_markdown || "(empty)"}
                        </p>
                      </div>
                    ) : (
                      <p className="text-sm text-[var(--text-faint)]">
                        No battlecard yet for this competitor.
                      </p>
                    )}

                    {canEdit && (
                      <div className="flex flex-col gap-2.5">
                        <p className="text-xs font-medium text-[var(--text-secondary)]">
                          Propose an update from:
                        </p>
                        {competitorChangeLogs.length === 0 ? (
                          <p className="text-sm text-[var(--text-faint)]">No detected changes yet.</p>
                        ) : (
                          <ul className="flex max-h-40 flex-col gap-1 overflow-y-auto rounded-lg border border-[var(--border-subtle)] p-2">
                            {competitorChangeLogs.map((log) => (
                              <li key={log.id}>
                                <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)] max-sm:flex-wrap">
                                  <input
                                    type="checkbox"
                                    checked={selectedIds.includes(log.id)}
                                    onChange={() => toggleSelected(c.id, log.id)}
                                  />
                                  <span className="rounded-md bg-[var(--bg-track)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                                    {log.classification ?? "unscored"}
                                  </span>
                                  <span className="truncate max-sm:min-w-0 max-sm:flex-1">
                                    {log.rationale ?? log.diff ?? `#${log.id}`}
                                  </span>
                                </label>
                              </li>
                            ))}
                          </ul>
                        )}
                        <button
                          onClick={() => handlePropose(c.id)}
                          disabled={
                            proposing[c.id] || activeCompetitorIds.includes(c.id) || selectedIds.length === 0
                          }
                          className="h-8 w-full rounded-lg bg-[var(--accent)] px-3 text-xs font-semibold text-[var(--accent-on)] disabled:opacity-50 sm:w-fit"
                        >
                          {activeCompetitorIds.includes(c.id)
                            ? "Generating..."
                            : proposing[c.id]
                              ? "Proposing..."
                              : "Propose update"}
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
