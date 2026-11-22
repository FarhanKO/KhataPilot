import { Bell, BellOff, BellRing, ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";

import { api, errorText } from "../api";
import { PhoneSmsCard } from "../components/PhoneSmsCard";

import { dayLabel, timeLabel, titleCase } from "../format";
import { useData } from "../hooks";
import { useApp } from "../state";
import type { DayTotals, Debtor, LedgerEntry, Obligation, Payment, Reminder, ReminderOptOut, ToolOutcome } from "../types";
import { Amount, Badge, Button, Card, Empty, Eyebrow, Loading, Note, Page, PageHeader, Stat, tdClass, thClass } from "../ui";

const FIRST = 10; // a day's entries shown before "More"
const MORE = 15;
const MAX = 500; // the API caps limit here

type DayEntries = {
  total: number;
  day: string | null;
  newer_day: string | null;
  older_day: string | null;
  day_total: number;
  entries: LedgerEntry[];
};
const METHODS: Record<string, string> = { cash: "Cash", bkash: "bKash", nagad: "Nagad", bank: "Bank", credit: "Credit" };

// Bills worth a second look get one red underline each:
// - the payee's name when the bill is due within NEAR_DAYS of the shop's today (overdue included), or when the
//   owner marked it priority in chat and it is due within PRIORITY_DAYS;
// - otherwise the amount, when it is a spike: at least SPIKE_TIMES the typical bill, where typical is the median
//   of the OTHER bills whose names are not underlined. The median ignores a few big ones, so "eight bills
//   around ৳5,000 and two at ৳40,000" flags the two. A lone bill has nothing to compare with.
const NEAR_DAYS = 5;
const PRIORITY_DAYS = 7;
const SPIKE_TIMES = 3;
const LATE_DAYS = 45; // baki with no promised day turns red after this long
const RED_UNDERLINE = "underline decoration-bad decoration-2 underline-offset-4";

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/** Whole days from one ISO date to another; negative when `to` is earlier. */
function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000);
}

export function LedgerTab() {
  const { version, refresh, review, toast } = useApp();
  const [day, setDay] = useState<string | null>(null); // null: the latest day with entries
  const [shown, setShown] = useState(FIRST);
  const [preparing, setPreparing] = useState(false);
  const today = useData<{ totals: DayTotals; balances: Record<string, number>; total_on_hand: number }>("/ledger/today", [version]);
  const entries = useData<DayEntries>(`/ledger/entries?by_day=true&limit=${shown}${day ? `&day=${day}` : ""}`, [version]);
  const debtors = useData<Debtor[]>("/debtors", [version]);
  const obligations = useData<Obligation[]>("/obligations", [version]);
  const payments = useData<Payment[]>("/payments", [version]);
  const reminders = useData<{ reminders: Reminder[]; opted_out: ReminderOptOut[]; paid: number; part_paid: number; waiting: number }>(
    "/reminders",
    [version],
  );
  const [optBusy, setOptBusy] = useState<string | null>(null);

  /** Stop (or allow again) baki reminders to one customer, through the guard like the same words in chat. */
  async function setReminders(name: string, stop: boolean) {
    setOptBusy(name);
    try {
      const out = await api.put<ToolOutcome>("/reminders/opt-out", { customer_name: name, stop });
      if (out.status === "ok") toast(stop ? `No more reminders to ${name}.` : `${name} can get reminders again.`, "ok");
      else toast(out.message, "bad");
      refresh();
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setOptBusy(null);
    }
  }

  async function prepareReminders() {
    setPreparing(true);
    try {
      const out = await api.post<ToolOutcome>("/reminders/prepare", { min_days: 14 });
      if (out.status === "held" && out.pending_id) review(out.pending_id);
      else toast(out.message, out.status === "blocked" ? "bad" : "info");
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setPreparing(false);
      refresh();
    }
  }

  if (today.error) return <Page><Note tone="bad">{today.error}</Note></Page>;
  if (!today.data) return <Page><Loading /></Page>;
  const t = today.data.totals;
  const owed = (debtors.data ?? []).reduce((sum, d) => sum + d.outstanding_paisa, 0);
  // Longest-owed first, so the baki most worth chasing leads the list; ties go to the larger amount.
  const byAge = [...(debtors.data ?? [])].sort(
    (a, b) => (b.days_outstanding ?? 0) - (a.days_outstanding ?? 0) || b.outstanding_paisa - a.outstanding_paisa,
  );
  const bills = obligations.data ?? [];
  const nameFlag = (o: Obligation): string | null => {
    const days = daysBetween(t.day, o.due_date);
    if (days <= NEAR_DAYS) return days < 0 ? "Overdue" : `Due within ${NEAR_DAYS} days`;
    if (o.priority && days <= PRIORITY_DAYS) return `Priority, due within ${PRIORITY_DAYS} days`;
    return null;
  };
  const rest = bills.filter((o) => !nameFlag(o));
  /** The median of the other bills whose names are not underlined, or null when there are none. */
  const typicalBesides = (o: Obligation): number | null => {
    const others = rest.filter((x) => x.id !== o.id).map((x) => x.amount_paisa);
    return others.length ? median(others) : null;
  };
  const book = entries.data;
  const rows = book?.entries.length ?? 0;
  const dayTotal = book?.day_total ?? 0;
  const switching = !!book && day !== null && book.day !== day;
  const loadingMore = !!book && !switching && rows < Math.min(shown, dayTotal, MAX);
  const hasMore = rows < Math.min(dayTotal, MAX);
  function goTo(next: string | null) {
    if (!next) return;
    setDay(next);
    setShown(FIRST);
  }

  return (
    <Page>
      <PageHeader
        eyebrow="Ledger"
        title={`Today, ${dayLabel(t.day)}`}
        description="From the shop's books, including what you told KhataPilot and the SMS you forwarded."
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Sales today" value={<Amount paisa={t.sales_paisa} />} hint="cash and mobile money" />
        <Stat
          label="Baki given / back"
          value={
            <>
              <Amount paisa={t.baki_given_paisa} /> <span className="text-faint">/</span> <Amount paisa={t.baki_collected_paisa} />
            </>
          }
        />
        <Stat
          label="Money in / out"
          value={
            <>
              <Amount paisa={t.money_in_paisa} /> <span className="text-faint">/</span> <Amount paisa={t.money_out_paisa} />
            </>
          }
        />
        <Stat
          label="On hand now"
          value={<Amount paisa={today.data.total_on_hand} />}
          tone={today.data.total_on_hand < 500000 ? "warn" : undefined}
          hint="all accounts together"
        />
      </div>

      <div className="space-y-3">
        <Eyebrow>Where the money is</Eyebrow>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {Object.entries(today.data.balances).map(([method, paisa]) => (
            <div key={method} className="flex items-baseline justify-between gap-2 rounded-xl border border-line bg-surface px-4 py-3">
              <span className="label text-faint">{METHODS[method] ?? method}</span>
              <Amount paisa={paisa} className="text-[17px] font-medium" />
            </div>
          ))}
        </div>
      </div>

      <Card
        eyebrow={`Entries · ${book?.total.toLocaleString() ?? "…"} in the books`}
        title={book?.day ? `${dayLabel(book.day)} · ${dayTotal.toLocaleString()} ${dayTotal === 1 ? "entry" : "entries"}` : "No entries yet"}
        flush
        actions={
          <>
            <Button variant="secondary" size="sm" disabled={!book?.newer_day || switching} onClick={() => goTo(book?.newer_day ?? null)}>
              <ChevronLeft className="size-3.5" /> Newer day
            </Button>
            <Button variant="secondary" size="sm" disabled={!book?.older_day || switching} onClick={() => goTo(book?.older_day ?? null)}>
              Older day <ChevronRight className="size-3.5" />
            </Button>
          </>
        }
      >
        {!entries.data ? (
          <div className="px-5"><Loading /></div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-y border-line bg-raised/50">
                  <tr>
                    <th className={thClass}>When</th>
                    <th className={thClass}>What</th>
                    <th className={thClass}>Who</th>
                    <th className={thClass}>Product note</th>
                    <th className={thClass}>Method</th>
                    <th className={`${thClass} text-right`}>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.data.entries.map((e) => (
                    <tr key={e.id} className="border-t border-line first:border-0 hover:bg-raised/40">
                      <td className={`${tdClass} font-mono text-[11.5px] whitespace-nowrap text-muted`}>
                        {timeLabel(e.occurred_at)}
                      </td>
                      <td className={tdClass}>
                        <div className="flex flex-wrap items-center gap-1.5">
                          {titleCase(e.kind)}
                          {e.source !== "seed" && <Badge tone="brand">{e.source}</Badge>}
                        </div>
                        <div className="text-xs text-faint">{e.category}</div>
                      </td>
                      <td className={`${tdClass} text-xs`}>
                        {e.counterparty_name ?? ""}
                        {e.counterparty_number && <div className="font-mono text-faint">{e.counterparty_number}</div>}
                      </td>
                      <td className={`${tdClass} max-w-56 text-xs`}>
                        {e.product_note ? (
                          <span className="break-words">{e.product_note}</span>
                        ) : (
                          <span className="text-faint" title="Say the products in chat to keep a note, e.g. “2 kg chini, 1 Lux soap”">—</span>
                        )}
                      </td>
                      <td className={`${tdClass} text-xs`}>{METHODS[e.method] ?? e.method}</td>
                      <td className={`${tdClass} text-right font-medium whitespace-nowrap ${e.direction === "in" ? "text-ok" : ""}`}>
                        {e.direction === "in" ? "+" : "−"}
                        <Amount paisa={e.amount_paisa} whole={false} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {(hasMore || loadingMore) && (
              <div className="border-t border-line px-5 py-3">
                <Button
                  variant="secondary"
                  size="md"
                  className="w-full"
                  busy={loadingMore}
                  onClick={() => setShown((n) => Math.min(n + MORE, MAX))}
                >
                  More <span className="font-normal text-faint">· {rows.toLocaleString()} of {dayTotal.toLocaleString()} shown</span>
                </Button>
              </div>
            )}
          </>
        )}
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card
          eyebrow="Baki"
          className="border-bad/60! shadow-[0_0_0_3px_color-mix(in_srgb,var(--bad)_10%,transparent)]"
          title={<>Who owes you · <Amount paisa={owed} /></>}
          actions={
            <Button variant="secondary" size="sm" busy={preparing} onClick={prepareReminders}>
              <BellRing className="size-3.5" /> Prepare reminders
            </Button>
          }
        >
          {!debtors.data ? (
            <Loading />
          ) : debtors.data.length === 0 ? (
            <Empty>Nobody owes you money.</Empty>
          ) : (
            <ul className="divide-y divide-line">
              {byAge.slice(0, 12).map((d) => {
                // With a promised day, late means that day has passed; without one, owed for over LATE_DAYS.
                const late = d.expected_by
                  ? daysBetween(t.day, d.expected_by) < 0
                  : (d.days_outstanding ?? 0) > LATE_DAYS;
                return (
                  <li key={d.name} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="min-w-0 truncate">{d.name}</span>
                      {d.number && (
                        <button
                          onClick={() => setReminders(d.name, !d.no_reminders)}
                          disabled={optBusy === d.name || d.no_reminders === "customer"}
                          aria-label={d.no_reminders ? `Allow reminders to ${d.name} again` : `Stop reminders to ${d.name}`}
                          title={
                            d.no_reminders === "customer"
                              ? "They replied STOP to a reminder; only they can turn reminders back on (by replying START)"
                              : d.no_reminders
                                ? "You stopped reminders to them. Tap to allow them again"
                                : "Tap to stop baki reminders to them"
                          }
                          className={`inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[10.5px] transition disabled:cursor-default ${
                            d.no_reminders ? "bg-raised text-muted" : "text-faint hover:bg-raised hover:text-ink"
                          }`}
                        >
                          {d.no_reminders ? <BellOff className="size-3" /> : <Bell className="size-3" />}
                          {d.no_reminders && <span className="label">no SMS</span>}
                        </button>
                      )}
                    </span>
                    <span className="flex shrink-0 items-center gap-3">
                      {d.expected_by && (
                        <span
                          className={`label text-[10.5px] ${late ? "text-bad" : "text-faint"}`}
                          title={late ? "The day they promised to pay back has passed" : "The day they promised to pay back"}
                        >
                          {late ? "was due" : "back by"} {dayLabel(d.expected_by)}
                        </span>
                      )}
                      <Badge tone={late ? "bad" : "neutral"}>{d.days_outstanding} days</Badge>
                      <Amount paisa={d.outstanding_paisa} className={`w-20 text-right font-medium ${late ? "text-bad" : ""}`} />
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
        <Card eyebrow="Payables" title="Bills and supplier credit due">
          {!obligations.data ? (
            <Loading />
          ) : obligations.data.length === 0 ? (
            <Empty>No open bills.</Empty>
          ) : (
            <ul className="divide-y divide-line">
              {obligations.data.map((o) => {
                const flag = nameFlag(o);
                const typicalBill = flag ? null : typicalBesides(o);
                const spike = typicalBill !== null && typicalBill > 0 && o.amount_paisa >= typicalBill * SPIKE_TIMES;
                return (
                  <li key={o.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className={`min-w-0 truncate ${flag ? `${RED_UNDERLINE} pb-1` : ""}`} title={flag ?? undefined}>
                        {o.counterparty_name}
                      </span>
                      {o.priority && <Badge tone="bad">priority</Badge>}
                    </span>
                    <span className="flex shrink-0 items-center gap-3">
                      <span className="label text-[10.5px] text-faint">due {dayLabel(o.due_date)}</span>
                      <span
                        className="w-20 text-right"
                        title={spike && typicalBill ? `About ${Math.round(o.amount_paisa / typicalBill)}× a typical bill` : undefined}
                      >
                        <Amount paisa={o.amount_paisa} className={`font-medium ${spike ? RED_UNDERLINE : ""}`} />
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </div>

      <PhoneSmsCard />

      {reminders.data && reminders.data.opted_out.length > 0 && (
        <Card eyebrow="No reminders" title={`${reminders.data.opted_out.length} customer${reminders.data.opted_out.length === 1 ? "" : "s"} asked for no SMS`}>
          <ul className="divide-y divide-line">
            {reminders.data.opted_out.map((o) => (
              <li key={o.number_masked} className="flex flex-wrap items-center justify-between gap-3 py-2.5 text-sm">
                <span className="min-w-0">
                  <span className="font-medium">{o.customer_name ?? "A customer"}</span>
                  <span className="ml-2 font-mono text-xs text-faint">{o.number_masked}</span>
                  <div className="mt-0.5 text-xs text-muted">
                    {o.source === "customer"
                      ? `Replied “${o.words ?? "STOP"}” on ${dayLabel(o.since.slice(0, 10))}. Only they can turn reminders back on, by replying START.`
                      : `You stopped reminders on ${dayLabel(o.since.slice(0, 10))}.`}
                  </div>
                </span>
                {o.owner_can_lift && o.customer_name && (
                  <Button size="sm" variant="ghost" busy={optBusy === o.customer_name} onClick={() => setReminders(o.customer_name!, false)}>
                    Allow again
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {reminders.data && reminders.data.reminders.length > 0 && (
        <Card
          eyebrow="Reminders"
          title={`Who paid since · ${reminders.data.paid} in full, ${reminders.data.part_paid} in part, ${reminders.data.waiting} not yet`}
        >
          <ul className="divide-y divide-line">
            {reminders.data.reminders.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5 text-sm">
                <span className="min-w-0">
                  <span className="font-medium">{r.customer_name}</span>
                  <span className="ml-2 font-mono text-xs text-faint">{r.to_masked}</span>
                  <div className="mt-0.5 text-xs text-muted">
                    Reminded {dayLabel(r.sent_at.slice(0, 10))} about <Amount paisa={r.owed_paisa} />
                    {r.paid_since_paisa > 0 && <> · paid <Amount paisa={r.paid_since_paisa} /> since</>}
                  </div>
                </span>
                <span className="flex items-center gap-3">
                  <Badge tone={r.outcome === "paid" ? "ok" : r.outcome === "part_paid" ? "info" : r.outcome === "not_sent" ? "bad" : "neutral"}>
                    {r.outcome === "part_paid" ? "part paid" : r.outcome === "not_sent" ? "not sent" : r.outcome}
                  </Badge>
                  <Amount paisa={r.still_owes_paisa} className="w-20 text-right font-medium" />
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card eyebrow="Payments" title="Made through KhataPilot">
        {!payments.data ? (
          <Loading />
        ) : payments.data.length === 0 ? (
          <Empty>No payments yet. Try “pay Rahim Traders 5000” in the chat.</Empty>
        ) : (
          <ul className="divide-y divide-line">
            {payments.data.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
                <span className="min-w-0">
                  <span className="font-medium">{p.payee_name ?? p.payee_number}</span>
                  <span className="ml-2 font-mono text-xs text-faint">{p.payee_number}</span>
                  <div className="mt-0.5 text-xs text-muted">
                    {METHODS[p.method]} · {p.execute_on ? dayLabel(p.execute_on) : ""} · tier {p.approved_tier}
                    {p.approved_with_pin ? " with PIN" : ""}
                    {p.sandbox_txn_id ? ` · TrxID ${p.sandbox_txn_id}` : ""}
                  </div>
                </span>
                <span className="flex items-center gap-3">
                  <Badge tone={p.status === "completed" ? "ok" : p.status === "failed" ? "bad" : p.status === "paused" ? "warn" : "info"}>
                    {p.status}
                  </Badge>
                  <Amount paisa={p.amount_paisa} className="font-medium" />
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Page>
  );
}
