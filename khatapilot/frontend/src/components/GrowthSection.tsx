import { ArrowRight, Sprout } from "lucide-react";
import { useMemo, useState } from "react";

import { api, errorText } from "../api";
import { compactTaka, taka } from "../format";
import { useData } from "../hooks";
import { useApp } from "../state";
import type { FlowPart, GrowthPlan, Lever, Scenario } from "../types";
import { Badge, Button, Card, Loading, Note, Segmented } from "../ui";

const STOCK_SHARES = [5, 10, 15, 20, 25, 30];

/** How much profit goes back into stock: sized from the books every month, or the owner's own share. */
function StockShare({ plan, onChange }: { plan: GrowthPlan; onChange: (p: GrowthPlan) => void }) {
  const { refresh, toast } = useApp();
  const [busy, setBusy] = useState(false);
  const p = plan.profile;
  async function choose(value: number) {
    setBusy(true);
    try {
      onChange(await api.put<GrowthPlan>("/growth/reinvest", { share_pct: value === 0 ? null : value }));
      refresh();
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-2.5 rounded-xl border border-line bg-page/60 p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="label text-[10px] text-faint">Put back into stock</span>
        <span className="text-xs text-muted">
          {p.reinvest_choice_pct !== null ? `Your choice: ${p.reinvest_choice_pct}%` : `From your books: ${p.reinvest_suggested_pct}%`}
          {p.reinvest_wait && <span className="text-warn"> · {p.reinvest_wait}</span>}
        </span>
      </div>
      <div className={busy ? "pointer-events-none opacity-60" : ""}>
        <Segmented
          value={p.reinvest_choice_pct ?? 0}
          onChange={(v) => void choose(v)}
          options={[{ value: 0, label: "Auto" }, ...STOCK_SHARES.map((s) => ({ value: s, label: `${s}%` }))]}
        />
      </div>
      <p className="text-xs leading-relaxed text-muted">
        {p.reinvest_why} Worked out again every month.{" "}
        {p.reinvest_choice_pct !== null && `Your ${p.reinvest_choice_pct}% is used instead; tap Auto to go back. `}
        In chat: “put 15% back into stock” or “let KhataPilot decide”. It moves no money.
      </p>
    </div>
  );
}
import { niceTicks, useWidth } from "./ForecastChart";

const STAGE_TONE: Record<GrowthPlan["profile"]["stage"], "ok" | "warn" | "bad" | "info"> = {
  ready_to_grow: "ok",
  steady: "ok",
  up_and_down: "info",
  choppy: "warn",
  shrinking: "bad",
  cushion_first: "warn",
};

// Stock and running costs in ink tones (darkest first), profit in green.
const COST_SHADES = ["var(--ink)", "color-mix(in srgb, var(--ink) 62%, var(--surface))", "color-mix(in srgb, var(--ink) 45%, var(--surface))", "color-mix(in srgb, var(--ink) 32%, var(--surface))", "color-mix(in srgb, var(--ink) 22%, var(--surface))", "color-mix(in srgb, var(--ink) 14%, var(--surface))"];
const USE_COLOR: Record<string, string> = { household: "var(--warn)", savings: "var(--info)", kept: "var(--ok)" };
const PATH_COLOR: Record<Scenario["key"], string> = { as_now: "var(--ink)", reinvest: "var(--ok)", collect_and_reinvest: "var(--info)" };
const TAB_LABEL: Record<Lever["tab"], string> = { ledger: "Open Ledger", forecast: "Open Forecast", savings: "", chat: "" };

interface Segment {
  key: string;
  label: string;
  amount: number;
  color: string;
  hatch?: boolean;
}

function Bar({ segments, total }: { segments: Segment[]; total: number }) {
  return (
    <div className="flex h-9 w-full overflow-hidden rounded-lg border border-line bg-page">
      {segments
        .filter((s) => s.amount > 0)
        .map((s) => (
          <div
            key={s.key}
            title={`${s.label}: ${taka(s.amount, true)}`}
            className="h-full border-r border-surface/70 last:border-r-0"
            style={{
              width: `${(s.amount / total) * 100}%`,
              background: s.hatch ? `repeating-linear-gradient(135deg, ${s.color} 0 5px, transparent 5px 9px)` : s.color,
            }}
          />
        ))}
    </div>
  );
}

function Legend({ segments, total }: { segments: Segment[]; total: number }) {
  return (
    <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
      {segments.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm" style={{ background: s.color }} />
          <span className="text-muted">{s.label}</span>
          <span className="font-medium tabular">{taka(s.amount, true)}</span>
          {total > 0 && <span className="text-faint tabular">{Math.round((s.amount / total) * 100)}%</span>}
        </li>
      ))}
    </ul>
  );
}

/** Money in, where it went, and the profit opened up into what happened to it. */
function MoneyFlowDiagram({ flow }: { flow: GrowthPlan["flow"] }) {
  const inSegments: Segment[] = flow.money_in.map((p, i) => ({ key: p.key, label: p.label, amount: p.amount_paisa, color: i ? COST_SHADES[3] : COST_SHADES[1] }));
  const totalIn = inSegments.reduce((s, p) => s + p.amount, 0);
  const costs: Segment[] = flow.costs.map((p: FlowPart, i) => ({ key: p.key, label: p.label, amount: p.amount_paisa, color: COST_SHADES[Math.min(i, COST_SHADES.length - 1)] }));
  const profit = flow.profit_paisa;
  const outSegments = [...costs, ...(profit > 0 ? [{ key: "profit", label: "Profit", amount: profit, color: "var(--ok)" }] : [])];
  const outTotal = Math.max(totalIn, outSegments.reduce((s, p) => s + p.amount, 0));
  const profitStart = outTotal > 0 ? ((outTotal - Math.max(0, profit)) / outTotal) * 100 : 100;

  const household = flow.uses.find((u) => u.key === "household")?.amount_paisa ?? 0;
  const saved = flow.uses.find((u) => u.key === "savings")?.amount_paisa ?? 0;
  const kept = flow.uses.find((u) => u.key === "kept");
  const overflow = kept && kept.amount_paisa < 0 ? -kept.amount_paisa : 0;
  const useSegments: Segment[] = [
    { key: "household", label: "Household", amount: household, color: USE_COLOR.household },
    { key: "savings", label: "Your savings", amount: saved, color: USE_COLOR.savings },
    ...(kept && kept.amount_paisa >= 0 ? [{ key: "kept", label: "Kept in the shop", amount: kept.amount_paisa, color: USE_COLOR.kept }] : []),
  ];
  const useTotal = Math.max(profit, household + saved, 1);

  return (
    <div className="space-y-1">
      <div className="label text-[10px] text-faint">Money in</div>
      <Bar segments={inSegments} total={Math.max(totalIn, 1)} />
      <Legend segments={inSegments} total={totalIn} />

      <div className="label pt-4 text-[10px] text-faint">Where it went</div>
      <Bar segments={outSegments} total={Math.max(outTotal, 1)} />
      <Legend segments={outSegments} total={totalIn} />

      {profit > 0 && (
        <>
          <svg viewBox="0 0 100 34" preserveAspectRatio="none" className="mt-2 block h-9 w-full" aria-hidden>
            <polygon points={`${profitStart},0 100,0 100,34 0,34`} fill="var(--ok)" opacity={0.13} />
            <line x1={profitStart} y1={0} x2={0} y2={34} stroke="var(--ok)" strokeWidth={0.4} vectorEffect="non-scaling-stroke" />
          </svg>
          <div className="label text-[10px] text-faint">What happened to the {taka(profit, true)} profit</div>
          <div className="relative">
            <Bar segments={useSegments} total={useTotal} />
            {overflow > 0 && (
              <div
                className="pointer-events-none absolute inset-y-0 rounded-r-lg border-2 border-bad"
                style={{ left: `${(profit / useTotal) * 100}%`, right: 0 }}
                title={`${taka(overflow, true)} more than the shop made`}
              />
            )}
          </div>
          <Legend segments={useSegments} total={0} />
          {overflow > 0 && (
            <p className="text-xs font-medium text-bad">
              The red-edged part, {taka(overflow, true)}, came out of the shop's own cash: more than it made.
            </p>
          )}
        </>
      )}
    </div>
  );
}

/** Where the shop sits between steady and choppy, by how far a usual day swings from normal. */
function SteadinessScale({ swing }: { swing: number | null }) {
  const max = 40;
  const at = swing === null ? null : Math.min(max, swing);
  return (
    <div>
      <div className="relative h-2.5 overflow-hidden rounded-full">
        <div className="absolute inset-y-0 left-0 bg-ok/55" style={{ width: `${(10 / max) * 100}%` }} />
        <div className="absolute inset-y-0 bg-warn/45" style={{ left: `${(10 / max) * 100}%`, width: `${(15 / max) * 100}%` }} />
        <div className="absolute inset-y-0 right-0 bg-bad/40" style={{ left: `${(25 / max) * 100}%` }} />
      </div>
      {at !== null && (
        <div className="relative h-4">
          <div className="absolute -top-0.5 size-0 -translate-x-1/2 border-x-[6px] border-b-[7px] border-x-transparent border-b-ink" style={{ left: `${(at / max) * 100}%` }} />
        </div>
      )}
      <div className="flex justify-between text-[11px] text-faint">
        <span>steady</span>
        <span>up and down</span>
        <span>choppy</span>
      </div>
    </div>
  );
}

const CHART = { left: 56, right: 30, top: 16, bottom: 28, height: 260 };  // room for the first and last month labels

/** Profit per month on the three paths; the wash is the 80% range around "as now". */
function ProjectionChart({ scenarios }: { scenarios: Scenario[] }) {
  const { ref, width } = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const base = scenarios[0];
  const n = base.months.length;
  const geo = useMemo(() => {
    const values = scenarios.flatMap((s) => s.months.flatMap((m) => [m.profit_paisa, m.low_paisa, m.high_paisa])).concat([0]);
    const ticks = niceTicks(Math.min(...values), Math.max(...values));
    const lo = Math.min(ticks[0], ...values);
    const hi = Math.max(ticks[ticks.length - 1], ...values);
    const innerW = width - CHART.left - CHART.right;
    const innerH = CHART.height - CHART.top - CHART.bottom;
    const x = (i: number) => CHART.left + (i / (n - 1)) * innerW;
    const y = (v: number) => CHART.top + (1 - (v - lo) / (hi - lo || 1)) * innerH;
    const line = (s: Scenario) => s.months.map((m, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(m.profit_paisa).toFixed(1)}`).join("");
    const band =
      base.months.map((m, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(m.high_paisa).toFixed(1)}`).join("") +
      [...base.months].reverse().map((m, j) => `L${x(n - 1 - j).toFixed(1)},${y(m.low_paisa).toFixed(1)}`).join("") +
      "Z";
    return { ticks, x, y, line, band, innerW };
  }, [scenarios, base, n, width]);
  const every = geo.innerW / n < 34 ? 3 : geo.innerW / n < 52 ? 2 : 1;

  return (
    <div ref={ref} className="relative">
      <svg
        width={width}
        height={CHART.height}
        role="img"
        aria-label="Monthly profit for the next 12 months on three paths"
        className="block"
        onPointerMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const i = Math.round((((e.clientX - rect.left) / rect.width) * width - CHART.left) / geo.innerW * (n - 1));
          setHover(Math.max(0, Math.min(n - 1, i)));
        }}
        onPointerLeave={() => setHover(null)}
      >
        {geo.ticks.map((t) => (
          <g key={t}>
            <line x1={CHART.left} x2={width - CHART.right} y1={geo.y(t)} y2={geo.y(t)} stroke="var(--line)" strokeWidth={t === 0 ? 1.5 : 1} />
            <text x={CHART.left - 8} y={geo.y(t)} dy="0.32em" textAnchor="end" className="fill-faint font-mono text-[10.5px] tabular">
              {compactTaka(t)}
            </text>
          </g>
        ))}
        {base.months.map((m, i) =>
          i % every === 0 ? (
            <text key={m.month} x={geo.x(i)} y={CHART.height - 8} textAnchor="middle" className="fill-faint font-mono text-[10.5px]">
              {m.label}
            </text>
          ) : null,
        )}
        {base.months.map((m, i) =>
          m.season ? (
            <g key={`season-${m.month}`}>
              <rect x={geo.x(i) - geo.innerW / (n - 1) / 2} y={CHART.top} width={geo.innerW / (n - 1)} height={CHART.height - CHART.top - CHART.bottom} fill="var(--warn)" opacity={0.08} />
              <text x={geo.x(i)} y={CHART.top + 10} textAnchor="middle" className="fill-warn font-mono text-[9.5px] uppercase">
                {m.season === "Ramadan and Eid" ? "Eid" : m.season}
              </text>
            </g>
          ) : null,
        )}
        <path d={geo.band} fill="var(--ink)" opacity={0.07} />
        {[...scenarios].reverse().map((s) => (
          <path key={s.key} d={geo.line(s)} fill="none" stroke={PATH_COLOR[s.key]} strokeWidth={2} strokeDasharray={s.key === "as_now" ? "5 4" : undefined} strokeLinecap="round" strokeLinejoin="round" />
        ))}
        {hover !== null && <line x1={geo.x(hover)} x2={geo.x(hover)} y1={CHART.top} y2={CHART.height - CHART.bottom} stroke="var(--faint)" strokeDasharray="2 3" />}
        {hover !== null &&
          scenarios.map((s) => <circle key={s.key} cx={geo.x(hover)} cy={geo.y(s.months[hover].profit_paisa)} r={4} fill={PATH_COLOR[s.key]} stroke="var(--surface)" strokeWidth={2} />)}
      </svg>
      {hover !== null && (
        <div
          className="pointer-events-none absolute top-2 z-10 w-56 rounded-xl bg-bar p-3 text-xs text-bar-ink shadow-[0_18px_40px_-12px_rgba(20,15,10,0.5)]"
          style={{ left: Math.min(Math.max(geo.x(hover) - 112, 4), width - 228) }}
        >
          <div className="label text-[10px] text-bar-muted">
            {base.months[hover].label} · profit that month{base.months[hover].season ? ` · ${base.months[hover].season}` : ""}
          </div>
          {scenarios.map((s) => (
            <div key={s.key} className="mt-1.5 flex items-center gap-2">
              <span className="size-2 rounded-full" style={{ background: PATH_COLOR[s.key] === "var(--ink)" ? "var(--bar-ink)" : PATH_COLOR[s.key] }} />
              <span className="flex-1 truncate text-bar-muted">{s.label}</span>
              <span className="font-medium tabular">{taka(s.months[hover].profit_paisa, true)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function LeverRow({ lever, index }: { lever: Lever; index: number }) {
  const { setTab } = useApp();
  return (
    <li className="flex gap-3 rounded-xl border border-line bg-page/60 p-3.5 text-sm">
      <span className="display flex size-7 shrink-0 items-center justify-center rounded-full bg-ink text-[13px] text-surface">{index}</span>
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="font-medium">{lever.title}</div>
        <p className="leading-relaxed text-muted">{lever.text}</p>
        <div className="flex flex-wrap items-center gap-2">
          {lever.monthly_paisa > 0 && <Badge tone="ok">+{taka(lever.monthly_paisa, true)} a month</Badge>}
          {lever.cash_paisa > 0 && <Badge tone="info">frees {taka(lever.cash_paisa, true)}</Badge>}
          {TAB_LABEL[lever.tab] && (
            <Button size="sm" variant="ghost" onClick={() => setTab(lever.tab as "ledger" | "forecast")}>
              {TAB_LABEL[lever.tab]} <ArrowRight className="size-3.5" />
            </Button>
          )}
        </div>
      </div>
    </li>
  );
}

export function GrowthSection() {
  const { version } = useApp();
  const { data, error, setData } = useData<GrowthPlan>("/growth", [version]);
  if (error) return <Note tone="info">{error}</Note>;
  if (!data) return <Loading label="Reading your books for growth" />;
  const p = data.profile;
  const tone = STAGE_TONE[p.stage];

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3 border-t border-line pt-8">
        <div className="space-y-1.5">
          <div className="label flex items-center gap-2 text-muted">
            <Sprout className="size-4" /> Grow your shop
          </div>
          <h2 className="display text-[26px] leading-tight">{data.headline}</h2>
          <p className="max-w-2xl text-sm text-muted">
            Worked out from your own books, so a steady shop and a choppy one get different advice. Estimates to plan
            with, not promises; nothing here moves money.
          </p>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <Card eyebrow={`${data.flow.label}, in one picture`} title={data.flow.headline}>
          <MoneyFlowDiagram flow={data.flow} />
        </Card>

        <Card eyebrow="What kind of shop" title={<span className="flex items-center gap-2">{p.stage_label} <Badge tone={tone}>{p.trend}</Badge></span>}>
          <div className="space-y-4">
            <p className="text-sm leading-relaxed text-muted">{p.stage_text}</p>
            <SteadinessScale swing={p.daily_swing_pct} />
            <dl className="grid grid-cols-2 gap-3">
              {[
                ["Daily swing", p.daily_swing_pct !== null ? `${p.daily_swing_pct.toFixed(0)}%` : "—"],
                ["Sales trend", `${p.monthly_trend_pct > 0 ? "+" : ""}${p.monthly_trend_pct.toFixed(1)}% / month`],
                ["Margin", `${data.flow.gross_margin_pct.toFixed(0)}%`],
                ["Put back into stock", p.reinvest_pct ? `${p.reinvest_pct}% of profit` : "not yet"],
              ].map(([label, value]) => (
                <div key={label} className="rounded-xl border border-line bg-page/60 px-3 py-2.5">
                  <dt className="label text-[10px] text-faint">{label}</dt>
                  <dd className="display mt-1 text-[19px] tabular">{value}</dd>
                </div>
              ))}
            </dl>
            <StockShare plan={data} onChange={setData} />
          </div>
        </Card>
      </div>

      <Card
        eyebrow="The next 12 months"
        title="Monthly profit on three paths"
        actions={
          <span className="label flex flex-wrap items-center gap-3 text-[10px] text-faint">
            {data.scenarios.map((s) => (
              <span key={s.key} className="flex items-center gap-1.5">
                <span className="h-0.5 w-4" style={{ background: PATH_COLOR[s.key] }} />
                {s.key === "as_now" ? "as now" : s.key === "reinvest" ? "stock" : "baki + stock"}
              </span>
            ))}
          </span>
        }
      >
        <ProjectionChart scenarios={data.scenarios} />
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          {data.scenarios.map((s) => (
            <div key={s.key} className="rounded-xl border border-line bg-page/60 p-3.5 text-sm">
              <div className="flex items-center gap-2 font-medium">
                <span className="h-0.5 w-4 shrink-0" style={{ background: PATH_COLOR[s.key] }} />
                {s.label}
              </div>
              <div className="display mt-2 text-[22px] tabular">{taka(s.total_profit_paisa, true)}</div>
              <div className="text-xs text-muted">
                profit over the year
                {s.extra_vs_now_paisa > 0 && <span className="text-ok"> · +{taka(s.extra_vs_now_paisa, true)} on now</span>}
              </div>
              <div className="mt-1 text-xs text-muted">savings on the way {taka(s.savings_paisa, true)}</div>
              <p className="mt-2 text-xs leading-relaxed text-faint">{s.assumption}</p>
            </div>
          ))}
        </div>
      </Card>

      <Card eyebrow="What would help most" title="Moves in order, each with its effect">
        {data.levers.length ? (
          <ol className="space-y-2">
            {data.levers.map((lever, i) => (
              <LeverRow key={`${lever.kind}-${lever.title}`} lever={lever} index={i + 1} />
            ))}
          </ol>
        ) : (
          <p className="text-sm text-muted">Nothing stands out: keep doing what you are doing.</p>
        )}
      </Card>

      <details className="group rounded-2xl border border-line bg-surface">
        <summary className="flex cursor-pointer list-none items-center gap-3 px-5 py-4 text-[15px] font-medium [&::-webkit-details-marker]:hidden">
          How the growth plan is worked out
        </summary>
        <ul className="list-disc space-y-1.5 px-5 pb-5 pl-10 text-sm text-muted">
          {data.method.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}
