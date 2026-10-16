import { BellRing } from "lucide-react";
import { useState } from "react";

import { api, errorText } from "../api";

import { dayLabel, timeLabel, titleCase } from "../format";
import { useData } from "../hooks";
import { useApp } from "../state";
import type { DayTotals, Debtor, LedgerEntry, Obligation, Payment, Reminder, ToolOutcome } from "../types";
import { Amount, Badge, Button, Card, Empty, Eyebrow, Loading, Note, Page, PageHeader, Stat, tdClass, thClass } from "../ui";

const PAGE = 25;
const METHODS: Record<string, string> = { cash: "Cash", bkash: "bKash", nagad: "Nagad", bank: "Bank", credit: "Credit" };

export function LedgerTab() {
  const { version, refresh, review, toast } = useApp();
  const [page, setPage] = useState(0);
  const [preparing, setPreparing] = useState(false);
  const today = useData<{ totals: DayTotals; balances: Record<string, number>; total_on_hand: number }>("/ledger/today", [version]);
  const entries = useData<{ total: number; entries: LedgerEntry[] }>(`/ledger/entries?limit=${PAGE}&offset=${page * PAGE}`, [version]);
  const debtors = useData<Debtor[]>("/debtors", [version]);
  const obligations = useData<Obligation[]>("/obligations", [version]);
  const payments = useData<Payment[]>("/payments", [version]);
  const reminders = useData<{ reminders: Reminder[]; paid: number; part_paid: number; waiting: number }>("/reminders", [version]);

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
  const lastPage = !entries.data || (page + 1) * PAGE >= entries.data.total;

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
        eyebrow="Entries"
        title={`${entries.data?.total.toLocaleString() ?? "…"} in the books`}
        flush
        actions={
          <>
            <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
              Newer
            </Button>
            <Button variant="secondary" size="sm" disabled={lastPage} onClick={() => setPage((p) => p + 1)}>
              Older
            </Button>
          </>
        }
      >
        {!entries.data ? (
          <div className="px-5"><Loading /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-y border-line bg-raised/50">
                <tr>
                  <th className={thClass}>When</th>
                  <th className={thClass}>What</th>
                  <th className={thClass}>Who</th>
                  <th className={thClass}>Method</th>
                  <th className={`${thClass} text-right`}>Amount</th>
                </tr>
              </thead>
              <tbody>
                {entries.data.entries.map((e) => (
                  <tr key={e.id} className="border-t border-line first:border-0 hover:bg-raised/40">
                    <td className={`${tdClass} font-mono text-[11.5px] whitespace-nowrap text-muted`}>
                      {dayLabel(e.local_date)} {timeLabel(e.occurred_at)}
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
        )}
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card
          eyebrow="Baki"
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
              {debtors.data.slice(0, 12).map((d) => {
                const late = (d.days_outstanding ?? 0) > 45;
                return (
                  <li key={d.name} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                    <span className="min-w-0 truncate">{d.name}</span>
                    <span className="flex shrink-0 items-center gap-3">
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
              {obligations.data.map((o) => (
                <li key={o.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                  <span className="min-w-0 truncate">{o.counterparty_name}</span>
                  <span className="flex shrink-0 items-center gap-3">
                    <span className="label text-[10.5px] text-faint">due {dayLabel(o.due_date)}</span>
                    <Amount paisa={o.amount_paisa} className="w-20 text-right font-medium" />
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

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
