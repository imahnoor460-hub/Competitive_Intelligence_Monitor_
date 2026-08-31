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
import { BriefingAudience, BriefingDigestType, BriefingJob } from "@/lib/types";
import { JobPollerRegistry } from "@/lib/job-poller";

const POLL_INTERVAL_MS = 3000;

interface StartBriefingJobPayload {
  audience?: BriefingAudience;
  digest_type?: BriefingDigestType;
  change_log_ids: number[];
}

interface BriefingJobsContextValue {
  startBriefingJob: (payload: StartBriefingJobPayload) => Promise<boolean>;
  activeJobCount: number;
  // Bumps by one every time any job resolves (success or failure) — pages
  // that list briefings can watch this in a useEffect to refetch without
  // each one needing its own polling loop.
  completedCount: number;
}

const BriefingJobsContext = createContext<BriefingJobsContextValue | null>(null);

export function useBriefingJobs(): BriefingJobsContextValue {
  const ctx = useContext(BriefingJobsContext);
  if (!ctx) {
    throw new Error("useBriefingJobs must be used within BriefingJobsProvider");
  }
  return ctx;
}

export function BriefingJobsProvider({ children }: { children: ReactNode }) {
  const { workspaceId } = useWorkspaceContext();
  const { push } = useToast();

  // One registry per provider instance, owning every interval. See
  // lib/job-poller.ts: it keeps polling to one request per job at a time,
  // stops on a terminal status, and settles each job exactly once.
  const [registry] = useState(() => new JobPollerRegistry(POLL_INTERVAL_MS));

  const [activeJobCount, setActiveJobCount] = useState(0);
  const [completedCount, setCompletedCount] = useState(0);

  // Every interval dies with the provider. Without this, a remount left the
  // previous instance's intervals running with nothing holding a handle to
  // them.
  useEffect(() => {
    return () => {
      registry.stopAll();
    };
  }, [registry]);

  const pollJob = useCallback(
    (wsId: number, jobId: number) => {
      const started = registry.start<BriefingJob>(
        jobId,
        () => apiFetch(`/workspaces/${wsId}/briefings/jobs/${jobId}`),
        (job) => {
          setActiveJobCount((n) => Math.max(0, n - 1));
          setCompletedCount((n) => n + 1);

          if (job.status === "success") {
            push({
              tone: "success",
              message: "Briefing ready — sent to approval queue",
              href: "/approvals",
            });
          } else {
            push({
              tone: "error",
              message: job.error || "Briefing generation failed",
            });
          }
        }
      );

      // Only count a job this call actually started, so a repeat call can't
      // leave a phantom skeleton row on the briefings page.
      if (started) setActiveJobCount((n) => n + 1);
    },
    [push, registry]
  );

  const startBriefingJob = useCallback(
    async (payload: StartBriefingJobPayload): Promise<boolean> => {
      if (!workspaceId) return false;
      try {
        const job: BriefingJob = await apiFetch(
          `/workspaces/${workspaceId}/briefings/generate-now`,
          { method: "POST", body: JSON.stringify(payload) }
        );
        pollJob(workspaceId, job.id);
        return true;
      } catch (err) {
        push({
          tone: "error",
          message: err instanceof ApiError ? err.message : "Failed to start briefing generation",
        });
        return false;
      }
    },
    [workspaceId, pollJob, push]
  );

  return (
    <BriefingJobsContext.Provider value={{ startBriefingJob, activeJobCount, completedCount }}>
      {children}
    </BriefingJobsContext.Provider>
  );
}
