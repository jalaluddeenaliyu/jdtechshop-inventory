import { useState } from "react";
import { naira } from "../lib/format";

// Single-series column chart: thin columns (≤ 24px), 4px rounded tops, square at the baseline,
// hairline grid, clean ₦ ticks, and a hover/focus tooltip. One series, so no legend: the panel
// title says what is plotted. Colour validated for the light surface (dataviz validator).
const BAR = "#1f8f6b";
const BAR_HOVER = "#16876a";

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v) ?? 10 * p;
}

function short(n: number): string {
  if (n >= 1_000_000) return `₦${(n / 1_000_000).toLocaleString("en-NG", { maximumFractionDigits: 1 })}m`;
  if (n >= 1_000) return `₦${(n / 1_000).toLocaleString("en-NG", { maximumFractionDigits: 1 })}k`;
  return `₦${n.toLocaleString("en-NG")}`;
}

export function BarChart({ points, label }: { points: { label: string; title: string; value: number; extra?: string }[]; label: string }) {
  const [active, setActive] = useState<number | null>(null);
  const W = 640, H = 220, left = 56, right = 8, top = 12, bottom = 28;
  const max = niceMax(Math.max(0, ...points.map((p) => p.value)));
  const plotW = W - left - right, plotH = H - top - bottom;
  const slot = plotW / Math.max(points.length, 1);
  const barW = Math.min(24, Math.max(2, slot - 2)); // 2px surface gap between neighbours
  const ticks = [0, max / 2, max];
  const labelEvery = Math.ceil(points.length / 10);
  const y = (v: number) => top + plotH - (Math.max(0, v) / max) * plotH;

  return (
    <figure className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label={label}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={left} x2={W - right} y1={y(t)} y2={y(t)} stroke="#e7ebe9" strokeWidth={1} />
            <text x={left - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="#5b6a64">{short(t)}</text>
          </g>
        ))}
        {points.map((p, i) => {
          const x = left + i * slot + (slot - barW) / 2;
          const h = Math.max(0, y(0) - y(p.value));
          const r = Math.min(4, barW / 2, h);
          return (
            <g key={i} onMouseEnter={() => setActive(i)} onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(i)} onBlur={() => setActive(null)} tabIndex={0}
              aria-label={`${p.title}: ${naira(p.value)}`} className="outline-none">
              {/* hit target: the whole slot, taller than the mark */}
              <rect x={left + i * slot} y={top} width={slot} height={plotH} fill="transparent" />
              {h > 0 && (
                <path fill={active === i ? BAR_HOVER : BAR}
                  d={`M${x},${y(0)} V${y(0) - h + r} Q${x},${y(0) - h} ${x + r},${y(0) - h} H${x + barW - r} Q${x + barW},${y(0) - h} ${x + barW},${y(0) - h + r} V${y(0)} Z`} />
              )}
              {i % labelEvery === 0 && (
                <text x={left + i * slot + slot / 2} y={H - 8} textAnchor="middle" fontSize="11" fill="#5b6a64">{p.label}</text>
              )}
            </g>
          );
        })}
        <line x1={left} x2={W - right} y1={y(0)} y2={y(0)} stroke="#c9d2ce" strokeWidth={1} />
      </svg>
      {active !== null && points[active] && (
        <div className="pointer-events-none absolute top-0 rounded-md border border-line bg-white px-3 py-2 text-sm shadow-md"
          style={{ left: `min(calc(${((left + active * slot + slot / 2) / W) * 100}% + 8px), calc(100% - 11rem))` }}>
          <p className="font-medium">{points[active].title}</p>
          <p className="num">{naira(points[active].value)}</p>
          {points[active].extra && <p className="num text-muted">{points[active].extra}</p>}
        </div>
      )}
    </figure>
  );
}
