"use client";

import { useEffect, useState, useCallback, FormEvent } from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { useBriefingJobs } from "@/lib/briefing-jobs-context";
import { Briefing, BriefingAudience, BriefingDigestType, ChangeLog, Competitor } from "@/lib/types";
import ClassificationBadge from "@/components/ui/ClassificationBadge";
import { renderMarkdown } from "@/lib/simple-markdown";

const STATUS_STYLES: Record<Briefing["status"], string> = {
  draft: "bg-[var(--bg-track)] text-[var(--text-dim)]",
  pending_approval: "bg-[var(--accent-wash)] text-[var(--accent)]",
  approved: "bg-[var(--teal)]/15 text-[var(--teal)]",
  rejected: "bg-[var(--red)]/15 text-[var(--red)]",
  delivered: "bg-[var(--blue)]/15 text-[var(--blue)]",
};

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

export default function BriefingsPage() {
  const { workspaceId, ready: contextReady, canEdit } = useWorkspaceContext();
  const { startBriefingJob, activeJobCount, completedCount } = useBriefingJobs();

  const [briefings, setBriefings] = useState<Briefing[]>([]);
  const [changeLogs, setChangeLogs] = useState<ChangeLog[]>([]);
  const [competitors, setCompetitors] = useState<Competitor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [audience, setAudience] = useState<BriefingAudience>("all");
  const [digestType, setDigestType] = useState<BriefingDigestType>("urgent");
  const [generating, setGenerating] = useState(false);

  const load = useCallback(async (wsId: number) => {
    try {
      const [briefingList, logs, comps] = await Promise.all([
        apiFetch(`/workspaces/${wsId}/briefings/`),
        apiFetch(`/workspaces/${wsId}/change-logs/`),
        apiFetch(`/workspaces/${wsId}/competitors/`),
      ]);
      setBriefings(briefingList);
      setChangeLogs(
        [...logs].sort(
          (a: ChangeLog, b: ChangeLog) =>
            new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        )
      );
      setCompetitors(comps);
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

  // A briefing generated elsewhere (or from this page) lands via a
  // background job — completedCount bumps whenever any job resolves, so
  // this list picks up newly-approved-queue briefings without a manual
  // reload.
  useEffect(() => {
    if (!workspaceId || completedCount === 0) return;
    void load(workspaceId);
  }, [completedCount, workspaceId, load]);

  function competitorName(id: number) {
    return competitors.find((c) => c.id === id)?.name ?? `#${id}`;
  }

  function toggleSelected(id: number) {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  }

  async function handleGenerate(e: FormEvent) {
    e.preventDefault();
    if (!workspaceId || selectedIds.length === 0) return;

    setGenerating(true);
    setError(null);
    const ok = await startBriefingJob({
      audience,
      digest_type: digestType,
      change_log_ids: selectedIds,
    });
    if (ok) {
      setSelectedIds([]);
    }
    setGenerating(false);
  }

  if (!contextReady || loading) return null;

  return (
    <div className="flex flex-col gap-[18px] px-4 py-5 pb-10 sm:px-[34px] sm:py-[30px] sm:pb-[44px]" style={{ maxWidth: 860 }}>
      <div className="flex flex-col gap-[7px]">
        <h1 className="m-0 text-[22px] font-semibold tracking-[-0.025em] sm:text-[26px]">Briefings</h1>
        <p className="m-0 max-w-[560px] text-[13.5px] text-[var(--text-muted)]">
          Digest drafts assembled from detected changes, held for approval before anything reaches
          Slack, email, or a CRM.
        </p>
      </div>

      {error && (
        <p className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</p>
      )}

      {canEdit && (
        <div className="flex flex-col gap-4 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-4 sm:px-[22px] sm:py-5">
          <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">
            Generate a briefing
          </h2>
          <form onSubmit={handleGenerate} className="flex flex-col gap-4">
            <div className="flex flex-col gap-4 sm:flex-row">
              <div className="flex flex-1 flex-col gap-1.5">
                <label className="text-xs font-medium text-[var(--text-muted)]">Audience</label>
                <select
                  value={audience}
                  onChange={(e) => setAudience(e.target.value as BriefingAudience)}
                  className="w-full rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-sm text-[var(--text-secondary)]"
                >
                  <option value="all">All</option>
                  <option value="exec">Exec</option>
                  <option value="sales">Sales</option>
                  <option value="product">Product</option>
                </select>
              </div>
              <div className="flex flex-1 flex-col gap-1.5">
                <label className="text-xs font-medium text-[var(--text-muted)]">Digest type</label>
                <select
                  value={digestType}
                  onChange={(e) => setDigestType(e.target.value as BriefingDigestType)}
                  className="w-full rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-sm text-[var(--text-secondary)]"
                >
                  <option value="urgent">Urgent</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                </select>
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-[var(--text-muted)]">Source changes</label>
                {selectedIds.length > 0 && (
                  <span className="font-mono text-[10.5px] text-[var(--text-faint)]">
                    {selectedIds.length} selected
                  </span>
                )}
              </div>
              {changeLogs.length === 0 ? (
                <p className="text-sm text-[var(--text-faint)]">
                  No detected changes yet to draft a briefing from.
                </p>
              ) : (
                <div className="flex max-h-72 flex-col gap-1.5 overflow-y-auto rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-nested)] p-2">
                  {changeLogs.map((log) => {
                    const checked = selectedIds.includes(log.id);
                    return (
                      <label
                        key={log.id}
                        className="flex cursor-pointer items-start gap-2.5 rounded-md px-2 py-1.5 transition-colors hover:bg-[var(--bg-track)]"
                        style={{ background: checked ? "var(--accent-wash)" : undefined }}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleSelected(log.id)}
                          className="mt-[3px] accent-[var(--accent)]"
                        />
                        <div className="flex min-w-0 flex-1 flex-col gap-1">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="text-[12.5px] font-medium text-[var(--text-primary)]">
                              {competitorName(log.competitor_id)}
                            </span>
                            <ClassificationBadge classification={log.classification} />
                            <span className="ml-auto font-mono text-[10.5px] text-[var(--text-faint)]">
                              {relativeTime(log.created_at)}
                            </span>
                          </div>
                          <p className="m-0 truncate text-[12.5px] text-[var(--text-secondary)]">
                            {log.headline || log.rationale || `Change #${log.id}`}
                          </p>
                        </div>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>

            <button
              type="submit"
              disabled={generating || selectedIds.length === 0}
              className="h-8 w-full rounded-lg bg-[var(--accent)] px-3 text-xs font-semibold text-[var(--accent-on)] disabled:opacity-50 sm:w-fit"
            >
              {generating ? "Queuing..." : "Generate briefing"}
            </button>
          </form>
        </div>
      )}

      <div className="flex flex-col gap-[14px]">
        <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">All briefings</h2>
        {activeJobCount === 0 && briefings.length === 0 ? (
          <p className="text-sm text-[var(--text-faint)]">No briefings yet.</p>
        ) : (
          <div className="flex flex-col gap-[14px]">
            {Array.from({ length: activeJobCount }).map((_, i) => (
              <div
                key={`generating-${i}`}
                className="flex items-center gap-2.5 rounded-[14px] border border-dashed border-[var(--border-hover)] bg-[var(--bg-card)] px-4 py-4 sm:px-[22px] sm:py-5"
              >
                <span
                  className="h-2 w-2 flex-shrink-0 rounded-full"
                  style={{ background: "var(--accent)", animation: "pulseDot 1.4s ease-in-out infinite" }}
                />
                <span className="text-[13px] text-[var(--text-muted)]">Generating briefing…</span>
              </div>
            ))}
            {briefings.map((b) => (
              <div
                key={b.id}
                className="flex flex-col gap-3 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-4 sm:px-[22px] sm:py-5"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[14.5px] font-semibold tracking-tight">{b.title}</span>
                  <span
                    className={`rounded-md px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide ${STATUS_STYLES[b.status]}`}
                  >
                    {b.status.replace("_", " ")}
                  </span>
                </div>
                <p className="font-mono text-[11px] text-[var(--text-faint)]">
                  {b.audience} &middot; {b.digest_type} &middot;{" "}
                  {new Date(b.created_at).toLocaleString()}
                </p>
                <div className="max-sm:break-words text-[13px] leading-[1.6] text-[var(--text-secondary)]">
                  {renderMarkdown(b.body_markdown)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
