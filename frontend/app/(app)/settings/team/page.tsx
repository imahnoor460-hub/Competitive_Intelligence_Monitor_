"use client";

import { useEffect, useState, FormEvent, useCallback } from "react";
import Link from "next/link";
import { apiFetch, ApiError } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { WorkspaceMember, WorkspaceRole } from "@/lib/types";

const ROLE_BADGE_STYLES: Record<WorkspaceRole, { color: string; background: string }> = {
  owner: { color: "var(--accent)", background: "var(--accent-wash)" },
  editor: { color: "var(--blue)", background: "#4d9fff1a" },
  reviewer: { color: "var(--violet)", background: "#9b7bff1a" },
};

function initialsFor(name: string | null, email: string) {
  const source = (name ?? email).trim();
  return source
    .split(/\s+/)
    .map((part) => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

export default function TeamSettingsPage() {
  const { workspaceId, workspace, role, isReadOnly, ready: contextReady } =
    useWorkspaceContext();
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<WorkspaceRole>("editor");
  const [inviting, setInviting] = useState(false);

  // The demo account is an owner in its workspace; member management is still
  // refused by the API for that session, so do not offer it.
  const isOwner = role === "owner" && !isReadOnly;

  const load = useCallback(async (wsId: number) => {
    try {
      const memberList = await apiFetch(`/workspaces/${wsId}/members`);
      setMembers(memberList);
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

  async function handleInvite(e: FormEvent) {
    e.preventDefault();
    if (!workspaceId || !inviteEmail.trim()) return;

    setError(null);
    setInviting(true);
    try {
      await apiFetch(`/workspaces/${workspaceId}/members`, {
        method: "POST",
        body: JSON.stringify({ email: inviteEmail, role: inviteRole }),
      });
      setInviteEmail("");
      await load(workspaceId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to invite member");
    } finally {
      setInviting(false);
    }
  }

  async function handleRoleChange(memberId: number, newRole: WorkspaceRole) {
    if (!workspaceId) return;

    try {
      await apiFetch(`/workspaces/${workspaceId}/members/${memberId}`, {
        method: "PATCH",
        body: JSON.stringify({ role: newRole }),
      });
      await load(workspaceId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update role");
    }
  }

  async function handleRemove(memberId: number) {
    if (!workspaceId) return;

    try {
      await apiFetch(`/workspaces/${workspaceId}/members/${memberId}`, { method: "DELETE" });
      await load(workspaceId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to remove member");
    }
  }

  if (!contextReady || loading) return null;

  return (
    <div className="flex flex-col gap-[18px] px-4 py-5 pb-10 sm:px-[34px] sm:py-[30px] sm:pb-[44px]" style={{ maxWidth: 780 }}>
      <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-end sm:justify-between sm:gap-6">
        <div className="flex flex-col gap-[7px]">
          <h1 className="m-0 text-[22px] font-semibold tracking-[-0.025em] sm:text-[26px]">Team</h1>
          <p className="m-0 max-w-[560px] text-[13.5px] text-[var(--text-muted)]">
            {workspace ? `Members of ${workspace.name}` : "Manage who has access to this workspace."}
          </p>
        </div>
        <div className="flex gap-[7px] max-sm:flex-wrap">
          <Link
            href="/settings/integrations"
            className="h-8 rounded-lg border border-[var(--border-input)] bg-[var(--bg-card)] px-3 text-xs font-medium leading-8 text-[var(--text-secondary)] hover:border-[var(--border-hover)]"
          >
            Integrations
          </Link>
          <Link
            href="/"
            className="h-8 rounded-lg border border-[var(--border-input)] bg-[var(--bg-card)] px-3 text-xs font-medium leading-8 text-[var(--text-secondary)] hover:border-[var(--border-hover)]"
          >
            Back to dashboard
          </Link>
        </div>
      </div>

      {error && (
        <p className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</p>
      )}

      {isOwner && (
        <div className="flex flex-col gap-4 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-4 sm:px-[22px] sm:py-5">
          <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">Invite a member</h2>
          <form onSubmit={handleInvite} className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex flex-1 flex-col gap-1.5">
              <label className="text-xs font-medium text-[var(--text-secondary)]">Email</label>
              <input
                type="email"
                required
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="teammate@company.com"
                className="rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-xs text-[var(--text-secondary)]"
              />
              <p className="m-0 text-[11px] text-[var(--text-faint)]">
                They need an existing account on this app already.
              </p>
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-[var(--text-secondary)]">Role</label>
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as WorkspaceRole)}
                className="rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-xs text-[var(--text-secondary)]"
              >
                <option value="editor">Editor</option>
                <option value="reviewer">Reviewer</option>
                <option value="owner">Owner</option>
              </select>
            </div>
            <button
              type="submit"
              disabled={inviting}
              className="h-8 w-full rounded-lg bg-[var(--accent)] px-3 text-xs font-semibold text-[var(--accent-on)] disabled:opacity-50 sm:w-auto"
            >
              {inviting ? "Inviting..." : "Invite"}
            </button>
          </form>
        </div>
      )}

      <div className="flex flex-col gap-4 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-4 sm:px-[22px] sm:py-5">
        <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">Members</h2>
        {members.length === 0 ? (
          <p className="text-sm text-[var(--text-faint)]">No members yet.</p>
        ) : (
          <div className="flex flex-col">
            {members.map((m, i) => {
              const badge = ROLE_BADGE_STYLES[m.role];
              return (
                <div
                  key={m.id}
                  className="flex flex-col items-start gap-2.5 py-[13px] sm:flex-row sm:items-center sm:justify-between sm:gap-4"
                  style={{
                    borderBottom: i < members.length - 1 ? "1px solid var(--border-subtler)" : undefined,
                  }}
                >
                  <div className="flex w-full min-w-0 items-center gap-3 sm:w-auto">
                    <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-[#242A34] font-mono text-[11px] font-semibold text-[var(--text-secondary)]">
                      {initialsFor(m.full_name, m.email) || "?"}
                    </div>
                    <div className="min-w-0">
                      <p className="m-0 truncate text-[13px] font-medium text-[var(--text-primary)]">
                        {m.full_name ?? m.email}
                      </p>
                      <p className="m-0 truncate text-[11.5px] text-[var(--text-faint)]">{m.email}</p>
                    </div>
                  </div>
                  {isOwner ? (
                    <div className="flex w-full shrink-0 items-center gap-2 sm:w-auto">
                      <select
                        value={m.role}
                        onChange={(e) => handleRoleChange(m.id, e.target.value as WorkspaceRole)}
                        className="flex-1 rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-2 py-1.5 text-xs text-[var(--text-secondary)] sm:flex-none"
                      >
                        <option value="owner">Owner</option>
                        <option value="editor">Editor</option>
                        <option value="reviewer">Reviewer</option>
                      </select>
                      <button
                        onClick={() => handleRemove(m.id)}
                        className="h-8 rounded-lg border border-[var(--red)]/40 px-3 text-xs font-medium text-[var(--red)]"
                      >
                        Remove
                      </button>
                    </div>
                  ) : (
                    <span
                      className="shrink-0 rounded-md px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide"
                      style={{ color: badge.color, background: badge.background }}
                    >
                      {m.role}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
