"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  ReactNode,
} from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { useToast } from "@/components/ui/Toast";
import { CheckRun, CheckSweep, SurfaceCheckResult } from "@/lib/types";
import { JobPollerRegistry } from "@/lib/job-poller";

const POLL_INTERVAL_MS = 3000;

/** What a check concluded, for the surface pages' inline status line. The
 * backend records this on the run (`outcome`) as well as returning it from an
 * inline check, so a worker-executed check reports the same thing. */
const OUTCOME_MESSAGES: Record<string, string> = {
  baseline_captured: "Baseline captured",
  no_change: "No change detected",
  change_detected: "Change detected",
};

export type SurfaceCheckState =
  | { state: "idle" }
  | { state: "checking" }
  | { state: "done"; message: string }
  | { state: "error"; message: string };

interface CheckJobsContextValue {
  /** Start a check of one surface, or attach to the one already running.
   * Resolves as soon as the request is accepted — the outcome arrives through
   * `surfaceCheckState`. */
  startSurfaceCheck: (competitorId: number, surfaceId: number) => Promise<void>;
  surfaceCheckState: (surfaceId: number) => SurfaceCheckState;

  /** Check every active surface in the workspace as one server-side sweep. */
  startCheckAll: () => Promise<void>;
  /** The sweep in flight, or the last one to finish. */
  sweep: CheckSweep | null;

  /** Re-attach to work started before this page loaded — see
   * lib/active-jobs-rehydrator.tsx. Safe to call repeatedly. */
  trackCheckRun: (run: CheckRun) => void;
  trackSweep: (sweep: CheckSweep) => void;

  /** Bumps every time a check or a sweep resolves, so pages showing surfaces
   * or change logs can refetch without their own polling loop. */
  completedCount: number;
}

const CheckJobsContext = createContext<CheckJobsContextValue | null>(null);

export function useCheckJobs(): CheckJobsContextValue {
  const ctx = useContext(CheckJobsContext);
  if (!ctx) {
    throw new Error("useCheckJobs must be used within CheckJobsProvider");
  }
  return ctx;
}

export function CheckJobsProvider({ children }: { children: ReactNode }) {
  const { workspaceId } = useWorkspaceContext();
  const { push } = useToast();

  // Two registries, not one. Both key their pollers by a plain job id, and a
  // check run and a sweep can trivially share the number 5 — a single registry
  // would treat them as the same job and silently refuse to poll the second.
  const [runRegistry] = useState(() => new JobPollerRegistry(POLL_INTERVAL_MS));
  const [sweepRegistry] = useState(() => new JobPollerRegistry(POLL_INTERVAL_MS));

  const [checkStates, setCheckStates] = useState<Record<number, SurfaceCheckState>>({});
  const [sweep, setSweep] = useState<CheckSweep | null>(null);
  const [completedCount, setCompletedCount] = useState(0);

  // Every interval dies with the provider, so a remount (dev HMR, Strict
  // Mode's double mount, navigating away and back) cannot leave the previous
  // instance's intervals running with nothing holding a handle to them.
  useEffect(() => {
    return () => {
      runRegistry.stopAll();
      sweepRegistry.stopAll();
    };
  }, [runRegistry, sweepRegistry]);

  const setSurfaceState = useCallback((surfaceId: number, state: SurfaceCheckState) => {
    setCheckStates((prev) => ({ ...prev, [surfaceId]: state }));
  }, []);

  const pollCheckRun = useCallback(
    (wsId: number, surfaceId: number, runId: number) => {
      const started = runRegistry.start<CheckRun>(
        runId,
        () => apiFetch(`/workspaces/${wsId}/check-runs/${runId}`),
        (run) => {
          setCompletedCount((n) => n + 1);

          if (run.status === "success") {
            setSurfaceState(surfaceId, {
              state: "done",
              // `outcome` is null on runs recorded before the column existed;
              // fall back to a plain confirmation rather than rendering null.
              message: OUTCOME_MESSAGES[run.outcome ?? ""] ?? "Check complete",
            });
          } else {
            setSurfaceState(surfaceId, {
              state: "error",
              message: run.error || "Check failed",
            });
          }
        }
      );

      if (started) setSurfaceState(surfaceId, { state: "checking" });
    },
    [runRegistry, setSurfaceState]
  );

  const trackCheckRun = useCallback(
    (run: CheckRun) => {
      if (!workspaceId) return;
      // Runs belonging to a sweep are reported through the sweep's own
      // progress counter. Polling each one individually would mean thirty
      // request streams for one "Run check now".
      if (run.sweep_id !== null) return;
      pollCheckRun(workspaceId, run.surface_id, run.id);
    },
    [workspaceId, pollCheckRun]
  );

  const startSurfaceCheck = useCallback(
    async (competitorId: number, surfaceId: number) => {
      if (!workspaceId) return;

      setSurfaceState(surfaceId, { state: "checking" });
      try {
        const result: SurfaceCheckResult = await apiFetch(
          `/workspaces/${workspaceId}/competitors/${competitorId}/surfaces/${surfaceId}/check`,
          { method: "POST" }
        );

        // One response shape, two paths: `queued` means a worker will do the
        // work and the outcome arrives by polling. `already_running` means a
        // scheduled check, or another tab, got there first — attach to that
        // run rather than reporting "already running" and going quiet, since
        // the user still wants the result. Anything else is already the
        // finished outcome, with nothing left to poll for.
        if (result.status === "queued" || result.status === "already_running") {
          pollCheckRun(workspaceId, surfaceId, result.check_run_id);
          return;
        }

        setSurfaceState(surfaceId, {
          state: "done",
          message: OUTCOME_MESSAGES[result.status] ?? result.status,
        });
        setCompletedCount((n) => n + 1);
      } catch (err) {
        setSurfaceState(surfaceId, {
          state: "error",
          message: err instanceof ApiError ? err.message : "Check failed",
        });
      }
    },
    [workspaceId, pollCheckRun, setSurfaceState]
  );

  const trackSweep = useCallback(
    (initial: CheckSweep) => {
      if (!workspaceId) return;

      // Show the counts we already have immediately — the first poll tick is a
      // whole interval away, and starting from nothing would read as though
      // the button had not worked.
      setSweep(initial);

      sweepRegistry.start<CheckSweep>(
        initial.id,
        () => apiFetch(`/workspaces/${workspaceId}/check-sweeps/${initial.id}`),
        (finished) => {
          setCompletedCount((n) => n + 1);

          if (finished.failed_count === 0) {
            push({
              tone: "success",
              message: `Checked ${finished.total} page${finished.total === 1 ? "" : "s"}`,
            });
          } else if (finished.status === "failed") {
            push({ tone: "error", message: "Every page check failed" });
          } else {
            // A partial failure is still a successful sweep — see
            // models/check_sweep.py — so it reports what got done, not just
            // that something went wrong.
            push({
              tone: "error",
              message: `Checked ${finished.finished - finished.failed_count} of ${
                finished.total
              } pages — ${finished.failed_count} failed`,
            });
          }
        },
        // Progress tick by tick, so the header counts up rather than sitting
        // on "Checking…" until the whole sweep lands.
        (progress) => setSweep(progress)
      );
    },
    [workspaceId, sweepRegistry, push]
  );

  const startCheckAll = useCallback(async () => {
    if (!workspaceId) return;

    try {
      const started: CheckSweep = await apiFetch(
        `/workspaces/${workspaceId}/check-all`,
        { method: "POST" }
      );

      // The backend closes an empty sweep on the spot rather than leaving it
      // queued with nothing that could ever finish it, so there is no terminal
      // status to poll for and nothing to report but the reason.
      if (started.total === 0 && started.status === "success") {
        push({ tone: "error", message: "No active pages to check" });
        setSweep(started);
        return;
      }

      trackSweep(started);
    } catch (err) {
      push({
        tone: "error",
        message: err instanceof ApiError ? err.message : "Failed to start checks",
      });
    }
  }, [workspaceId, trackSweep, push]);

  const surfaceCheckState = useCallback(
    (surfaceId: number): SurfaceCheckState => checkStates[surfaceId] ?? { state: "idle" },
    [checkStates]
  );

  return (
    <CheckJobsContext.Provider
      value={{
        startSurfaceCheck,
        surfaceCheckState,
        startCheckAll,
        sweep,
        trackCheckRun,
        trackSweep,
        completedCount,
      }}
    >
      {children}
    </CheckJobsContext.Provider>
  );
}
