"use client";

import { useState } from "react";
import { BaselineFact, ChangeItem, ChangeItemType, ChangeLog, Snapshot, Surface, SurfaceType } from "@/lib/types";
import ClassificationBadge, { classificationColor } from "@/components/ui/ClassificationBadge";
import { surfaceDisplayName } from "@/lib/surface-name";

// Mirrors ClassificationBadge's color set (accent=pricing_move, blue=new_feature,
// violet=positioning_shift, teal=hiring_signal, red=promotion) so surface types
// and change classifications read as one consistent color system.
export const SURFACE_TYPE_STYLES: Record<SurfaceType, { color: string; label: string }> = {
  pricing: { color: "var(--accent)", label: "Pricing" },
  product: { color: "var(--blue)", label: "Product" },
  changelog: { color: "var(--violet)", label: "Changelog" },
  blog: { color: "var(--red)", label: "Blog" },
  jobs: { color: "var(--teal)", label: "Jobs" },
  other: { color: "var(--text-muted)", label: "Other" },
};

export function HeadingDot({ color = "var(--accent)" }: { color?: string }) {
  return <span className="h-2 w-2 flex-shrink-0 rounded-sm" style={{ background: color }} />;
}

function HighlightList({ items, color }: { items: string[]; color: string }) {
  return (
    <ul className="m-0 mt-2 flex flex-col gap-1 pl-0 text-[12.5px] leading-[1.5] text-[var(--text-secondary)]">
      {items.map((item, i) => (
        <li key={i} className="flex items-start gap-2">
          <span className="mt-[6px] h-1.5 w-1.5 flex-shrink-0 rounded-full" style={{ background: color }} />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

// Same color-reuse rule as SURFACE_TYPE_STYLES: teal/red/blue/accent are the
// existing classification colors, just applied to item-level change types.
const CHANGE_TYPE_STYLES: Record<ChangeItemType, { color: string; label: (pct: number | null) => string }> = {
  price_drop: { color: "var(--teal)", label: (pct) => (pct !== null ? `↓ ${Math.round(Math.abs(pct))}%` : "↓ Lower") },
  price_increase: { color: "var(--red)", label: (pct) => (pct !== null ? `↑ ${Math.round(Math.abs(pct))}%` : "↑ Higher") },
  new: { color: "var(--blue)", label: () => "New" },
  removed: { color: "var(--red)", label: () => "Removed" },
  policy: { color: "var(--accent)", label: () => "Policy" },
  other: { color: "var(--text-muted)", label: () => "Changed" },
};

const CHANGE_TYPE_TILE_LABELS: Record<ChangeItemType, string> = {
  price_drop: "Price drops",
  price_increase: "Price increases",
  new: "New items",
  removed: "Removed",
  policy: "Policy edits",
  other: "Other changes",
};

function CardHeader({
  title,
  pillLabel,
  pillColor,
  url,
  createdAt,
}: {
  title: string;
  pillLabel: string;
  pillColor: string;
  url: string | null;
  createdAt: string | null;
}) {
  const dt = createdAt ? new Date(createdAt) : null;
  return (
    // The date sits beside the headline on desktop; on a phone that leaves the
    // headline a ~110px column, so it wraps underneath instead.
    <div className="mb-3 flex flex-wrap items-start justify-between gap-2 max-sm:gap-y-1.5">
      <div className="flex min-w-0 flex-col gap-1 max-sm:w-full">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[15px] font-bold text-[var(--text-primary)] max-sm:min-w-0 max-sm:break-words sm:text-[16px]">
            {title}
          </span>
          <span
            className="flex-shrink-0 rounded-full px-2.5 py-1 text-[12px] font-medium"
            style={{
              background: `color-mix(in srgb, ${pillColor} 18%, transparent)`,
              color: pillColor,
            }}
          >
            {pillLabel}
          </span>
        </div>
        {url && (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="truncate text-[13px] text-[var(--blue)] hover:underline"
            title={url}
          >
            {url.replace(/^https?:\/\//, "")}
          </a>
        )}
      </div>
      {dt && (
        <div className="flex flex-shrink-0 flex-row items-baseline gap-1.5 text-[12px] text-[var(--text-faint)] sm:flex-col sm:items-end sm:gap-0">
          <span>{dt.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}</span>
          <span>{dt.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</span>
        </div>
      )}
    </div>
  );
}

// Tiles are colored only for the two "movement" categories that read as
// good/bad news at a glance (a price dropping, an item disappearing) —
// "new" and "policy" stay neutral text, matching the reference mockup
// rather than coloring every tile just because a color exists for it.
const STAT_TILE_VALUE_COLOR: Partial<Record<ChangeItemType, string>> = {
  price_drop: "var(--teal)",
  price_increase: "var(--red)",
  removed: "var(--red)",
};

// Fixed display order (not sorted by count) so tiles land in the same
// place card to card.
const STAT_TILE_ORDER: ChangeItemType[] = ["price_drop", "new", "removed", "policy", "price_increase", "other"];

function StatTiles({ items }: { items: ChangeItem[] }) {
  const counts: Partial<Record<ChangeItemType, number>> = {};
  for (const item of items) {
    counts[item.change_type] = (counts[item.change_type] ?? 0) + 1;
  }
  const entries = STAT_TILE_ORDER.filter((type) => counts[type]);
  if (entries.length === 0) return null;

  return (
    <div className="mt-3 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
      {entries.map((type) => (
        <div key={type} className="rounded-xl bg-[var(--bg-track)] px-3 py-2.5 sm:px-3.5 sm:py-3">
          <div className="text-[12.5px] text-[var(--text-faint)]">{CHANGE_TYPE_TILE_LABELS[type]}</div>
          <div
            className="mt-0.5 text-[19px] font-bold"
            style={{ color: STAT_TILE_VALUE_COLOR[type] ?? "var(--text-primary)" }}
          >
            {counts[type]}
          </div>
        </div>
      ))}
    </div>
  );
}

function ChangeItemsTable({ items }: { items: ChangeItem[] }) {
  return (
    <>
    <div className="mt-3 flex flex-col gap-2 sm:hidden">
      {items.map((row, i) => {
        const style = CHANGE_TYPE_STYLES[row.change_type];
        return (
          <div
            key={i}
            className="flex flex-col gap-1.5 rounded-lg border border-[var(--border-subtler)] px-3 py-2.5"
          >
            <div className="flex items-start justify-between gap-2">
              <span className="min-w-0 flex-1 text-[13px] text-[var(--text-secondary)]">{row.item}</span>
              <span
                className="flex-shrink-0 whitespace-nowrap rounded-full px-2 py-0.5 text-[11.5px] font-medium"
                style={{
                  background: `color-mix(in srgb, ${style.color} 18%, transparent)`,
                  color: style.color,
                }}
              >
                {style.label(row.change_pct)}
              </span>
            </div>
            <div className="flex flex-wrap items-baseline gap-1.5 text-[13px]">
              {row.before && (
                <>
                  <span className="text-[var(--text-dim)] line-through">{row.before}</span>
                  <span className="text-[var(--text-dim)]">→</span>
                </>
              )}
              <span className="font-semibold text-[var(--text-primary)]">{row.after}</span>
            </div>
          </div>
        );
      })}
    </div>
    <div className="mt-4 hidden overflow-x-auto sm:block">
      <table className="w-full min-w-[420px] border-collapse text-[13.5px]">
        <thead>
          <tr className="border-b border-[var(--border-subtle)] text-[12px] text-[var(--text-faint)]">
            <th className="px-1 pb-2 text-left font-normal">Item</th>
            <th className="px-1 pb-2 text-right font-normal">Before</th>
            <th className="px-1 pb-2 text-right font-normal">After</th>
            <th className="px-1 pb-2 text-right font-normal">Change</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, i) => {
            const style = CHANGE_TYPE_STYLES[row.change_type];
            return (
              <tr key={i} className="border-b border-[var(--border-subtler)] last:border-0">
                <td className="px-1 py-2.5 text-[var(--text-secondary)]">{row.item}</td>
                <td className="px-1 py-2.5 text-right">
                  {row.before ? (
                    <span className="text-[var(--text-dim)] line-through">{row.before}</span>
                  ) : (
                    <span className="ml-auto inline-block h-3 w-8 rounded bg-[var(--bg-track)]" />
                  )}
                </td>
                <td className="px-1 py-2.5 text-right font-semibold text-[var(--text-primary)]">{row.after}</td>
                <td className="px-1 py-2.5 text-right">
                  <span
                    className="whitespace-nowrap rounded-full px-2.5 py-1 text-[12px] font-medium"
                    style={{
                      background: `color-mix(in srgb, ${style.color} 18%, transparent)`,
                      color: style.color,
                    }}
                  >
                    {style.label(row.change_pct)}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
    </>
  );
}

function FactList({ facts }: { facts: BaselineFact[] }) {
  return (
    <div className="mt-2 flex flex-col">
      {facts.map((fact, i) => (
        <div
          key={i}
          className="flex items-center justify-between gap-3 py-2"
          style={{ borderBottom: i < facts.length - 1 ? "1px solid var(--border-subtler)" : undefined }}
        >
          <span className="text-[13.5px] text-[var(--text-muted)]">{fact.label}</span>
          <span className="text-right text-[13.5px] font-medium text-[var(--text-primary)]">{fact.value}</span>
        </div>
      ))}
    </div>
  );
}

export function BaselineCard({ snapshot, surface }: { snapshot: Snapshot; surface: Surface | undefined }) {
  const [showRaw, setShowRaw] = useState(false);
  const typeStyle = surface ? SURFACE_TYPE_STYLES[surface.surface_type] : SURFACE_TYPE_STYLES.other;
  const title = snapshot.headline || (surface ? surfaceDisplayName(surface) : typeStyle.label);
  const hasFacts = !!snapshot.facts && snapshot.facts.length > 0;
  const preview =
    !hasFacts && !snapshot.summary && snapshot.text_content
      ? snapshot.text_content.trim().slice(0, 280)
      : null;

  return (
    <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-nested)] px-3.5 py-3.5 sm:px-5 sm:py-4">
      <CardHeader
        title={title}
        pillLabel="Current snapshot"
        pillColor="var(--text-muted)"
        url={surface ? surface.url.split("?")[0] : null}
        createdAt={snapshot.created_at}
      />

      {hasFacts ? (
        <>
          <p className="m-0 mb-1 text-[13px] text-[var(--text-faint)]">Baseline saved · nothing to compare yet</p>
          <FactList facts={snapshot.facts!} />
        </>
      ) : snapshot.summary ? (
        <p className="m-0 text-[14px] leading-[1.55] text-[var(--text-primary)]">{snapshot.summary}</p>
      ) : (
        <div className="text-[13.5px] leading-[1.55] text-[var(--text-faint)]">
          <p className="m-0">No changes detected yet — this is what&apos;s currently on the page.</p>
          {preview && (
            <code className="mt-1 block text-[12px] text-[var(--text-dim)]">
              {preview}
              {snapshot.text_content!.length > 280 ? "…" : ""}
            </code>
          )}
        </div>
      )}

      {!hasFacts && snapshot.highlights && snapshot.highlights.length > 0 && (
        <HighlightList items={snapshot.highlights} color={typeStyle.color} />
      )}

      {snapshot.text_content && (
        <div className="mt-4">
          <button
            onClick={() => setShowRaw((v) => !v)}
            className="h-8 rounded-lg border border-[var(--border-input)] px-3 text-[13px] font-medium text-[var(--text-secondary)] hover:border-[var(--border-hover)]"
          >
            {"</> "}
            {showRaw ? "Hide raw text" : "Raw text"}
          </button>
          {showRaw && (
            <pre className="mt-2 max-h-[280px] overflow-y-auto overflow-x-auto whitespace-pre-wrap max-sm:break-words rounded-lg border border-[var(--border-subtler)] bg-[var(--bg-page)] p-3 font-mono text-[11.5px] text-[var(--text-dim)]">
              {snapshot.text_content}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export function ChangeCard({
  log,
  surface,
  competitorName,
}: {
  log: ChangeLog;
  surface: Surface | undefined;
  competitorName?: string | undefined;
}) {
  const [showAllItems, setShowAllItems] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const color = classificationColor(log.classification);
  const hasItems = !!log.items && log.items.length > 0;
  const fallbackLabel = surface ? surfaceDisplayName(surface) : "Change";
  const title = competitorName
    ? `${competitorName} — ${log.headline || fallbackLabel}`
    : log.headline || fallbackLabel;
  const visibleItems = hasItems ? (showAllItems ? log.items! : log.items!.slice(0, 5)) : [];

  return (
    <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-nested)] px-3.5 py-3.5 sm:px-5 sm:py-4">
      <CardHeader
        title={title}
        pillLabel={hasItems ? `${log.items!.length} change${log.items!.length === 1 ? "" : "s"}` : "Change"}
        pillColor={color}
        url={surface ? surface.url.split("?")[0] : null}
        createdAt={log.created_at}
      />

      {hasItems ? (
        <>
          <StatTiles items={log.items!} />
          <ChangeItemsTable items={visibleItems} />
          <div className="mt-4 flex flex-wrap gap-2">
            {log.items!.length > 5 && (
              <button
                onClick={() => setShowAllItems((v) => !v)}
                className="h-8 rounded-lg border border-[var(--border-input)] px-3 text-[13px] font-medium text-[var(--text-secondary)] hover:border-[var(--border-hover)]"
              >
                {showAllItems ? "Show fewer" : `View all ${log.items!.length} changes`}
              </button>
            )}
            {log.diff && (
              <button
                onClick={() => setShowRaw((v) => !v)}
                className="h-8 rounded-lg border border-[var(--border-input)] px-3 text-[13px] font-medium text-[var(--text-secondary)] hover:border-[var(--border-hover)]"
              >
                {"</> "}
                {showRaw ? "Hide raw text" : "Raw text"}
              </button>
            )}
          </div>
          {showRaw && log.diff && (
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap max-sm:break-words rounded-lg border border-[var(--border-subtler)] bg-[var(--bg-page)] p-3 font-mono text-[11.5px] text-[var(--text-dim)]">
              {log.diff}
            </pre>
          )}
        </>
      ) : (
        <>
          <div className="mb-1.5 flex items-center gap-2">
            <ClassificationBadge classification={log.classification} />
            {log.materiality_score !== null && (
              <span className="text-[12px] text-[var(--text-muted)]">
                {(log.materiality_score / 100).toFixed(2)} materiality
              </span>
            )}
          </div>
          {log.rationale && (
            <p className="m-0 text-[14px] leading-[1.55] text-[var(--text-primary)]">{log.rationale}</p>
          )}
          {log.highlights && log.highlights.length > 0 && (
            <HighlightList items={log.highlights} color={color} />
          )}
          {log.diff && (
            <div className="mt-3">
              <button
                onClick={() => setShowRaw((v) => !v)}
                className="h-8 rounded-lg border border-[var(--border-input)] px-3 text-[13px] font-medium text-[var(--text-secondary)] hover:border-[var(--border-hover)]"
              >
                {"</> "}
                {showRaw ? "Hide raw diff" : "Raw diff"}
              </button>
              {showRaw && (
                <pre className="mt-2 overflow-x-auto whitespace-pre-wrap max-sm:break-words rounded-lg border border-[var(--border-subtler)] bg-[var(--bg-page)] p-3 font-mono text-[11.5px] text-[var(--text-dim)]">
                  {log.diff}
                </pre>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
