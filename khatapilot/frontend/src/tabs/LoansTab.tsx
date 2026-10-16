import { ArrowRight, Crown } from "lucide-react";
import { useState } from "react";

import { api, errorText } from "../api";
import { dateTimeLabel, pct, taka } from "../format";
import { useData } from "../hooks";
import { useApp } from "../state";
import type { LoanApplication, LoanMatch, LoanOptions, Pending, ToolOutcome } from "../types";
import { Amount, Badge, Button, Card, Field, inputClass, Loading, Marker, Note, Page, PageHeader } from "../ui";

const PURPOSES: [string, string][] = [
  ["eid_stock", "Extra stock before Eid"],
  ["stock", "Buying stock"],
  ["cover_shortfall", "Covering a cash shortfall"],
  ["working_capital", "Working capital"],
  ["equipment", "Equipment"],
  ["shop_repair", "Shop repairs"],
];

function ProductCard({ m, recommended, purpose, requested }: { m: LoanMatch; recommended: boolean; purpose: string; requested: number }) {
  const { review, toast, refresh } = useApp();
  const [busy, setBusy] = useState(false);
  const p = m.product;

  async function apply() {
    setBusy(true);
    try {
      const outcome = await api.post<ToolOutcome>("/loans/apply", {
        product_id: p.id,
        amount_taka: Math.round(Math.min(requested, m.amount_paisa) / 100),
        purpose,
      });
      if (outcome.status === "held" && outcome.pending_id) {
        refresh();
        review(outcome.pending_id);
      } else {
        toast(outcome.message, outcome.status === "ok" ? "ok" : "bad");
      }
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={`flex flex-col rounded-2xl bg-surface p-5 ${recommended ? "border-2 border-ink" : "border border-line"} ${
        !m.eligible ? "opacity-65" : ""
      }`}
    >
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="label flex min-w-0 flex-1 items-start gap-2 text-faint">
          <Marker tone={recommended ? "brand" : m.eligible && m.affordable ? "ok" : "neutral"} className="mt-[5px]" />
          {p.lender_type}
        </div>
        <div className="flex flex-wrap gap-1">
          {recommended && <Badge tone="brand">Recommended</Badge>}
          {!m.eligible ? (
            <Badge tone="neutral">not yet</Badge>
          ) : !m.affordable ? (
            <Badge tone="warn">too heavy</Badge>
          ) : !recommended && m.rank ? (
            <Badge tone="ok">#{m.rank}</Badge>
          ) : null}
          <Badge tone="neutral">illustrative</Badge>
        </div>
      </div>
      <div className="mt-2 flex items-center gap-2 text-[17px] font-medium tracking-[-0.01em]">
        {recommended && <Crown className="size-4 shrink-0" />}
        {p.name}
      </div>
      <div className="mt-0.5 text-xs text-muted">
        {p.speed} · {p.collateral}
      </div>
      <p className="mt-3 text-sm leading-relaxed">{m.explanation}</p>
      {m.eligible && m.monthly_instalment_paisa !== null && (
        <div className="mt-4 grid grid-cols-3 divide-x divide-line rounded-xl border border-line bg-page/60">
          {[
            [p.flat_fee_rate ? "Repay once" : "Each month", <Amount key="i" paisa={m.monthly_instalment_paisa} />],
            ["Term", `${m.tenure_months} mo`],
            ["Total cost", <Amount key="c" paisa={m.total_cost_paisa} />],
          ].map(([label, v]) => (
            <div key={label as string} className="px-3 py-2.5">
              <div className="label text-[10px] text-faint">{label}</div>
              <div className="mt-1 text-[17px] font-medium">{v}</div>
            </div>
          ))}
        </div>
      )}
      <div className="mt-auto flex flex-wrap items-center gap-3 pt-4 text-xs text-muted">
        <span className="font-mono text-[11px]">
          {taka(p.min_taka * 100, true)}–{taka(p.max_taka * 100, true)} ·{" "}
          {p.flat_fee_rate ? `${pct(p.flat_fee_rate)} flat` : `${pct(p.annual_rate)} a year`}
          {p.fee_rate ? ` + ${pct(p.fee_rate)} fee` : ""} · bands {p.bands.join(", ")}
        </span>
        {m.eligible && m.affordable && (
          <Button variant={recommended ? "primary" : "secondary"} size="sm" className="ml-auto" busy={busy} onClick={apply}>
            Prepare application <ArrowRight className="size-3.5" />
          </Button>
        )}
      </div>
    </div>
  );
}

export function LoansTab() {
  const { version, review } = useApp();
  const [amount, setAmount] = useState("");
  const [purpose, setPurpose] = useState("eid_stock");
  const [query, setQuery] = useState({ amount: "", purpose: "eid_stock" });
  const path = `/loans/options?purpose=${query.purpose}${query.amount ? `&amount_taka=${query.amount}` : ""}`;
  const options = useData<LoanOptions>(path, [version]);
  const applications = useData<{ waiting_for_approval: Pending[]; sent: LoanApplication[] }>("/loans/applications", [version]);

  const o = options.data;
  return (
    <Page>
      <PageHeader
        eyebrow="Loans"
        title="Compare loans that fit your books"
        description="The products are illustrative samples, not offers from real lenders. Nothing is sent anywhere until you approve an application."
      />

      <Card>
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery({ amount: amount.replace(/\D/g, ""), purpose });
          }}
        >
          <div className="w-44">
            <Field label="Amount (৳, optional)">
              <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="numeric" placeholder="e.g. 30000" className={inputClass} />
            </Field>
          </div>
          <div className="w-64">
            <Field label="For">
              <select value={purpose} onChange={(e) => setPurpose(e.target.value)} className={inputClass}>
                {PURPOSES.map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Button type="submit" variant="primary">
            Compare options
          </Button>
        </form>
      </Card>

      {options.error ? (
        <Note tone={options.error.includes("Agree") ? "info" : "bad"}>
          {options.error.includes("Agree") ? "Agree to a credit profile in the Credit tab first; loan matching uses it." : options.error}
        </Note>
      ) : !o ? (
        <Loading label="Matching products" />
      ) : (
        <>
          <div className={`rounded-2xl border px-5 py-4 ${o.recommended ? "border-ok/30 bg-ok-soft" : "border-warn/30 bg-warn-soft"}`}>
            <div className={`label flex items-center gap-2 ${o.recommended ? "text-ok" : "text-warn"}`}>
              <Marker tone={o.recommended ? "ok" : "warn"} />
              Recommendation
            </div>
            <p className="mt-1.5 text-[16px] leading-snug font-medium">{o.recommendation}</p>
            <p className="mt-2 text-xs leading-relaxed text-muted">
              Request {taka(o.requested_paisa, true)} ({o.amount_basis}). Band {o.band} ({pct(o.probability_of_default, 1)}).
              Monthly business income about {taka(o.affordability.monthly_business_income_paisa, true)}; instalments up to{" "}
              {taka(o.affordability.max_instalment_paisa, true)} a month are safe.
              {o.affordability.shortfall_day && ` The forecast shows a shortfall on ${o.affordability.shortfall_day}.`}
            </p>
          </div>
          <div className="grid gap-4 xl:grid-cols-2">
            {o.matches.map((m) => (
              <ProductCard key={m.product.id} m={m} recommended={m.product.id === o.recommended} purpose={o.purpose} requested={o.requested_paisa} />
            ))}
          </div>
        </>
      )}

      <Card eyebrow="Applications" title="Prepared and sent">
        {!applications.data ? (
          <Loading />
        ) : applications.data.waiting_for_approval.length + applications.data.sent.length === 0 ? (
          <p className="text-sm text-faint">None yet.</p>
        ) : (
          <ul className="divide-y divide-line">
            {applications.data.waiting_for_approval.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-3 py-3 text-sm">
                <Badge tone="warn">waiting for you</Badge>
                <span className="flex-1">{p.summary}</span>
                <Button variant="primary" size="sm" onClick={() => review(p.id)}>
                  Review
                </Button>
              </li>
            ))}
            {applications.data.sent.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-3 py-3 text-sm">
                <Badge tone={a.status === "submitted" ? "ok" : "bad"}>{a.status}</Badge>
                <span className="flex-1">
                  {a.product_name}, {taka(a.amount_paisa, true)} over {a.tenure_months} months
                  <span className="ml-2 text-xs text-muted">
                    {a.sandbox_ref ? `lender ref ${a.sandbox_ref} (simulated)` : a.failure} · {dateTimeLabel(a.submitted_at)}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Page>
  );
}
