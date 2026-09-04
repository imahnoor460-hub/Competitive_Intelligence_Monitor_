"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { apiFetch, ApiError } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { useCheckJobs } from "@/lib/check-jobs-context";
import { ChangeLog, Competitor, Snapshot, Surface } from "@/lib/types";
import { surfaceDisplayName } from "@/lib/surface-name";
import { SURFACE_TYPE_STYLES, HeadingDot, BaselineCard, ChangeCard } from "@/components/changelog/SurfaceChangeCards";

export default function SurfaceDetailPage() {
  const params = useParams();
  const competitorId = Number(params.id);
  const surfaceId = Number(params.surfaceId);
  const { workspaceId, ready: contextReady, canEdit } = useWorkspaceContext();
  // Check state lives in the provider, not here: with a queue configured the
  // check outlives this page, and a reload has to be able to pick it back up.
  const { startSurfaceCheck, surfaceCheckState, completedCount } = useCheckJobs();
  const checkStatus = surfaceCheckState(surfaceId);

  const [loading, setLoading] = useState(true);
  const [competitor, setCompetitor] = useState<Competitor | null>(null);
  const [surface, setSurface] = useState<Surface | null>(null);
  const [changeLogs, setChangeLogs] = useState<ChangeLog[]>([]);
  const [baselineSnapshot, setBaselineSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(
    async (wsId: number) => {
      try {
        const [comps, surfs, logs] = await Promise.all([
          apiFetch(`/workspaces/${wsId}/competitors/`),
          apiFetch(`/workspaces/${wsId}/competitors/${competitorId}/surfaces/`),
          apiFetch(`/workspaces/${wsId}/change-logs/`),
        ]);
        setCompetitor(comps.find((c: Competitor) => c.id === competitorId) ?? null);
        const surf = surfs.find((s: Surface) => s.id === surfaceId) ?? null;
        setSurface(surf);

        const surfaceLogs: ChangeLog[] = logs
          .filter((l: ChangeLog) => l.surface_id === surfaceId)
          .sort(
            (a: ChangeLog, b: ChangeLog) =>
              new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
          );
        setChangeLogs(surfaceLogs);

        // No ChangeLog yet — either this page has never been checked, or
        // every check so far found nothing worth recording — so there's
        // nothing to diff. Fall back to its latest captured snapshot so
        // the page still shows what's currently there instead of just
        // "no changes yet." A page that's genuinely never been checked has
        // no snapshot to fetch either; the 404 is caught and just leaves
        // this null, which the empty state below explains.
        if (surfaceLogs.length === 0) {
          try {
            const snapshot: Snapshot = await apiFetch(
              `/workspaces/${wsId}/competitors/${competitorId}/surfaces/${surfaceId}/snapshot`
            );
            setBaselineSnapshot(snapshot);
          } catch {
            setBaselineSnapshot(null);
          }
        } else {
          setBaselineSnapshot(null);
        }
      } catch (err) {
        if (err instanceof ApiError) setError(err.message);
      } finally {
        setLoading(false);
      }
    },
    [competitorId, surfaceId]
  );

  useEffect(() => {
    if (!workspaceId) return;
    void (async () => {
      await load(workspaceId);
    })();
  }, [workspaceId, load]);

  async function handleCheckSurface() {
    await startSurfaceCheck(competitorId, surfaceId);
  }

  // A queued check finishes in a worker, long after the click. Refetch when
  // one settles so the change it found actually appears, rather than waiting
  // for the user to reload the page themselves.
  useEffect(() => {
    if (completedCount === 0 || !workspaceId) return;
    void (async () => {
      await load(workspaceId);
    })();
  }, [completedCount, workspaceId, load]);

  async function handleDeleteSurface() {
    if (!workspaceId || !surface) return;
    if (!window.confirm(`Stop monitoring "${surfaceDisplayName(surface)}"? This removes its change history.`)) {
      return;
    }
    setDeleting(true);
    try {
      await apiFetch(`/workspaces/${workspaceId}/competitors/${competitorId}/surfaces/${surfaceId}`, {
        method: "DELETE",
      });
      window.location.href = `/competitors/${competitorId}`;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete page");
      setDeleting(false);
    }
  }

  if (!contextReady || loading) return null;

  if (!surface) {
    return (
      <div className="flex flex-col gap-3 px-4 py-5 sm:px-[34px] sm:py-[30px]" style={{ maxWidth: 900 }}>
        <p className="text-sm text-[var(--text-faint)]">Page not found.</p>
        <Link
          href={`/competitors/${competitorId}`}
          className="text-sm font-medium text-[var(--accent)] hover:text-[var(--accent-hover)]"
        >
          Back to {competitor?.name ?? "competitor"}
        </Link>
      </div>
    );
  }

  const typeStyle = SURFACE_TYPE_STYLES[surface.surface_type];

  return (
    <div className="flex flex-col gap-[18px] px-4 py-5 pb-10 sm:px-[34px] sm:py-[30px] sm:pb-[44px]" style={{ maxWidth: 900 }}>
      <Link
        href={`/competitors/${competitorId}`}
        className="w-fit text-[12.5px] font-medium text-[var(--text-muted)] hover:text-[var(--accent)]"
      >
        ← {competitor?.name ?? "Back"}
      </Link>

      <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-end sm:justify-between sm:gap-6">
        <div className="flex flex-col gap-[7px]">
          <div className="flex items-center gap-2.5 max-sm:flex-wrap">
            <h1 className="m-0 text-[22px] font-semibold tracking-[-0.025em] sm:text-[26px]">
              {surfaceDisplayName(surface)}
            </h1>
            <span
              className="flex-shrink-0 rounded-md px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide"
              style={{
                background: `color-mix(in srgb, ${typeStyle.color} 12%, transparent)`,
                color: typeStyle.color,
              }}
            >
              {surface.surface_type}
            </span>
          </div>
          <a
            href={surface.url}
            target="_blank"
            rel="noopener noreferrer"
            className="max-sm:break-all text-[13px] text-[var(--blue)] hover:underline"
          >
            {surface.url}
          </a>
          <p className="m-0 font-mono text-[11px] text-[var(--text-faint)]">
            Checked {surface.check_frequency}
            {surface.last_checked_at && ` · last checked ${new Date(surface.last_checked_at).toLocaleString()}`}
          </p>
        </div>
        {canEdit && (
          <div className="flex items-center gap-2 max-sm:flex-wrap">
            <button
              onClick={handleCheckSurface}
              disabled={checkStatus.state === "checking"}
              className="h-8 rounded-lg border border-[var(--border-input)] bg-[var(--bg-card)] px-3 text-xs font-medium text-[var(--text-secondary)] hover:border-[var(--border-hover)] disabled:opacity-50"
            >
              {checkStatus.state === "checking" ? "Checking..." : "Check now"}
            </button>
            <button
              onClick={handleDeleteSurface}
              disabled={deleting}
              className="h-8 rounded-lg border border-[var(--red)]/40 px-3 text-xs font-medium text-[var(--red)] disabled:opacity-50"
            >
              {deleting ? "Deleting…" : "Delete page"}
            </button>
          </div>
        )}
      </div>

      {checkStatus.state === "done" && (
        <p className="m-0 text-[12.5px] text-[var(--text-muted)]">{checkStatus.message}</p>
      )}
      {checkStatus.state === "error" && (
        <p className="m-0 text-[12.5px] text-[var(--red)]">{checkStatus.message}</p>
      )}
      {error && <p className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</p>}

      <div className="flex flex-col gap-4 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-4 sm:px-[22px] sm:py-5">
        <h2 className="m-0 flex items-center gap-2 text-[14.5px] font-semibold tracking-[-0.01em]">
          <HeadingDot />
          Change log
        </h2>
        {!baselineSnapshot && changeLogs.length === 0 ? (
          <p className="text-sm text-[var(--text-faint)]">
            {surface.last_checked_at
              ? "No changes detected yet for this page."
              : canEdit
              ? "This page hasn't been checked yet — click “Check now” above to capture its current content."
              : "This page hasn't been checked yet."}
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            {baselineSnapshot && <BaselineCard snapshot={baselineSnapshot} surface={surface} />}
            {changeLogs.map((log) => (
              <ChangeCard key={log.id} log={log} surface={surface} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
