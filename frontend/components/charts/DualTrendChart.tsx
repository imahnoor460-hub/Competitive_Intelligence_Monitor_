"use client";

import { useEffect, useMemo, useState, MouseEvent } from "react";

export interface DualTrendPoint {
  date: string;
  detected: number;
  material: number;
}

const WIDTH = 534;
const HEIGHT = 196;
// The viewBox scales uniformly to the container, so the desktop 534×196 box
// is only ~90px tall once it is squeezed into a 288px portrait card. A
// narrower box at the same height keeps the trend legible there.
const NARROW_WIDTH = 300;
const NARROW_MEDIA_QUERY = "(max-width: 639px)";
const PADDING = { top: 12, right: 12, bottom: 20, left: 12 };

export default function DualTrendChart({ data }: { data: DualTrendPoint[] }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  // Resolved after mount so the server and the first client render agree.
  const [narrow, setNarrow] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia(NARROW_MEDIA_QUERY);
    const sync = () => setNarrow(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const width = narrow ? NARROW_WIDTH : WIDTH;
  const innerWidth = width - PADDING.left - PADDING.right;
  const innerHeight = HEIGHT - PADDING.top - PADDING.bottom;
  const maxCount = Math.max(1, ...data.map((d) => d.detected));

  const points = useMemo(
    () =>
      data.map((d, i) => ({
        x:
          PADDING.left +
          (data.length === 1 ? innerWidth / 2 : (i / (data.length - 1)) * innerWidth),
        yDetected: PADDING.top + innerHeight - (d.detected / maxCount) * innerHeight,
        yMaterial: PADDING.top + innerHeight - (d.material / maxCount) * innerHeight,
        ...d,
      })),
    [data, innerWidth, innerHeight, maxCount]
  );

  const path = (key: "yDetected" | "yMaterial") =>
    points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p[key]}`).join(" ");

  const baseline = PADDING.top + innerHeight;
  const areaPath =
    points.length > 0
      ? `${path("yDetected")} L ${points[points.length - 1].x} ${baseline} L ${points[0].x} ${baseline} Z`
      : "";

  function handleMove(e: MouseEvent<SVGSVGElement>) {
    if (points.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * width;
    let nearest = 0;
    let nearestDist = Infinity;
    points.forEach((p, i) => {
      const dist = Math.abs(p.x - x);
      if (dist < nearestDist) {
        nearestDist = dist;
        nearest = i;
      }
    });
    setHoverIndex(nearest);
  }

  const hovered = hoverIndex !== null ? points[hoverIndex] : null;

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${HEIGHT}`}
        className="w-full overflow-visible"
        onMouseMove={handleMove}
        onMouseLeave={() => setHoverIndex(null)}
        role="img"
        aria-label="Changes detected vs. judged material, daily"
      >
        <defs>
          <linearGradient id="ciAreaBlue" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--blue)" stopOpacity="0.3" />
            <stop offset="100%" stopColor="var(--blue)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 0.5, 1].map((f) => (
          <line
            key={f}
            x1={PADDING.left}
            x2={width - PADDING.right}
            y1={PADDING.top + innerHeight * f}
            y2={PADDING.top + innerHeight * f}
            stroke="var(--border-subtle)"
            strokeWidth={1}
            strokeDasharray={f === 1 ? undefined : "3 5"}
          />
        ))}
        {points.length > 0 && (
          <>
            <path d={areaPath} fill="url(#ciAreaBlue)" stroke="none" />
            <path
              d={path("yDetected")}
              fill="none"
              stroke="var(--blue)"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <path
              d={path("yMaterial")}
              fill="none"
              stroke="var(--accent)"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </>
        )}
        {hovered && (
          <line
            x1={hovered.x}
            x2={hovered.x}
            y1={PADDING.top}
            y2={baseline}
            stroke="var(--text-dim)"
            strokeWidth={1}
          />
        )}
        {points.map((p, i) => (
          <g key={p.date}>
            <circle
              cx={p.x}
              cy={p.yDetected}
              r={hoverIndex === i ? 4.5 : 3}
              fill="var(--blue)"
              stroke="var(--bg-card)"
              strokeWidth={2}
            />
            <circle
              cx={p.x}
              cy={p.yMaterial}
              r={hoverIndex === i ? 4.5 : 3}
              fill="var(--accent)"
              stroke="var(--bg-card)"
              strokeWidth={2}
            />
          </g>
        ))}
      </svg>
      <div className="mt-1 h-4 text-xs text-[var(--text-secondary)]">
        {hovered && (
          <>
            <span className="font-mono font-semibold text-[var(--blue)]">{hovered.detected}</span>{" "}
            detected &middot;{" "}
            <span className="font-mono font-semibold text-[var(--accent)]">{hovered.material}</span>{" "}
            material on{" "}
            {new Date(hovered.date).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
          </>
        )}
      </div>
    </div>
  );
}
