import { Printer, ShieldCheck, X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { dateTimeLabel, dayLabel, taka } from "../format";
import type { LenderReport, MonthlyReport, OwnerReport, ReportIntegrity, ReportLine, ReportShop } from "../types";

const money = (paisa: number | null | undefined) => (paisa === null || paisa === undefined ? "–" : taka(paisa, true));
const monthYear = (iso: string) =>
  new Date(`${iso}T12:00:00+06:00`).toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "Asia/Dhaka" });

function Section({ title, children, note }: { title: string; children: ReactNode; note?: string }) {
  return (
    <section className="report-section mt-7 break-inside-avoid">
      <h3 className="border-b border-ink/70 pb-1 text-[13px] font-semibold tracking-wide text-ink uppercase">{title}</h3>
      <div className="mt-2.5 text-[13px] leading-relaxed">{children}</div>
      {note && <p className="mt-1.5 text-[11.5px] text-muted">{note}</p>}
    </section>
  );
}

function Kpis({ items }: { items: { label: string; value: string; hint?: string }[] }) {
  return (
    <div className="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-5">
      {items.map((k) => (
        <div key={k.label} className="bg-surface px-3 py-2.5">
          <div className="text-[10.5px] tracking-wide text-muted uppercase">{k.label}</div>
          <div className="mt-0.5 text-[16px] font-semibold tabular">{k.value}</div>
          {k.hint && <div className="text-[11px] text-muted">{k.hint}</div>}
        </div>
      ))}
    </div>
  );
}

function Lines({ lines, total }: { lines: ReportLine[]; total?: string }) {
  const sum = lines.reduce((s, l) => s + l.paisa, 0);
  return (
    <table className="w-full text-[13px]">
      <tbody>
        {lines.map((l) => (
          <tr key={l.label} className="border-b border-line/70">
            <td className="py-1.5 pr-3">
              {l.label}
              {l.note && <div className="text-[11px] text-muted">{l.note}</div>}
            </td>
            <td className="py-1.5 text-right align-top tabular">{money(l.paisa)}</td>
          </tr>
        ))}
        {total && (
          <tr className="font-semibold">
            <td className="pt-2">{total}</td>
            <td className="pt-2 text-right tabular">{money(sum)}</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function Table({ head, rows, right = [] }: { head: string[]; rows: (string | number)[][]; right?: number[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[480px] text-[12.5px]">
        <thead>
          <tr className="border-b border-ink/40 text-left text-[11px] tracking-wide text-muted uppercase">
            {head.map((h, i) => (
              <th key={h} className={`py-1.5 pr-3 font-medium ${right.includes(i) ? "text-right" : ""}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r} className="border-b border-line/70">
              {row.map((cell, i) => (
                <td key={i} className={`py-1.5 pr-3 ${right.includes(i) ? "text-right tabular" : ""}`}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Header({ report, shop, subtitle }: { report: MonthlyReport; shop: ReportShop; subtitle: string }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4 border-b-2 border-ink pb-4">
      <div>
        <div className="text-[11px] tracking-[0.14em] text-muted uppercase">{subtitle}</div>
        <h1 className="mt-1 text-[24px] leading-tight font-semibold">{shop.name}</h1>
        <div className="mt-0.5 text-[15px]">{report.title}</div>
        <div className="mt-1 text-[12px] text-muted">
          {shop.owner_name} · {shop.shop_type} · {shop.place} · records since {monthYear(shop.trading_since)}
        </div>
      </div>
      <div className="text-right text-[11.5px] text-muted">
        <div>Period: {report.period}</div>
        <div>Made {dateTimeLabel(report.generated_at)}</div>
        <div className="font-mono">{report.id}</div>
        {shop.accounts.length > 0 && <div className="mt-1">{shop.accounts.join(" · ")}</div>}
      </div>
    </header>
  );
}

function Integrity({ integrity }: { integrity: ReportIntegrity }) {
  const p = integrity.provider;
  return (
    <Section title="How these numbers can be checked">
      <ul className="space-y-1">
        <li>
          <ShieldCheck className="mr-1 inline size-3.5" />
          Books against the bKash, Nagad and bank statements ({dateTimeLabel(p.checked)}):{" "}
          {!p.reachable ? "the provider could not be reached, so this was not checked" : p.matches ? "they match" : `${p.problems.length} difference${p.problems.length === 1 ? "" : "s"}: ${p.problems.join(" ")}`}
          .
        </li>
        <li>
          Audit log: {integrity.audit.entries} entries, hash chain {integrity.audit.verified ? "verified intact" : "BROKEN"} (head{" "}
          <span className="font-mono">{integrity.audit.head}</span>).
        </li>
        <li>
          Fingerprint of this report's figures: <span className="font-mono text-[11px] break-all">{integrity.fingerprint}</span>. The same
          books give the same fingerprint, so a copy can be checked against a fresh one.
        </li>
      </ul>
    </Section>
  );
}

function OwnerSheet({ r }: { r: OwnerReport }) {
  const f = r.figures;
  const prev = r.previous;
  const change =
    prev && prev.cash_profit_paisa > 0 ? `${Math.round((f.cash_profit_paisa / prev.cash_profit_paisa - 1) * 100)}% on ${prev.label}` : undefined;
  return (
    <>
      <Header report={r} shop={r.shop} subtitle="Monthly statement · for the owner" />
      <p className="mt-4 text-[14.5px] leading-relaxed">{r.headline}</p>
      <Kpis
        items={[
          { label: "Sales", value: money(f.sales_paisa), hint: `${money(f.sales_mobile_paisa)} by mobile money` },
          { label: "Baki paid back", value: money(f.baki_back_paisa) },
          { label: "Cash profit", value: money(f.cash_profit_paisa), hint: change },
          { label: "Gross margin", value: f.gross_margin_pct === null ? "–" : `${f.gross_margin_pct}%`, hint: "this month" },
          { label: "Cash at month end", value: money(f.closing_cash_paisa), hint: "cash and accounts" },
        ]}
      />
      <div className="grid gap-x-8 sm:grid-cols-2">
        <Section title="Money in">
          <Lines lines={r.money_in} total="Total in" />
        </Section>
        <Section title="Money out for the shop">
          <Lines lines={r.money_out} total="Total out" />
        </Section>
      </div>
      <Section title="Your own money" note="Household money and savings are yours, so they are not costs and do not lower profit.">
        <Lines lines={r.own_money} />
      </Section>
      <Section title="Week by week">
        <Table
          head={["Week", "Money in", "Money out", "Cash profit"]}
          right={[1, 2, 3]}
          rows={r.weeks.map((w) => [w.label, money(w.money_in_paisa), money(w.money_out_paisa), money(w.profit_paisa)])}
        />
      </Section>
      <Section title="Baki (credit to customers)" note="The list is who owes now, not at the month's end.">
        <p>
          Given on credit {money(r.baki.given_paisa)}, paid back {money(r.baki.back_paisa)}, owed at the month's end{" "}
          {money(r.baki.outstanding_end_paisa)}. Now {r.baki.customers_owing_now} customers owe {money(r.baki.owed_now_paisa)}
          {r.baki.over_60_days_now_paisa > 0 && <>, {money(r.baki.over_60_days_now_paisa)} of it for over 60 days</>}.
        </p>
        {r.baki.top_now.length > 0 && (
          <Table head={["Customer", "Owes now", "Days"]} right={[1, 2]} rows={r.baki.top_now.map((d) => [d.name, money(d.owed_paisa), d.days ?? "–"])} />
        )}
      </Section>
      <Section title="Bills and supplier credit" note="Open bills are as of now.">
        <p>
          Owed to suppliers at the month's end: {money(r.bills.payables_end_paisa)}. Open bills now: {money(r.bills.open_now_paisa)}.
        </p>
        {r.bills.open_now.length > 0 && (
          <Table
            head={["To", "Amount", "Due"]}
            right={[1]}
            rows={r.bills.open_now.map((b) => [`${b.payee}${b.priority ? " (priority)" : ""}`, money(b.amount_paisa), `${dayLabel(b.due)}${b.overdue ? ", overdue" : ""}`])}
          />
        )}
      </Section>
      <Section title="Payments made through KhataPilot">
        <p>
          {r.payments.count} completed, {money(r.payments.total_paisa)} in all
          {r.payments.not_completed > 0 && <>; {r.payments.not_completed} not completed</>}.
        </p>
        {r.payments.items.length > 0 && (
          <Table
            head={["Day", "To", "Amount", "Status"]}
            right={[2]}
            rows={r.payments.items.map((p) => [dayLabel(p.day), p.purpose === "savings" ? "Your savings" : p.to, money(p.amount_paisa), p.status])}
          />
        )}
      </Section>
      <Section title="Savings and goals">
        <p>
          {r.savings.share_pct ? `You save ${r.savings.share_pct}% of profit. ` : "No savings plan is on. "}
          {r.savings.share_pct && r.savings.target_paisa !== null && (
            <>At that share this month's target is {money(r.savings.target_paisa)}; </>
          )}
          {money(r.savings.saved_paisa)} moved this month, {money(r.savings.saved_total_paisa)} saved in all.
        </p>
        {r.savings.goals.length > 0 && (
          <ul className="mt-1.5 list-disc pl-5">
            {r.savings.goals.map((g) => (
              <li key={g.name}>{g.text}</li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="What the guard did this month">
        <p>
          {r.security.blocked} blocked, {r.security.held_for_you} held for your approval, {r.security.alerts} alerts,{" "}
          {r.security.wrong_pin} wrong PIN tries, STOP used {r.security.stop_used} time{r.security.stop_used === 1 ? "" : "s"}.
        </p>
      </Section>
      <Section title="Looking ahead (now)">
        <p>{r.forecast}</p>
        {r.growth && (
          <>
            <p className="mt-1.5">
              {r.growth.stage}: {r.growth.text}
            </p>
            <ol className="mt-1.5 list-decimal pl-5">
              {r.growth.moves.map((m) => (
                <li key={m.title}>
                  <span className="font-medium">{m.title}:</span> {m.text}
                </li>
              ))}
            </ol>
          </>
        )}
      </Section>
      <Integrity integrity={r.integrity} />
      <Section title="Notes">
        <ul className="list-disc pl-5 text-[12px] text-muted">
          {r.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      </Section>
    </>
  );
}

function LenderSheet({ r }: { r: LenderReport }) {
  const a = r.averages;
  const c = r.customer_credit;
  const o = r.obligations;
  return (
    <>
      <Header report={r} shop={r.shop} subtitle="Business statement · for a bank or NGO" />
      <p className="mt-4 text-[14.5px] leading-relaxed">{r.headline}</p>
      <Kpis
        items={[
          { label: "Sales a month", value: money(a.sales_paisa), hint: `average of ${a.months}` },
          { label: "Cash profit a month", value: money(a.cash_profit_paisa) },
          { label: "Gross margin", value: a.gross_margin_pct === null ? "–" : `${a.gross_margin_pct}%` },
          { label: "Profit", value: a.steadiness, hint: a.profit_swing_pct !== null ? `swings ${a.profit_swing_pct}% around the middle` : undefined },
          {
            label: "Sales trend",
            value: a.sales_trend_pct_a_month === null ? "–" : `${a.sales_trend_pct_a_month > 0 ? "+" : ""}${a.sales_trend_pct_a_month}%`,
            hint: "a month",
          },
        ]}
      />
      <Section title="Month by month" note={`Best month ${a.best.month} (${money(a.best.profit_paisa)}), weakest ${a.worst.month} (${money(a.worst.profit_paisa)}).`}>
        <Table
          head={["Month", "Sales", "Stock bought", "Running costs", "Cash profit", "Margin", "Cash at end"]}
          right={[1, 2, 3, 4, 5, 6]}
          rows={r.months.map((m) => [
            m.label,
            money(m.sales_paisa + m.baki_given_paisa),
            money(m.stock_bought_paisa),
            money(m.running_costs_paisa),
            money(m.cash_profit_paisa),
            m.gross_margin_pct === null ? "–" : `${m.gross_margin_pct}%`,
            money(m.closing_cash_paisa),
          ])}
        />
      </Section>
      <div className="grid gap-x-8 sm:grid-cols-2">
        <Section title="Credit given to customers">
          <p>
            Over these months {money(c.given_paisa)} was given on credit and {money(c.paid_back_paisa)} paid back
            {c.paid_back_pct !== null && <> ({c.paid_back_pct}%)</>}. Owed at the end: {money(c.outstanding_end_paisa)}; now{" "}
            {c.customers_owing_now} customers owe, {money(c.over_60_days_now_paisa)} of it for over 60 days.
          </p>
        </Section>
        <Section title="Suppliers and other debts">
          <p>
            Owed to suppliers at the end: {money(o.supplier_payables_end_paisa)}. Supplier payments on time:{" "}
            {o.on_time_pct === null ? "no record" : `${o.on_time_pct}% (${o.supplier_payments_on_time} of ${o.supplier_payments_on_time + o.supplier_payments_late})`}.
            Informal loans still owed: {money(o.informal_loans_outstanding_paisa)}.
          </p>
          {o.applications_through_khatapilot.length > 0 && (
            <p className="mt-1">
              Loan applications made through KhataPilot:{" "}
              {o.applications_through_khatapilot.map((x) => `${x.lender} ${money(x.amount_paisa)} (${x.status}, ${dayLabel(x.day)})`).join("; ")}.
            </p>
          )}
        </Section>
      </div>
      <Section title="What the business can repay">
        <p>{r.capacity.text}</p>
      </Section>
      <Section title="Credit profile">
        {r.credit_profile.included ? (
          <>
            <p>
              Band <span className="font-semibold">{r.credit_profile.band}</span> ({r.credit_profile.band_words}). {r.credit_profile.summary}
            </p>
            <p className="mt-1 text-[12px] text-muted">
              Helps: {r.credit_profile.helps.join(", ") || "–"}. Hurts: {r.credit_profile.hurts.join(", ") || "–"}. Built{" "}
              {dayLabel(r.credit_profile.built)} from {r.credit_profile.history_days} days of books ({r.credit_profile.model}).
            </p>
          </>
        ) : (
          <p>{r.credit_profile.why}</p>
        )}
      </Section>
      <Section title="Savings record">
        <p>
          {r.savings_record.share_pct ? `Saves ${r.savings_record.share_pct}% of profit each month. ` : "No monthly savings plan. "}
          Saved in {r.savings_record.months_saved} of {r.savings_record.months} months: {money(r.savings_record.saved_paisa)}; {money(r.savings_record.saved_total_paisa)} in
          all.
        </p>
      </Section>
      <Integrity integrity={r.integrity} />
      <Section title="Owner's declaration">
        <p>{r.declaration}</p>
        <div className="mt-8 grid gap-8 text-[12px] text-muted sm:grid-cols-3">
          {["Owner's signature", "Date", "Received by (lender)"].map((label) => (
            <div key={label}>
              <div className="h-8 border-b border-ink/60" />
              <div className="mt-1">{label}</div>
            </div>
          ))}
        </div>
      </Section>
      <Section title="Notes">
        <ul className="list-disc pl-5 text-[12px] text-muted">
          {r.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      </Section>
    </>
  );
}

/** The report as a printable page over the app: Print opens the browser's print dialog (or "Save as PDF"). */
export function ReportView({ report, onClose }: { report: MonthlyReport; onClose: () => void }) {
  useEffect(() => {
    const before = document.title;
    document.title = `${report.shop.name} - ${report.title}`; // the PDF's default file name
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      document.title = before;
      window.removeEventListener("keydown", onKey);
    };
  }, [report, onClose]);

  return createPortal(
    <div id="print-root" role="dialog" aria-modal="true" aria-label={report.title} className="fixed inset-0 z-[60] overflow-y-auto bg-page">
      <div className="no-print sticky top-0 z-10 flex items-center gap-2 border-b border-line bg-surface/95 px-4 py-2.5 backdrop-blur sm:px-6">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{report.title}</span>
        <button
          onClick={() => window.print()}
          className="inline-flex items-center gap-1.5 rounded-full border border-ink bg-brand px-3.5 py-1.5 text-xs font-medium text-brand-ink transition hover:opacity-90"
        >
          <Printer className="size-3.5" /> Print or save as PDF
        </button>
        <button onClick={onClose} aria-label="Close the report" className="rounded-full p-2 text-muted hover:bg-raised hover:text-ink">
          <X className="size-4" />
        </button>
      </div>
      <article className="print-sheet mx-auto my-6 max-w-[860px] rounded-xl border border-line bg-surface px-6 py-8 text-ink shadow-sm sm:px-10">
        {report.kind === "owner" ? <OwnerSheet r={report} /> : <LenderSheet r={report} />}
        <footer className="mt-10 border-t border-line pt-3 text-[11px] text-muted">
          Made by KhataPilot from the shop's own books. Not audited accounts. {report.id}
        </footer>
      </article>
    </div>,
    document.body,
  );
}
