"use client";

import { createContext, useContext, useCallback, useRef, useState, ReactNode } from "react";
import { apiFetch } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { useToast } from "@/components/ui/Toast";
import { CompetitorDiscoveryJob } from "@/lib/types";

const POLL_INTERVAL_MS = 3000;

interface CompetitorDiscoveryJobsContextValue {
  // Starts polling a discovery job returned by POST /competitors. The add
  // request itself no longer waits for discovery — see
  // services/competitor_discovery_service.py.
  trackDiscoveryJob: (competitorId: number, jobId: number) => void;
  // Names of competitors whose pages are still being discovered, so the
  // dashboard can label them rather than leaving the add button spinning.
  discoveringCount: number;
  // Bumps by one every time a discovery job resolves, so pages listing
  // competitors or surfaces can refetch without their own polling loop.
  completedCount: number;
}

const CompetitorDiscoveryJobsContext =
  createContext<CompetitorDiscoveryJobsContextValue | null>(null);

export function useCompetitorDiscoveryJobs(): CompetitorDiscoveryJobsContextValue {
  const ctx = useContext(CompetitorDiscoveryJobsContext);
  if (!ctx) {
    throw new Error(
      "useCompetitorDiscoveryJobs must be used within CompetitorDiscoveryJobsProvider"
    );
  }
  return ctx;
}

export function CompetitorDiscoveryJobsProvider({ children }: { children: ReactNode }) {
  const { workspaceId } = useWorkspaceContext();
  const { push } = useToast();
  // Same shape as briefing-jobs-context: one setInterval per job id, kept in
  // a ref so polling survives client-side navigation (this provider is
  // mounted above the page shell) and is cleared exactly once on resolve.
  const pollersRef = useRef<Record<number, ReturnType<typeof setInterval>>>({});
  const [discoveringCount, setDiscoveringCount] = useState(0);
  const [completedCount, setCompletedCount] = useState(0);

  const trackDiscoveryJob = useCallback(
    (competitorId: number, jobId: number) => {
      if (!workspaceId) return;
      if (pollersRef.current[jobId]) return;

      setDiscoveringCount((n) => n + 1);

      const resolve = () => {
        clearInterval(pollersRef.current[jobId]);
        delete pollersRef.current[jobId];
        setDiscoveringCount((n) => Math.max(0, n - 1));
        setCompletedCount((n) => n + 1);
      };

      const interval = setInterval(async () => {
        try {
          const job: CompetitorDiscoveryJob = await apiFetch(
            `/workspaces/${workspaceId}/competitors/${competitorId}/discovery-jobs/${jobId}`
          );
          if (job.status === "success") {
            resolve();
            push({
              tone: "success",
              message:
                job.surfaces_discovered > 0
                  ? `Found ${job.surfaces_discovered} page${
                      job.surfaces_discovered === 1 ? "" : "s"
                    } to watch`
                  : "No pages could be auto-detected — add them manually",
              href: `/competitors/${competitorId}`,
            });
          } else if (job.status === "failed") {
            resolve();
            push({
              tone: "error",
              message: job.error || "Page discovery failed",
            });
          }
        } catch {
          // A transient poll failure (network blip) isn't worth surfacing —
          // the next tick will just try again.
        }
      }, POLL_INTERVAL_MS);

      pollersRef.current[jobId] = interval;
    },
    [workspaceId, push]
  );

  return (
    <CompetitorDiscoveryJobsContext.Provider
      value={{ trackDiscoveryJob, discoveringCount, completedCount }}
    >
      {children}
    </CompetitorDiscoveryJobsContext.Provider>
  );
}
