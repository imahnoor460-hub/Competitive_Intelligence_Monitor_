"use client";

import { useEffect, useState, useCallback, FormEvent } from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { useWorkspaceContext } from "@/lib/workspace-context";
import { Competitor, ResponseLibraryItem } from "@/lib/types";

export default function ResponseLibraryPage() {
  const { workspaceId, ready: contextReady, canEdit } = useWorkspaceContext();

  const [items, setItems] = useState<ResponseLibraryItem[]>([]);
  const [competitors, setCompetitors] = useState<Competitor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [tags, setTags] = useState("");
  const [competitorId, setCompetitorId] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async (wsId: number) => {
    try {
      const [libraryItems, comps] = await Promise.all([
        apiFetch(`/workspaces/${wsId}/response-library/`),
        apiFetch(`/workspaces/${wsId}/competitors/`),
      ]);
      setItems(libraryItems);
      setCompetitors(comps);
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

  function competitorName(id: number | null) {
    if (id === null) return null;
    return competitors.find((c) => c.id === id)?.name ?? `#${id}`;
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!workspaceId || !title.trim() || !body.trim()) return;

    setSubmitting(true);
    setError(null);
    try {
      await apiFetch(`/workspaces/${workspaceId}/response-library/`, {
        method: "POST",
        body: JSON.stringify({
          title,
          body_markdown: body,
          competitor_id: competitorId ? Number(competitorId) : null,
          tags: tags
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean),
        }),
      });
      setTitle("");
      setBody("");
      setTags("");
      setCompetitorId("");
      await load(workspaceId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create item");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: number) {
    if (!workspaceId) return;
    try {
      await apiFetch(`/workspaces/${workspaceId}/response-library/${id}`, { method: "DELETE" });
      await load(workspaceId);
    } catch (err) {
      if (err instanceof ApiError) setError(err.message);
    }
  }

  if (!contextReady || loading) return null;

  return (
    <div className="flex flex-col gap-[18px] px-4 py-5 pb-10 sm:px-[34px] sm:py-[30px] sm:pb-[44px]" style={{ maxWidth: 1000 }}>
      <div className="flex flex-col gap-[7px]">
        <h1 className="m-0 text-[22px] font-semibold tracking-[-0.025em] sm:text-[26px]">Response library</h1>
        <p className="m-0 max-w-[560px] text-[13.5px] text-[var(--text-muted)]">
          Reusable, pre-approved answers your team can drop straight into a conversation.
        </p>
      </div>

      {error && (
        <p className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</p>
      )}

      {canEdit && (
        <div className="flex flex-col gap-4 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-4 sm:px-[22px] sm:py-5">
          <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">Add a response</h2>
          <form onSubmit={handleCreate} className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-[var(--text-secondary)]">Title</label>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                required
                className="rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-xs text-[var(--text-secondary)]"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-[var(--text-secondary)]">Body</label>
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                required
                rows={4}
                className="rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-xs text-[var(--text-secondary)]"
              />
            </div>
            <div className="flex flex-col gap-3 sm:flex-row">
              <div className="flex flex-1 flex-col gap-1.5">
                <label className="text-xs font-medium text-[var(--text-secondary)]">
                  Competitor (optional)
                </label>
                <select
                  value={competitorId}
                  onChange={(e) => setCompetitorId(e.target.value)}
                  className="rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-xs text-[var(--text-secondary)]"
                >
                  <option value="">General</option>
                  {competitors.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex flex-1 flex-col gap-1.5">
                <label className="text-xs font-medium text-[var(--text-secondary)]">
                  Tags (comma-separated)
                </label>
                <input
                  value={tags}
                  onChange={(e) => setTags(e.target.value)}
                  placeholder="pricing, objection-handling"
                  className="rounded-lg border border-[var(--border-input)] bg-[var(--bg-input)] px-3 py-2 text-xs text-[var(--text-secondary)]"
                />
              </div>
            </div>
            <button
              type="submit"
              disabled={submitting}
              className="h-8 w-full rounded-lg bg-[var(--accent)] px-3 text-xs font-semibold text-[var(--accent-on)] disabled:opacity-50 sm:w-fit"
            >
              {submitting ? "Saving..." : "Save"}
            </button>
          </form>
        </div>
      )}

      <div className="flex flex-col gap-3">
        <h2 className="m-0 text-[14.5px] font-semibold tracking-[-0.01em]">All responses</h2>
        {items.length === 0 ? (
          <p className="text-sm text-[var(--text-faint)]">No saved responses yet.</p>
        ) : (
          <div className="flex flex-col gap-[14px]">
            {items.map((item) => (
              <div
                key={item.id}
                className="flex flex-col gap-3 rounded-[14px] border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-4 sm:px-[22px] sm:py-5"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[14.5px] font-semibold tracking-tight">{item.title}</span>
                  {canEdit && (
                    <button
                      onClick={() => handleDelete(item.id)}
                      className="h-8 rounded-lg border border-[var(--red)]/40 px-3 text-xs font-medium text-[var(--red)]"
                    >
                      Delete
                    </button>
                  )}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {competitorName(item.competitor_id) && (
                    <span className="rounded-md bg-[var(--bg-nested)] px-2 py-0.5 font-mono text-[10px] text-[var(--text-muted)]">
                      {competitorName(item.competitor_id)}
                    </span>
                  )}
                  {item.tags?.map((tag) => (
                    <span
                      key={tag}
                      className="rounded-md bg-[var(--bg-nested)] px-2 py-0.5 font-mono text-[10px] text-[var(--text-muted)]"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
                <p className="whitespace-pre-wrap max-sm:break-words text-[13px] leading-[1.6] text-[var(--text-secondary)]">
                  {item.body_markdown}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
