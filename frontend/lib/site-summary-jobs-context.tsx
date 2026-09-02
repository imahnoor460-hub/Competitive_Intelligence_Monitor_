"use client";

import {
  createContext,
  useContext,
  useCallback,
  useEffect,
  useState,
  ReactNode,
} from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { useToast } from "@/components/ui/Toast";
import { SiteSummaryJob } from "@/lib/types";
import { JobPollerRegistry } from "@/lib/job-poller";

const POLL_INTERVAL_MS = 3000;

interface SiteSummaryJobsContextValue {
  // Queues an "Analyze site" refresh and starts polling it. Resolves to the
  // job id when one was queued (or was already running), null on failure, so
  // the caller can show a spinner without owning the poll loop.
  startSiteSummaryJob: (competitorId: number) => Promise<number | null>;
  // Re-attach to a job this tab did not start, after a reload discarded the
  // poller watching it. See lib/active-jobs-rehydrator.tsx.
  trackSiteSummaryJob: (competitorId: number, jobId: number) => void;
  // Which competitors have a refresh in flight, so a competitor page can
  // disable its own button without tracking that state separately.
  runningFor: number[];
  // Bumps by one every time a job resolves, so the competitor page can
  // refetch the summary without its own polling loop.
  completedCount: number;
}

const SiteSummaryJobsContext = createContext<SiteSummaryJobsContextValue | null>(null);

export function useSiteSummaryJobs(): SiteSummaryJobsContextValue {
  const ctx = useContext(SiteSummaryJobsContext);
  if (!ctx) {
    throw new Error("useSiteSummaryJobs must be used within SiteSummaryJobsProvider");
  }
  return ctx;
}

export function SiteSummaryJobsProvider({ children }: { children: ReactNode }) {
  const { workspaceId } = useWorkspaceContext();
  const { push } = useToast();

  // One registry per provider instance, owning every interval — see
  // lib/job-poller.ts for the lifecycle rules (one poller per job, no
  // overlapping requests, stop on a terminal status, settle exactly once).
  const [registry] = useState(() => new JobPollerRegistry(POLL_INTERVAL_MS));

  const [runningFor, setRunningFor] = useState<number[]>([]);
  const [completedCount, setCompletedCount] = useState(0);

  // Every interval dies with the provider, or a remount leaves the previous
  // instance's intervals running with nothing holding a handle to them.
  useEffect(() => {
    return () => {
      registry.stopAll();
    };
  }, [registry]);

  const trackSiteSummaryJob = useCallback(
    (competitorId: number, jobId: number) => {
      if (!workspaceId) return;

      const started = registry.start<SiteSummaryJob>(
        jobId,
        () =>
          apiFetch(
            `/workspaces/${workspaceId}/competitors/${competitorId}/site-summary/jobs/${jobId}`
          ),
        (job) => {
          setRunningFor((ids) => ids.filter((id) => id !== competitorId));
          setCompletedCount((n) => n + 1);

          if (job.status === "success") {
            push({ tone: "success", message: "Site analysis updated" });
          } else {
            push({
              tone: "error",
              message: job.error || "Site analysis failed",
            });
          }
        }
      );

      // Only mark it running if this call actually started a poller, or a
      // repeat call would leave the competitor's button stuck disabled.
      if (started) {
        setRunningFor((ids) => (ids.includes(competitorId) ? ids : [...ids, competitorId]));
      }
    },
    [workspaceId, push, registry]
  );

  const startSiteSummaryJob = useCallback(
    async (competitorId: number): Promise<number | null> => {
      if (!workspaceId) return null;
      try {
        // 202 for a new job, 200 when one was already in flight — either way
        // the body is the job to poll, so both are handled the same.
        const job: SiteSummaryJob = await apiFetch(
          `/workspaces/${workspaceId}/competitors/${competitorId}/site-summary/refresh`,
          { method: "POST" }
        );
        trackSiteSummaryJob(competitorId, job.id);
        return job.id;
      } catch (err) {
        push({
          tone: "error",
          message:
            err instanceof ApiError ? err.message : "Failed to start site analysis",
        });
        return null;
      }
    },
    [workspaceId, trackSiteSummaryJob, push]
  );

  return (
    <SiteSummaryJobsContext.Provider
      value={{ startSiteSummaryJob, trackSiteSummaryJob, runningFor, completedCount }}
    >
      {children}
    </SiteSummaryJobsContext.Provider>
  );
}
