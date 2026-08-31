"use client";

import {
  createContext,
  useContext,
  useCallback,
  useEffect,
  useState,
  ReactNode,
} from "react";
import { apiFetch } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { useToast } from "@/components/ui/Toast";
import { CompetitorDiscoveryJob } from "@/lib/types";
import { JobPollerRegistry } from "@/lib/job-poller";

const POLL_INTERVAL_MS = 3000;

interface CompetitorDiscoveryJobsContextValue {
  // Starts polling a discovery job returned by POST /competitors. The add
  // request itself no longer waits for discovery — see
  // services/competitor_discovery_service.py. Safe to call more than once for
  // the same job: the registry keeps it to one poller.
  trackDiscoveryJob: (competitorId: number, jobId: number) => void;
  // How many discovery jobs are still running, so the dashboard can label them
  // rather than leaving the add button spinning.
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

  // One registry per provider instance, owning every interval. The lifecycle
  // rules — one poller per job id, no overlapping requests, stop on a terminal
  // status — live in lib/job-poller.ts rather than in this component, so they
  // are enforced in one place and testable without a DOM.
  //
  // useState with a lazy initializer rather than a ref: the instance must be
  // built exactly once and stay stable across rerenders, and reading it during
  // render is legitimate, where reading ref.current during render is not.
  const [registry] = useState(() => new JobPollerRegistry(POLL_INTERVAL_MS));

  const [discoveringCount, setDiscoveringCount] = useState(0);
  const [completedCount, setCompletedCount] = useState(0);

  // Every interval dies with the provider. Without this, a remount — dev HMR,
  // React Strict Mode's double mount, navigating away and back — left the
  // previous instance's intervals running with nothing holding a handle to
  // them, so they kept requesting the same job forever. `registry` never
  // changes identity, so this dependency is stable and the effect runs once.
  useEffect(() => {
    return () => {
      registry.stopAll();
    };
  }, [registry]);

  const trackDiscoveryJob = useCallback(
    (competitorId: number, jobId: number) => {
      if (!workspaceId) return;

      const started = registry.start<CompetitorDiscoveryJob>(
        jobId,
        () =>
          apiFetch(
            `/workspaces/${workspaceId}/competitors/${competitorId}/discovery-jobs/${jobId}`
          ),
        (job) => {
          setDiscoveringCount((n) => Math.max(0, n - 1));
          setCompletedCount((n) => n + 1);

          if (job.status === "success") {
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
          } else {
            push({
              tone: "error",
              message: job.error || "Page discovery failed",
            });
          }
        }
      );

      // Only count a job this call actually started. A repeat call for a job
      // already being polled must not inflate the indicator, which would leave
      // "Discovering pages for 3 competitors..." stuck on screen.
      if (started) setDiscoveringCount((n) => n + 1);
    },
    [workspaceId, push, registry]
  );

  return (
    <CompetitorDiscoveryJobsContext.Provider
      value={{ trackDiscoveryJob, discoveringCount, completedCount }}
    >
      {children}
    </CompetitorDiscoveryJobsContext.Provider>
  );
}
