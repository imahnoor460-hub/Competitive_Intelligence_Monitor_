"use client";

import { useEffect, useState, useCallback } from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { ApprovalItem, BattlecardUpdate, Briefing } from "@/lib/types";

function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const hours = Math.round(diffMs / 3600000);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h`;
  const days = Math.round(hours / 24);
  return `${days}d`;
}

const TYPE_LABELS: Record<string, string> = {
  briefing: "BRIEFING",
  battlecard_update: "BATTLECARD UPDATE",
  crm_note: "CRM NOTE",
};

export default function ApprovalsPage() {
  const { workspaceId, ready: contextReady, canApprove, refreshPendingApprovals } =
    useWorkspaceContext();

  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [briefingsById, setBriefingsById] = useState<Record<number, Briefing>>({});
  const [battlecardUpdatesById, setBattlecardUpdatesById] = useState<Record<number, BattlecardUpdate>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [noteOpen, setNoteOpen] = useState<Record<number, boolean>>({});
  const [deciding, setDeciding] = useState<Record<number, boolean>>({});
  const [decided, setDecided] = useState<Record<number, "approved" | "rejected">>({});

  const load = useCallback(async (wsId: number) => {
    try {
      const items: ApprovalItem[] = await apiFetch(`/workspaces/${wsId}/approvals/?status=pending`);
      setApprovals(items);

      const briefingItems = items.filter((i) => i.item_type === "briefing");
      const briefings = await Promise.all(
        briefingItems.map((i) => apiFetch(`/workspaces/${wsId}/briefings/${i.item_id}`))
      );
      const byId: Record<number, Briefing> = {};
      briefings.forEach((b: Briefing) => {
        byId[b.id] = b;
      });
      setBriefingsById(byId);

      const battlecardUpdateItems = items.filter((i) => i.item_type === "battlecard_update");
      const battlecardUpdates = await Promise.all(
        battlecardUpdateItems.map((i) => apiFetch(`/workspaces/${wsId}/battlecard-updates/${i.item_id}`))
      );
      const updatesById: Record<number, BattlecardUpdate> = {};
      battlecardUpdates.forEach((u: BattlecardUpdate) => {
        updatesById[u.id] = u;
      });
      setBattlecardUpdatesById(updatesById);
    } catch (err) {
      if (err instanceof ApiError) setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!workspaceId) return;
    void (async () => {
      await load(workspaceId);
    })();
  }, [workspaceId, load]);

  async function handleDecide(item: ApprovalItem, decision: "approve" | "reject") {
    if (!workspaceId) return;
    setDeciding((prev) => ({ ...prev, [item.id]: true }));
    try {
      await apiFetch(`/workspaces/${workspaceId}/approvals/${item.id}/${decision}`, {
        method: "POST",
        body: JSON.stringify({ notes: notes[item.id] || undefined }),
      });
      setDecided((prev) => ({ ...prev, [item.id]: decision === "approve" ? "approved" : "rejected" }));
      await refreshPendingApprovals();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Failed to ${decision}`);
    } finally {
      setDeciding((prev) => ({ ...prev, [item.id]: false }));
    }
  }

  if (!contextReady || loading) return null;

  return (
    <div className="flex flex-col gap-[18px] px-4 py-5 pb-10 sm:px-[34px] sm:py-[30px] sm:pb-[44px]" style={{ maxWidth: 860 }}>
      <div className="flex flex-col gap-[7px]">
        <h1 className="m-0 text-[22px] font-semibold tracking-[-0.025em] sm:text-[26px]">Approval queue</h1>
        <p className="m-0 max-w-[560px] text-[13.5px] text-[var(--text-muted)]">
          Nothing reaches Slack, email, or a CRM until a reviewer signs off here.
        </p>
      </div>

      {error && (
        <p className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</p>
      )}

      {approvals.length === 0 ? (
        <p className="text-sm text-[var(--text-faint)]">Nothing waiting for approval.</p>
      ) : (
        <div className="flex flex-col gap-[14px]">
          {approvals.map((item) => {
            const briefing = briefingsById[item.item_id];
            const battlecardUpdate = battlecardUpdatesById[item.item_id];
            const outcome = decided[item.id];

            return (
              <div
                key={item.id}
                className="flex flex-col gap-4 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-4 sm:px-[22px] sm:py-5"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2.5">
                    <span className="rounded-md bg-[var(--accent-wash)] px-2 py-0.5 font-mono text-[10px] tracking-wide text-[var(--accent)]">
                      {TYPE_LABELS[item.item_type] ?? item.item_type.toUpperCase()}
                    </span>
                    <span className="text-[14.5px] font-semibold tracking-tight">
                      {briefing?.title ??
                        (battlecardUpdate ? "Battlecard update" : `Item #${item.item_id}`)}
                    </span>
                  </div>
                  {briefing && (
                    <span className="font-mono text-[11px] text-[var(--text-dim)]">
                      {briefing.audience} &middot; {briefing.digest_type}
                    </span>
                  )}
                  {battlecardUpdate?.change_summary && (
                    <span className="font-mono text-[11px] text-[var(--text-dim)]">
                      {battlecardUpdate.change_summary}
                    </span>
                  )}
                </div>

                <div
                  className="rounded-r-lg border-l-2 px-3 py-3 sm:px-4"
                  style={{ borderColor: "var(--accent)", background: "var(--bg-nested)" }}
                >
                  <span className="font-mono text-[9.5px] uppercase tracking-[.13em] text-[var(--text-dim)]">
                    Preview of what will be sent
                  </span>
                  {briefing ? (
                    <p className="mt-2 whitespace-pre-wrap max-sm:break-words text-[13px] leading-[1.6] text-[var(--text-secondary)]">
                      {briefing.body_markdown}
                    </p>
                  ) : battlecardUpdate ? (
                    <p className="mt-2 whitespace-pre-wrap text-[13px] leading-[1.6] text-[var(--text-secondary)]">
                      {battlecardUpdate.proposed_content_markdown}
                    </p>
                  ) : (
                    <p className="mt-2 text-[13px] text-[var(--text-faint)]">
                      No preview available for this item type yet.
                    </p>
                  )}
                </div>

                <div className="flex flex-col items-start gap-3 border-t border-[var(--border-subtle)] pt-4 sm:flex-row sm:items-center sm:justify-between">
                  <span className="font-mono text-[11px] text-[var(--text-faint)]">
                    Drafted by the agent &middot; waiting {relativeTime(item.requested_at)}
                  </span>

                  {outcome ? (
                    <span
                      className="text-[12.5px] font-medium"
                      style={{ color: outcome === "approved" ? "var(--teal)" : "var(--red)" }}
                    >
                      {outcome === "approved" ? "Approved & sent" : "Rejected"}
                    </span>
                  ) : (
                    canApprove && (
                      <div className="flex items-center gap-2 max-sm:flex-wrap">
                        <button
                          onClick={() =>
                            setNoteOpen((prev) => ({ ...prev, [item.id]: !prev[item.id] }))
                          }
                          className="h-8 rounded-lg border border-[var(--border-input)] px-3 text-xs font-medium text-[var(--text-secondary)] hover:border-[var(--border-hover)]"
                        >
                          Add note
                        </button>
                        <button
                          onClick={() => handleDecide(item, "reject")}
                          disabled={deciding[item.id]}
                          className="h-8 rounded-lg border border-[var(--red)]/40 px-3 text-xs font-medium text-[var(--red)] disabled:opacity-50"
                        >
                          Reject
                        </button>
                        <button
                          onClick={() => handleDecide(item, "approve")}
                          disabled={deciding[item.id]}
                          className="h-8 rounded-lg bg-[var(--accent)] px-3 text-xs font-semibold text-[var(--accent-on)] disabled:opacity-50"
                        >
                          Approve &amp; send
                        </button>
                      </div>
                    )
                  )}
                </div>

                {noteOpen[item.id] && !outcome && (
                  <input
                    value={notes[item.id] ?? ""}
                    onChange={(e) => setNotes((prev) => ({ ...prev, [item.id]: e.target.value }))}
                    placeholder="Optional note, recorded with the decision"
                    className="rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-xs text-[var(--text-secondary)]"
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
