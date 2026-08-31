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
import { BattlecardUpdateJob } from "@/lib/types";
import { JobPollerRegistry } from "@/lib/job-poller";

const POLL_INTERVAL_MS = 3000;

interface StartBattlecardUpdateJobPayload {
  competitorId: number;
  change_log_ids: number[];
}

interface BattlecardJobsContextValue {
  startBattlecardUpdateJob: (payload: StartBattlecardUpdateJobPayload) => Promise<boolean>;
  // Competitor ids with a proposal currently generating — lets the page show
  // a "Generating..." state on the right competitor's card instead of a
  // generic global count.
  activeCompetitorIds: number[];
  // Bumps by one every time any job resolves (success or failure) — pages
  // showing battlecard content can watch this in a useEffect to refetch
  // without each one needing its own polling loop.
  completedCount: number;
}

const BattlecardJobsContext = createContext<BattlecardJobsContextValue | null>(null);

export function useBattlecardJobs(): BattlecardJobsContextValue {
  const ctx = useContext(BattlecardJobsContext);
  if (!ctx) {
    throw new Error("useBattlecardJobs must be used within BattlecardJobsProvider");
  }
  return ctx;
}

export function BattlecardJobsProvider({ children }: { children: ReactNode }) {
  const { workspaceId } = useWorkspaceContext();
  const { push } = useToast();

  // One registry per provider instance, owning every interval. See
  // lib/job-poller.ts: it keeps polling to one request per job at a time,
  // stops on a terminal status, and settles each job exactly once.
  const [registry] = useState(() => new JobPollerRegistry(POLL_INTERVAL_MS));

  const [activeByJob, setActiveByJob] = useState<Record<number, number>>({});
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
    (wsId: number, competitorId: number, jobId: number) => {
      const started = registry.start<BattlecardUpdateJob>(
        jobId,
        () =>
          apiFetch(
            `/workspaces/${wsId}/competitors/${competitorId}/battlecard/updates/jobs/${jobId}`
          ),
        (job) => {
          setActiveByJob((prev) => {
            const next = { ...prev };
            delete next[jobId];
            return next;
          });
          setCompletedCount((n) => n + 1);

          if (job.status === "success") {
            push({
              tone: "success",
              message: "Battlecard update ready — sent to approval queue",
              href: "/approvals",
            });
          } else {
            push({
              tone: "error",
              message: job.error || "Battlecard update generation failed",
            });
          }
        }
      );

      // Only mark the competitor busy if this call actually started a poller,
      // so a repeat call can't strand a card on "Generating...".
      if (started) setActiveByJob((prev) => ({ ...prev, [jobId]: competitorId }));
    },
    [push, registry]
  );

  const startBattlecardUpdateJob = useCallback(
    async ({ competitorId, change_log_ids }: StartBattlecardUpdateJobPayload): Promise<boolean> => {
      if (!workspaceId) return false;
      try {
        const job: BattlecardUpdateJob = await apiFetch(
          `/workspaces/${workspaceId}/competitors/${competitorId}/battlecard/updates`,
          { method: "POST", body: JSON.stringify({ change_log_ids }) }
        );
        pollJob(workspaceId, competitorId, job.id);
        return true;
      } catch (err) {
        push({
          tone: "error",
          message: err instanceof ApiError ? err.message : "Failed to propose battlecard update",
        });
        return false;
      }
    },
    [workspaceId, pollJob, push]
  );

  // Only read during render (`.includes` on the battlecards page), never as a
  // hook dependency, so a fresh array identity each render is harmless.
  const activeCompetitorIds = Array.from(new Set(Object.values(activeByJob)));

  return (
    <BattlecardJobsContext.Provider
      value={{ startBattlecardUpdateJob, activeCompetitorIds, completedCount }}
    >
      {children}
    </BattlecardJobsContext.Provider>
  );
}
