/**
 * Poll lifecycle for background jobs, kept out of React so the rules that
 * actually matter — one poller per job, no overlapping requests, stop on a
 * terminal status, always clean up — are enforceable and testable on their own.
 *
 * The bug this exists to prevent: a bare `setInterval(async () => …)` fires
 * again whether or not the previous request finished, so a poll slower than
 * the interval piles up concurrent in-flight requests for the same job, each
 * on its own socket. If several of those then come back terminal, the settle
 * handler runs once per response instead of once per job.
 */

export type JobStatus = "queued" | "running" | "success" | "failed";

/** Verified against the API: CompetitorDiscoveryJobStatus serializes to these
 * exact strings (queued | running | success | failed). */
const TERMINAL_STATUSES: readonly string[] = ["success", "failed"];

export function isTerminalStatus(status: string): boolean {
  return TERMINAL_STATUSES.includes(status);
}

interface Poller {
  intervalId: ReturnType<typeof setInterval>;
  /** A request is in flight; skip this tick rather than starting a second. */
  inFlight: boolean;
  /** Terminal status already seen; onSettled must run exactly once. */
  settled: boolean;
}

export class JobPollerRegistry {
  private readonly pollers = new Map<number, Poller>();
  // Assigned in the constructor body rather than as a parameter property:
  // parameter properties are not erasable syntax, and this module is executed
  // directly by Node's type-stripping test runner.
  private readonly intervalMs: number;

  constructor(intervalMs: number) {
    this.intervalMs = intervalMs;
  }

  get activeCount(): number {
    return this.pollers.size;
  }

  isPolling(jobId: number): boolean {
    return this.pollers.has(jobId);
  }

  /**
   * Begins polling `jobId`, unless it is already being polled.
   *
   * Returns true when a poller was actually created, so the caller can keep a
   * "jobs in flight" counter accurate — calling this twice for one job must
   * not increment it twice.
   */
  start<T extends { status: string }>(
    jobId: number,
    poll: () => Promise<T>,
    onSettled: (job: T) => void,
    /**
     * Called with every successful poll response, terminal or not, before
     * `onSettled`. Exists for jobs that report progress while they run — a
     * check sweep's `finished`/`total` is only useful mid-flight, and a
     * settle-only callback would show the count for the first time at the
     * moment it stops mattering.
     */
    onUpdate?: (job: T) => void
  ): boolean {
    // Idempotent by job id. This is what makes a repeated call, a rerender, or
    // the same job arriving in two state updates harmless.
    if (this.pollers.has(jobId)) return false;

    const poller: Poller = {
      intervalId: undefined as unknown as ReturnType<typeof setInterval>,
      inFlight: false,
      settled: false,
    };

    poller.intervalId = setInterval(() => {
      // Skip rather than stack: a poll slower than intervalMs must not start a
      // second request for the same job.
      if (poller.inFlight || poller.settled) return;
      poller.inFlight = true;

      void poll()
        .then((job) => {
          // A second terminal response can't re-settle the job.
          if (poller.settled) return;

          // Progress first: the terminal response carries the final counts,
          // so settling without reporting it would leave the last tick's
          // stale numbers on screen.
          onUpdate?.(job);

          if (!isTerminalStatus(job.status)) return;

          poller.settled = true;
          this.stop(jobId);
          onSettled(job);
        })
        .catch(() => {
          // A transient failure isn't worth surfacing — the next tick retries.
          // Deliberately does not stop the poller, and `finally` below still
          // clears inFlight so an error can't wedge it permanently.
        })
        .finally(() => {
          poller.inFlight = false;
        });
    }, this.intervalMs);

    this.pollers.set(jobId, poller);
    return true;
  }

  stop(jobId: number): void {
    const poller = this.pollers.get(jobId);
    if (poller === undefined) return;

    clearInterval(poller.intervalId);
    this.pollers.delete(jobId);
  }

  /** Clears every interval this registry owns. The provider calls this from its
   * unmount cleanup, so intervals cannot outlive the component that made
   * them. */
  stopAll(): void {
    for (const jobId of [...this.pollers.keys()]) {
      this.stop(jobId);
    }
  }
}
