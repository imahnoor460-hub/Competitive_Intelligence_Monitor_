"use client";

import { useEffect, useRef } from "react";
import { apiFetch } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { useBriefingJobs } from "@/lib/briefing-jobs-context";
import { useBattlecardJobs } from "@/lib/battlecard-jobs-context";
import { useCompetitorDiscoveryJobs } from "@/lib/competitor-discovery-jobs-context";
import { useCheckJobs } from "@/lib/check-jobs-context";
import { useSiteSummaryJobs } from "@/lib/site-summary-jobs-context";
import { ActiveJobs } from "@/lib/types";

/**
 * Re-attaches pollers to work that was already running when this tab loaded.
 *
 * Poll lifecycle lives in React memory, so before this a refresh orphaned
 * every in-flight job: the worker kept going and finished the work, but the
 * UI that started it was gone, so nothing ever announced the result or
 * refetched the page. Closing a laptop lid mid-sweep had the same effect.
 *
 * Renders nothing. It is a component rather than a hook in the layout because
 * it has to sit inside all five job providers to reach their `track`
 * functions, and hooks cannot be called from a component that also renders
 * those providers.
 */
export default function ActiveJobsRehydrator() {
  const { workspaceId } = useWorkspaceContext();
  const { trackBriefingJob } = useBriefingJobs();
  const { trackBattlecardUpdateJob } = useBattlecardJobs();
  const { trackDiscoveryJob } = useCompetitorDiscoveryJobs();
  const { trackCheckRun, trackSweep } = useCheckJobs();
  const { trackSiteSummaryJob } = useSiteSummaryJobs();

  // One rehydration per workspace, not per render. Every `track` function is a
  // useCallback whose identity changes when its provider re-renders, so
  // depending on them would refetch on unrelated state changes; the registries
  // would dedupe the pollers, but the requests would still go out.
  const rehydratedFor = useRef<number | null>(null);

  useEffect(() => {
    if (!workspaceId) return;
    if (rehydratedFor.current === workspaceId) return;
    rehydratedFor.current = workspaceId;

    let cancelled = false;

    void (async () => {
      let active: ActiveJobs;
      try {
        active = await apiFetch(`/workspaces/${workspaceId}/jobs/active`);
      } catch {
        // Best-effort recovery. Failing here costs the re-attach, not the
        // work — the jobs still finish server-side, and the next reload gets
        // another go. Allow that next attempt by clearing the guard.
        rehydratedFor.current = null;
        return;
      }
      if (cancelled) return;

      active.check_sweeps.forEach(trackSweep);
      active.check_runs.forEach(trackCheckRun);
      active.briefing_job_ids.forEach(trackBriefingJob);
      active.battlecard_update_jobs.forEach((job) =>
        trackBattlecardUpdateJob(job.competitor_id, job.id)
      );
      active.competitor_discovery_jobs.forEach((job) =>
        trackDiscoveryJob(job.competitor_id, job.id)
      );
      active.site_summary_jobs.forEach((job) =>
        trackSiteSummaryJob(job.competitor_id, job.id)
      );
    })();

    return () => {
      cancelled = true;
    };
    // The track functions are deliberately not dependencies — see the ref
    // above. They are read at effect time and are stable enough for that.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  return null;
}
