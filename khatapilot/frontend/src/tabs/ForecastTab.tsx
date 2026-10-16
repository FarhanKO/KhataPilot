import { CheckCircle2, ChevronDown, CircleDashed } from "lucide-react";
import { useState } from "react";

import { api, errorText } from "../api";
import { ForecastChart } from "../components/ForecastChart";
import { dayLabel, taka } from "../format";
import { useData } from "../hooks";
import { useApp } from "../state";
import type { CashForecast } from "../types";
import { Amount, Badge, Button, Card, Loading, Marker, Note, Page, PageHeader, Segmented, Stat, tdClass, thClass } from "../ui";

export function ForecastTab() {
  const { version, toast, refresh } = useApp();
  const [days, setDays] = useState(14);
  const [checking, setChecking] = useState(false);
  const { data, error } = useData<CashForecast>(`/forecast?days=${days}`, [version]);

  async function check() {
    setChecking(true);
    try {
      const result = await api.post<{ alerted: boolean; already_alerted?: boolean; headline: string }>("/sim/cash-check");
      toast(
        result.alerted ? "Alert sent to the owner." : result.already_alerted ? "Already alerted today." : result.headline,
        result.alerted ? "warn" : "info",
      );
      refresh();
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setChecking(false);
    }
  }

  if (error) return <Page><Note tone="bad">{error}</Note></Page>;
  if (!data) return <Page><Loading label="Projecting cash" /></Page>;
  const short = data.first_shortfall;

  return (
    <Page>
      <PageHeader
        eyebrow="Cash forecast"
        title={`Cash for the next ${data.days_ahead} days`}
        description="A day-by-day projection built from your own books: weekday sales, bills, supplier credit, scheduled payments and baki coming back."
        actions={
          <>
            <Segmented
              value={days}
              onChange={setDays}
              options={[14, 21, 28].map((d) => ({ value: d, label: `${d} days` }))}
            />
            <Button variant="secondary" size="sm" busy={checking} onClick={check}>
              Run the daily check
            </Button>
          </>
        }
      />

      <div className={`rounded-2xl border px-5 py-4 ${short ? "border-bad/35 bg-bad-soft" : "border-ok/30 bg-ok-soft"}`}>
        <div className={`label flex items-center gap-2 ${short ? "text-bad" : "text-ok"}`}>
          <Marker tone={short ? "bad" : "ok"} />
          {short ? "Shortfall ahead" : "No shortfall"}
        </div>
        <p className="mt-1.5 text-[16px] leading-snug font-medium">{data.headline}</p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="On hand now" value={<Amount paisa={data.start_balance_paisa} />} />
        <Stat
          label="First shortfall"
          value={short ? dayLabel(short.day) : "None"}
          hint={short ? `${taka(short.short_by_paisa, true)} below the buffer` : "stays above the buffer"}
          tone={short ? "bad" : "ok"}
        />
        <Stat
          label="Lowest point"
          value={<Amount paisa={data.lowest_balance_paisa} />}
          hint={dayLabel(data.lowest_day)}
          tone={data.lowest_balance_paisa < 0 ? "bad" : undefined}
        />
        <Stat label="Safety buffer" value={<Amount paisa={data.buffer_paisa} />} hint="the least you want on hand" />
      </div>

      <Card
        eyebrow="Projection"
        title="Projected cash, day by day"
        actions={
          <span className="label flex items-center gap-4 text-[10px] text-faint">
            <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-ink" />expected</span>
            <span className="flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-sm bg-ink/10" />80% range</span>
            <span className="flex items-center gap-1.5"><span className="w-4 border-t-[1.5px] border-dashed border-warn" />buffer</span>
          </span>
        }
      >
        <ForecastChart forecast={data} />
      </Card>

      {data.suggestions.length > 0 && (
        <Card eyebrow="What would fix it" title="Each option re-run through the same forecast">
          <ul className="space-y-2">
            {data.suggestions.map((s) => (
              <li key={s.kind} className="flex items-start gap-3 rounded-xl border border-line bg-page/60 p-3.5 text-sm">
                {s.closes_gap ? (
                  <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-ok" />
                ) : (
                  <CircleDashed className="mt-0.5 size-5 shrink-0 text-faint" />
                )}
                <div className="flex-1">
                  <div>{s.text}</div>
                  <div className="mt-1 text-xs text-muted">
                    Lowest point would be {taka(s.new_lowest_paisa, true)}
                    {s.closes_gap ? ", above your buffer." : ", still below your buffer."}
                  </div>
                </div>
                <Badge tone={s.closes_gap ? "ok" : "neutral"}>{s.closes_gap ? "fixes it" : "helps"}</Badge>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card eyebrow="Detail" title="Day by day" flush>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-y border-line bg-raised/50">
              <tr>
                <th className={thClass}>Day</th>
                <th className={`${thClass} text-right`}>In</th>
                <th className={`${thClass} text-right`}>Out</th>
                <th className={`${thClass} text-right`}>Balance</th>
                <th className={thClass}>Known amounts</th>
              </tr>
            </thead>
            <tbody>
              {data.days.map((d) => (
                <tr key={d.day} className={`border-t border-line first:border-0 ${d.below_buffer ? "bg-bad-soft/70" : ""}`}>
                  <td className={`${tdClass} whitespace-nowrap`}>
                    {dayLabel(d.day)}
                    {d.below_buffer && <Badge tone="bad" className="ml-2">short</Badge>}
                  </td>
                  <td className={`${tdClass} text-right text-muted`}><Amount paisa={d.expected_in_paisa} /></td>
                  <td className={`${tdClass} text-right text-muted`}><Amount paisa={d.expected_out_paisa} /></td>
                  <td className={`${tdClass} text-right font-medium ${d.below_buffer ? "text-bad" : ""}`}>
                    <Amount paisa={d.balance_paisa} />
                  </td>
                  <td className={`${tdClass} text-xs text-muted`}>
                    {d.items.map((i) => `${i.direction === "out" ? "−" : "+"}${taka(i.amount_paisa, true)} ${i.label}`).join("; ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <details className="group rounded-2xl border border-line bg-surface">
        <summary className="flex cursor-pointer list-none items-center gap-3 px-5 py-4 [&::-webkit-details-marker]:hidden">
          <span className="label flex items-center gap-2 text-muted">
            <Marker />
            Method
          </span>
          <span className="text-[15px] font-medium">How this forecast is made</span>
          <ChevronDown className="ml-auto size-4 text-faint transition group-open:rotate-180" />
        </summary>
        <ul className="list-disc space-y-1.5 px-5 pb-5 pl-10 text-sm text-muted">
          {data.method.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      </details>
    </Page>
  );
}
