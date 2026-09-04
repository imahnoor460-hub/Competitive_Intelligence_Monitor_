"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { apiFetch } from "@/lib/api";
import { useCheckJobs } from "@/lib/check-jobs-context";
import { ChangeLog, Competitor } from "@/lib/types";
import { MenuIcon } from "./NavIcons";

const MAX_RESULTS_PER_GROUP = 5;

/** The API serializes naive UTC datetimes with no timezone designator, which
 * `new Date()` reads as *local* time — an offset-hours-wrong "last sweep"
 * label for everyone outside UTC. Append the designator when it is missing. */
function parseUtc(value: string): Date {
  return new Date(/(Z|[+-]\d\d:?\d\d)$/.test(value) ? value : `${value}Z`);
}

function snippet(text: string | null, query: string): string {
  if (!text) return "";
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return text.slice(0, 80);
  const start = Math.max(0, idx - 20);
  return `${start > 0 ? "…" : ""}${text.slice(start, start + 80)}`;
}

export default function Header({ onOpenMenu }: { onOpenMenu: () => void }) {
  const router = useRouter();
  const { workspaceId, workspace, isDemo, isReadOnly, refreshPendingApprovals } =
    useWorkspaceContext();
  const { startCheckAll, sweep, completedCount } = useCheckJobs();
  // Derived from the sweep row rather than stamped into state when one
  // settles: the server already records when the sweep finished, and reading
  // it back means the label is right after a reload too.
  const lastSweep = sweep?.finished_at
    ? parseUtc(sweep.finished_at).toLocaleTimeString()
    : null;

  // A sweep is the server's record now, so "still going" is read off the row
  // rather than tracked in this component — which is what lets the label
  // survive a reload and keep counting where it left off.
  const running =
    sweep !== null && (sweep.status === "queued" || sweep.status === "running");

  const [competitors, setCompetitors] = useState<Competitor[]>([]);
  const [changeLogs, setChangeLogs] = useState<ChangeLog[]>([]);
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const searchBoxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    (async () => {
      try {
        const [comps, logs] = await Promise.all([
          apiFetch(`/workspaces/${workspaceId}/competitors/`),
          apiFetch(`/workspaces/${workspaceId}/change-logs/`),
        ]);
        if (!cancelled) {
          setCompetitors(comps);
          setChangeLogs(logs);
        }
      } catch {
        // Search index is best-effort — leave it empty if this fails.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (searchBoxRef.current && !searchBoxRef.current.contains(e.target as Node)) {
        setSearchOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  function competitorName(id: number) {
    return competitors.find((c) => c.id === id)?.name ?? `#${id}`;
  }

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return { competitors: [], changes: [] as ChangeLog[] };

    const matchedCompetitors = competitors
      .filter((c) => c.name.toLowerCase().includes(q))
      .slice(0, MAX_RESULTS_PER_GROUP);

    const matchedChanges = changeLogs
      .filter((l) => {
        const haystack = [
          competitorName(l.competitor_id),
          l.classification ?? "",
          l.rationale ?? "",
          l.diff ?? "",
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(q);
      })
      .slice(0, MAX_RESULTS_PER_GROUP);

    return { competitors: matchedCompetitors, changes: matchedChanges };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, competitors, changeLogs]);

  function goToCompetitor(id: number) {
    setQuery("");
    setSearchOpen(false);
    router.push(`/competitors/${id}`);
  }

  function goToChange(log: ChangeLog) {
    setQuery("");
    setSearchOpen(false);
    router.push(`/feed?highlight=${log.id}`);
  }

  const hasResults = results.competitors.length > 0 || results.changes.length > 0;

  // One POST, and the server fans the work out. This replaced a sequential
  // loop that ran here in the browser — fetch competitors, then surfaces, then
  // a blocking check per surface, one after another — which meant nothing on
  // the server knew a sweep was happening and closing the tab abandoned it
  // halfway with no record of what had been missed.
  async function handleRunCheckNow() {
    await startCheckAll();
  }

  // A finished check is the moment approvals may have appeared: every check
  // that finds something material queues one.
  useEffect(() => {
    if (completedCount === 0) return;
    void refreshPendingApprovals();
  }, [completedCount, refreshPendingApprovals]);

  return (
    // Portrait phones get two rows — status and workspace badge above, search
    // and the sweep button below — because the desktop row needs ~700px to
    // sit flat. The `sm:contents` wrappers dissolve at 640px so that from
    // there up every child is a direct flex item of this header again, and
    // `sm:order-*` puts them back in the desktop order the markup no longer
    // matches.
    <header className="sticky top-0 z-30 flex flex-col gap-2 border-b border-[var(--border-subtle)] bg-[var(--bg-page)]/90 px-4 py-2.5 backdrop-blur sm:h-[62px] sm:flex-row sm:items-center sm:gap-[18px] sm:px-[34px] sm:py-0">
      <div className="flex min-w-0 items-center gap-2.5 sm:contents">
        <button
          type="button"
          onClick={onOpenMenu}
          aria-label="Open menu"
          className="flex h-[34px] w-[34px] flex-shrink-0 items-center justify-center rounded-[9px] border border-[var(--border-input)] bg-[var(--bg-card)] text-[var(--text-secondary)] sm:hidden"
        >
          <MenuIcon />
        </button>
        <div className="flex min-w-0 flex-1 items-center gap-[9px] sm:order-1 sm:min-w-[auto] sm:flex-initial">
          <span
            className="h-1.5 w-1.5 rounded-full bg-[var(--teal)] max-sm:flex-shrink-0"
            style={{ animation: "pulseDot 2.4s ease-in-out infinite" }}
          />
          <span className="truncate font-mono text-[11px] tracking-wide text-[var(--text-muted)] sm:overflow-visible sm:whitespace-normal sm:text-clip">
            Agent live &middot; last sweep {lastSweep ?? "not yet run"}
          </span>
        </div>
        <div className="flex h-[34px] flex-shrink-0 items-center gap-2 self-start rounded-[9px] border border-[var(--accent-border)] bg-[var(--accent-wash)] px-3 sm:order-4 sm:shrink sm:self-auto">
          <span className="font-mono text-[10.5px] uppercase tracking-[.1em] text-[var(--accent)]">
            {isReadOnly
              ? "demo · read-only"
              : isDemo
                ? `demo · ${workspace?.role ?? "member"}`
                : workspace?.role ?? "member"}
          </span>
        </div>
      </div>
      <div className="hidden flex-1 sm:block sm:order-2" />
      {/* Row two on mobile: the search field and the sweep button. */}
      <div className="flex items-center gap-2 sm:contents">
        <div ref={searchBoxRef} className="relative min-w-0 flex-1 sm:order-3 sm:w-[270px] sm:min-w-[auto] sm:flex-initial">
          <div className="flex h-[34px] items-center gap-[9px] rounded-[9px] border border-[var(--border-input)] bg-[var(--bg-input)] px-[13px]">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" className="flex-shrink-0">
              <circle cx="7" cy="7" r="4.8" stroke="var(--text-dim)" strokeWidth="1.4" />
              <path d="M10.6 10.6L14 14" stroke="var(--text-dim)" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSearchOpen(true);
              }}
              onFocus={() => setSearchOpen(true)}
              placeholder="Search changes, competitors…"
              className="w-full bg-transparent text-[12.5px] text-[var(--text-secondary)] outline-none placeholder:text-[var(--text-dim)]"
            />
          </div>
          {searchOpen && query.trim() !== "" && (
            <div className="absolute left-0 right-0 top-[40px] z-20 max-h-[360px] overflow-y-auto rounded-[9px] border border-[var(--border-default)] bg-[var(--bg-card)] p-1.5 shadow-lg">
              {!hasResults ? (
                <p className="px-2.5 py-2 text-[12px] text-[var(--text-faint)]">No matches.</p>
              ) : (
                <>
                  {results.competitors.length > 0 && (
                    <div className="flex flex-col gap-0.5">
                      <div className="px-2.5 pb-1 pt-1 font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dimmer)]">
                        Competitors
                      </div>
                      {results.competitors.map((c) => (
                        <button
                          key={c.id}
                          onClick={() => goToCompetitor(c.id)}
                          className="rounded-md px-2.5 py-1.5 text-left text-[12.5px] text-[var(--text-secondary)] hover:bg-[var(--bg-nested)]"
                        >
                          {c.name}
                        </button>
                      ))}
                    </div>
                  )}
                  {results.changes.length > 0 && (
                    <div className="flex flex-col gap-0.5">
                      <div className="px-2.5 pb-1 pt-1 font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dimmer)]">
                        Changes
                      </div>
                      {results.changes.map((log) => (
                        <button
                          key={log.id}
                          onClick={() => goToChange(log)}
                          className="flex flex-col gap-0.5 rounded-md px-2.5 py-1.5 text-left hover:bg-[var(--bg-nested)]"
                        >
                          <span className="text-[12.5px] text-[var(--text-secondary)]">
                            {competitorName(log.competitor_id)}
                          </span>
                          <span className="truncate font-mono text-[11px] text-[var(--text-dim)]">
                            {snippet(log.rationale ?? log.diff, query)}
                          </span>
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
        <button
          onClick={handleRunCheckNow}
          // Hidden rather than disabled would lose the explanation; the API
          // refuses this for a demo workspace either way.
          disabled={running || !workspaceId || isReadOnly}
          title={isReadOnly ? "Checks are disabled in the read-only demo" : undefined}
          className="h-[34px] flex-shrink-0 rounded-[9px] bg-[var(--accent)] px-[15px] text-[12.5px] font-semibold text-[var(--accent-on)] disabled:opacity-50 sm:order-5 sm:shrink"
        >
          {running ? "Checking..." : "Run check now"}
        </button>
      </div>
    </header>
  );
}
