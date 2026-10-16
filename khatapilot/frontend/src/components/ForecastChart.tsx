import { useEffect, useMemo, useRef, useState } from "react";

import { compactTaka, dayLabel, taka } from "../format";
import type { CashForecast } from "../types";

const PAD = { left: 52, right: 18, top: 18, bottom: 30 };
const HEIGHT = 280;

function niceTicks(min: number, max: number, count = 5): number[] {
  const span = max - min || 1;
  const raw = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(Math.round(v));
  return ticks;
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

/** Projected cash, day by day: the line is the expected balance, the wash is the 80% range. */
export function ForecastChart({ forecast }: { forecast: CashForecast }) {
  const { ref, width } = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const days = forecast.days;
  const n = days.length;

  const geometry = useMemo(() => {
    const values = days.flatMap((d) => [d.low_paisa, d.high_paisa]).concat([0, forecast.buffer_paisa]);
    const ticks = niceTicks(Math.min(...values), Math.max(...values));
    const lo = Math.min(ticks[0], ...values);
    const hi = Math.max(ticks[ticks.length - 1], ...values);
    const innerW = width - PAD.left - PAD.right;
    const innerH = HEIGHT - PAD.top - PAD.bottom;
    const x = (i: number) => PAD.left + (n === 1 ? innerW / 2 : (i / (n - 1)) * innerW);
    const y = (v: number) => PAD.top + (1 - (v - lo) / (hi - lo || 1)) * innerH;
    const line = days.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.balance_paisa).toFixed(1)}`).join("");
    const band =
      days.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.high_paisa).toFixed(1)}`).join("") +
      [...days].reverse().map((d, j) => `L${x(n - 1 - j).toFixed(1)},${y(d.low_paisa).toFixed(1)}`).join("") +
      "Z";
    return { ticks, x, y, line, band, innerW };
  }, [days, forecast.buffer_paisa, n, width]);

  const { ticks, x, y, line, band, innerW } = geometry;
  const labelEvery = innerW / n < 46 ? 2 : 1;
  const first = forecast.first_shortfall;
  const firstIndex = first ? days.findIndex((d) => d.day === first.day) : -1;
  const last = days[n - 1];

  function pick(clientX: number, rect: DOMRect) {
    const position = ((clientX - rect.left) / rect.width) * width;
    const index = Math.round(((position - PAD.left) / innerW) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, index)));
  }

  const active = hover !== null ? days[hover] : null;
  const tooltipLeft = hover !== null ? Math.min(Math.max(x(hover) - 110, 4), width - 224) : 0;

  return (
    <div ref={ref} className="relative">
      <svg
        width={width}
        height={HEIGHT}
        role="img"
        aria-label={`Projected cash for ${n} days. ${forecast.headline}`}
        tabIndex={0}
        className="block outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
        onPointerMove={(e) => pick(e.clientX, e.currentTarget.getBoundingClientRect())}
        onPointerLeave={() => setHover(null)}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") setHover((h) => Math.min(n - 1, (h ?? -1) + 1));
          if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? n) - 1));
          if (e.key === "Escape") setHover(null);
        }}
        onBlur={() => setHover(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeWidth={t === 0 ? 1.5 : 1} />
            <text x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-faint font-mono text-[10.5px] tabular">
              {compactTaka(t)}
            </text>
          </g>
        ))}
        {days.map((d, i) =>
          i % labelEvery === 0 ? (
            <text key={d.day} x={x(i)} y={HEIGHT - 10} textAnchor="middle" className={`font-mono text-[10.5px] ${d.below_buffer ? "fill-bad font-semibold" : "fill-faint"}`}>
              {dayLabel(d.day).replace(/ \w+$/, "")}
            </text>
          ) : null,
        )}

        <path d={band} fill="var(--ink)" opacity={0.07} />
        <line
          x1={PAD.left}
          x2={width - PAD.right}
          y1={y(forecast.buffer_paisa)}
          y2={y(forecast.buffer_paisa)}
          stroke="var(--warn)"
          strokeWidth={1.25}
          strokeDasharray="5 4"
        />
        <text x={PAD.left + 6} y={y(forecast.buffer_paisa) - 6} className="fill-warn font-mono text-[10.5px] uppercase">
          Safety buffer {taka(forecast.buffer_paisa, true)}
        </text>
        <path d={line} fill="none" stroke="var(--ink)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />

        {hover !== null && (
          <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={HEIGHT - PAD.bottom} stroke="var(--faint)" strokeWidth={1} strokeDasharray="2 3" />
        )}
        {days.map((d, i) =>
          d.below_buffer || i === hover ? (
            <circle
              key={d.day}
              cx={x(i)}
              cy={y(d.balance_paisa)}
              r={i === hover ? 5 : 4.5}
              fill={d.below_buffer ? "var(--bad)" : "var(--ink)"}
              stroke="var(--surface)"
              strokeWidth={2}
            />
          ) : null,
        )}
        {firstIndex >= 0 && first && (
          <text
            x={x(firstIndex)}
            y={y(days[firstIndex].balance_paisa) + 20}
            textAnchor="middle"
            className="fill-bad font-mono text-[10.5px] font-semibold uppercase"
          >
            Short {taka(first.short_by_paisa, true)}
          </text>
        )}
        <circle cx={x(n - 1)} cy={y(last.balance_paisa)} r={4} fill={last.below_buffer ? "var(--bad)" : "var(--ink)"} stroke="var(--surface)" strokeWidth={2} />
        <text x={x(n - 1) - 8} y={y(last.balance_paisa) - 10} textAnchor="end" className="fill-ink font-mono text-[11px] font-medium tabular">
          {taka(last.balance_paisa, true)}
        </text>
      </svg>

      {active && hover !== null && (
        <div
          className="pointer-events-none absolute top-2 z-10 w-56 rounded-xl bg-bar p-3 text-xs text-bar-ink shadow-[0_18px_40px_-12px_rgba(20,15,10,0.5)]"
          style={{ left: tooltipLeft }}
        >
          <div className="label text-[10px] text-bar-muted">{dayLabel(active.day, true)}</div>
          <div className="display mt-1 flex items-center gap-2 text-xl">
            {active.below_buffer && <span className="size-2 rounded-full bg-bad" />}
            {taka(active.balance_paisa, true)}
          </div>
          <div className="mt-1 text-bar-muted">
            80% range {taka(active.low_paisa, true)} to {taka(active.high_paisa, true)}
          </div>
          <div className="mt-0.5 tabular text-bar-muted">
            In {taka(active.expected_in_paisa, true)} · Out {taka(active.expected_out_paisa, true)}
          </div>
          {active.items.slice(0, 2).map((item) => (
            <div key={item.key} className="mt-1.5 border-t border-bar-line pt-1.5">
              <span className="font-semibold">{taka(item.amount_paisa, true)}</span> {item.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
