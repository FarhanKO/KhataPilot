import { AlertTriangle, Eye, EyeOff, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { api, errorText } from "../api";
import { dayLabel, maskNumber, taka, TIER_NAMES, withAttempts } from "../format";
import { useApp } from "../state";
import type { ApprovalResult, Pending, ReminderDraft } from "../types";
import { Amount, Badge, Button, Empty, Modal, Note, PinField } from "../ui";

/** One receipt line: LABEL ········· value */
function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-3 py-1.5">
      <dt className="label shrink-0 text-faint">{label}</dt>
      <span aria-hidden className="leader" />
      <dd className="max-w-[65%] text-right text-sm font-medium">{children}</dd>
    </div>
  );
}

function Hero({ caption, paisa, to }: { caption: string; paisa: number; to?: ReactNode }) {
  return (
    <div className="pb-4 text-center">
      <div className="label text-faint">{caption}</div>
      <Amount paisa={paisa} className="display mt-2 block text-[44px]" />
      {to && <div className="mt-1.5 text-[15px] text-muted">{to}</div>}
    </div>
  );
}

interface Application {
  product_name: string;
  amount_paisa: number;
  tenure_months: number;
  monthly_instalment_paisa: number;
  total_cost_paisa: number;
  purpose: string;
  payload: {
    request: { lender_type: string; repaid_in_one_go: boolean; purpose: string };
    credit_profile: { band: string; probability_of_default: number; strengths: string[]; concerns: string[] };
    key_figures: Record<string, number>;
  };
}

function Details({ item, onRemove, removing }: { item: Pending; onRemove: (name: string) => void; removing: string | null }) {
  const [reveal, setReveal] = useState(false);
  const payment = item.action.payment;
  const application = (item.action.params?.application ?? null) as Application | null;
  if (payment) {
    return (
      <div className="rounded-2xl border border-line bg-page px-5 pt-5 pb-3">
        <Hero caption="You are about to pay" paisa={payment.amount_paisa} to={<>to <span className="font-medium text-ink">{payment.payee_name ?? "an unknown payee"}</span></>} />
        <dl className="border-t border-dashed border-line-strong pt-2">
          <Row label="Number">
            <span className="inline-flex items-center gap-2 font-mono">
              {reveal ? payment.payee_number : maskNumber(payment.payee_number)}
              <button onClick={() => setReveal((r) => !r)} className="text-muted hover:text-ink" title="Show the full number">
                {reveal ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
              </button>
            </span>
          </Row>
          <Row label="From">{payment.method === "bank" ? "Bank account" : payment.method === "nagad" ? "Nagad" : "bKash"}</Row>
          <Row label="When">{payment.execute_on ? dayLabel(payment.execute_on, true) : "Now"}</Row>
          {payment.note && <Row label="Note">{payment.note}</Row>}
        </dl>
      </div>
    );
  }
  if (application) {
    const p = application.payload;
    return (
      <div className="space-y-3">
        <div className="rounded-2xl border border-line bg-page px-5 pt-5 pb-3">
          <Hero
            caption="Loan application"
            paisa={application.amount_paisa}
            to={
              <>
                from <span className="font-medium text-ink">{application.product_name}</span>
                <span className="label ml-2 text-faint">{p.request.lender_type} · illustrative</span>
              </>
            }
          />
          <dl className="border-t border-dashed border-line-strong pt-2">
            <Row label="Repayment">
              {p.request.repaid_in_one_go
                ? `${taka(application.monthly_instalment_paisa)} in one go after ${application.tenure_months} month`
                : `${taka(application.monthly_instalment_paisa)} a month for ${application.tenure_months} months`}
            </Row>
            <Row label="Total cost">{taka(application.total_cost_paisa)}</Row>
            <Row label="Purpose">{p.request.purpose}</Row>
          </dl>
        </div>
        <Note tone="neutral" title="Shared with the lender">
          Your credit profile (band {p.credit_profile.band}, {(p.credit_profile.probability_of_default * 100).toFixed(1)}%
          chance of falling behind) and its reasons, average monthly sales, months of records, supplier punctuality, customer
          repayments and cash buffer. Your phone number is masked; no customer or supplier names are sent.
        </Note>
      </div>
    );
  }
  const reminders = (item.action.params?.reminders ?? null) as ReminderDraft[] | null;
  if (reminders) {
    const skipped = (item.action.params?.skipped ?? []) as { name: string; why: string }[];
    const removed = (item.action.params?.removed ?? []) as string[];
    const total = reminders.reduce((sum, r) => sum + r.owed_paisa, 0);
    return (
      <div className="space-y-3">
        <div className="rounded-2xl border border-line bg-page px-5 pt-5 pb-3">
          <Hero
            caption={`Baki reminders by SMS · ${reminders.length} customer${reminders.length === 1 ? "" : "s"}`}
            paisa={total}
            to={<>still owed to the shop</>}
          />
          <ul className="max-h-72 space-y-2.5 overflow-y-auto border-t border-dashed border-line-strong pt-3">
            {reminders.map((r) => (
              <li key={r.customer_name} className="text-sm">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5">
                  <span className="font-medium">{r.customer_name}</span>
                  <span className="flex items-center gap-2">
                    <span className="label text-faint">
                      <span className="font-mono">{maskNumber(r.to_number)}</span> · {r.days_outstanding} days · <Amount paisa={r.owed_paisa} />
                    </span>
                    <button
                      type="button"
                      onClick={() => onRemove(r.customer_name)}
                      disabled={removing !== null}
                      aria-label={`Take ${r.customer_name} out of these reminders`}
                      title={`Don't send this one: take ${r.customer_name} out`}
                      className="inline-flex size-6 items-center justify-center rounded-full text-faint transition hover:bg-bad-soft hover:text-bad disabled:opacity-40"
                    >
                      <X className={`size-3.5 ${removing === r.customer_name ? "animate-pulse" : ""}`} />
                    </button>
                  </span>
                </div>
                <p className="mt-1 rounded-lg bg-raised/60 px-3 py-2 text-[13px] text-muted">{r.body}</p>
              </li>
            ))}
          </ul>
        </div>
        <Note tone="neutral" title="Written from your books">
          The names, numbers, amounts and words come from your ledger and a fixed template, not from the AI.
          {skipped.length > 0 && <> Not included: {skipped.map((s) => `${s.name} (${s.why})`).join("; ")}.</>}
          {removed.length > 0 && <> You took out: {removed.join(", ")}.</>}
          {reminders.length > 1 && <> Tap × beside a name to leave that customer out.</>}
        </Note>
      </div>
    );
  }
  if (item.kind === "control.change" || item.kind === "control.block") return <SettingsChange item={item} />;
  return <p className="rounded-2xl border border-line bg-page p-5 text-sm">{item.summary}</p>;
}

const METHOD_NAMES: Record<string, string> = { bkash: "bKash", nagad: "Nagad", bank: "Bank account" };

/** "Delete all my data", asked for in chat: exactly what goes and what stays, before the PIN. */
function EraseData({ item }: { item: Pending }) {
  const p = (item.action.params ?? {}) as { items?: { what: string; count: number }[]; scheduled_payments?: number; kept?: string };
  const items = p.items ?? [];
  return (
    <div className="space-y-3">
      <div className="rounded-2xl border border-bad/35 bg-bad-soft/50 p-4">
        <div className="label text-[10.5px] text-bad">This will be deleted</div>
        {items.length ? (
          <ul className="mt-2 grid gap-1 text-sm sm:grid-cols-2">
            {items.map((i) => (
              <li key={i.what} className="flex justify-between gap-3">
                <span>{i.what}</span>
                <span className="tabular font-medium">{i.count.toLocaleString("en-IN")}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm">Nothing: the books are already empty.</p>
        )}
        {!!p.scheduled_payments && (
          <p className="mt-2 text-sm font-medium text-bad">
            {p.scheduled_payments} scheduled payment{p.scheduled_payments !== 1 ? "s" : ""} will be cancelled.
          </p>
        )}
      </div>
      {p.kept && <Note tone="neutral" title="What stays">{p.kept}</Note>}
      <Note tone="warn" title="Asked for in chat">
        Nothing is deleted until you confirm with your PIN here. To keep a copy first, reject this and say "export my
        data".
      </Note>
    </div>
  );
}

/** A change to the owner's own safety settings, asked for in chat. */
function SettingsChange({ item }: { item: Pending }) {
  const p = (item.action.params ?? {}) as Record<string, string | number | null>;
  if (p.control === "erase_data") return <EraseData item={item} />;
  const money = (v: unknown) => (typeof v === "number" ? taka(v) : "unchanged");
  const caption =
    p.control === "limits" ? "Change your payment limits"
    : p.control === "add_payee" ? "Add a trusted payee"
    : p.control === "remove_payee" ? "Remove a trusted payee"
    : p.control === "savings_account" ? "Where your savings go"
    : "Block a number";
  return (
    <div className="space-y-3">
      <div className="rounded-2xl border border-line bg-page px-5 pt-5 pb-3">
        <div className="label pb-3 text-center text-faint">{caption}</div>
        <dl className="border-t border-dashed border-line-strong pt-2">
          {p.control === "limits" && (
            <>
              <Row label="Per payment">
                {p.per_txn_paisa != null ? <>{money(p.per_txn_before_paisa)} → <b>{money(p.per_txn_paisa)}</b></> : "unchanged"}
              </Row>
              <Row label="Per day">
                {p.daily_paisa != null ? <>{money(p.daily_before_paisa)} → <b>{money(p.daily_paisa)}</b></> : "unchanged"}
              </Row>
            </>
          )}
          {(p.control === "add_payee" || p.control === "remove_payee") && (
            <>
              <Row label="Name">{p.name}</Row>
              <Row label="Number"><span className="font-mono">{maskNumber(String(p.number ?? ""))}</span></Row>
              {p.method && <Row label="Paid by">{METHOD_NAMES[String(p.method)] ?? p.method}</Row>}
            </>
          )}
          {p.control === "savings_account" && (
            <>
              <Row label="Name">{p.name}</Row>
              {/* the full number, so the owner can check it is their own account before entering the PIN */}
              <Row label="Number"><span className="font-mono">{String(p.number ?? "")}</span></Row>
              {p.method && <Row label="Method">{METHOD_NAMES[String(p.method)] ?? p.method}</Row>}
            </>
          )}
          {p.control === "block" && (
            <>
              <Row label="Number"><span className="font-mono">{maskNumber(String(p.number ?? ""))}</span></Row>
              <Row label="Reason">{p.reason}</Row>
            </>
          )}
        </dl>
      </div>
      <Note tone="neutral" title="Asked for in chat">
        You asked for this in chat. Nothing changes until you {item.requires_pin ? "confirm with your PIN" : "tap Approve"}{" "}
        here; the PIN itself can only be changed in the Controls tab.
      </Note>
    </div>
  );
}

export function ApprovalDialog({ open, focusId, pending, onClose, onDone }: {
  open: boolean;
  focusId?: string;
  pending: Pending[];
  onClose: () => void;
  onDone: () => void;
}) {
  const { toast } = useApp();
  const [selected, setSelected] = useState<string | undefined>(focusId);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "ok" | "bad" | "warn"; text: string } | null>(null);

  useEffect(() => {
    if (open) {
      setSelected(focusId ?? pending[0]?.id);
      setResult(null);
      setPin("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, focusId]);

  const item = pending.find((p) => p.id === selected) ?? pending[0];
  const [removing, setRemoving] = useState<string | null>(null);

  /** Leave one customer out of held reminders; the rest stay exactly as shown. */
  async function removeRecipient(name: string) {
    if (!item) return;
    setRemoving(name);
    try {
      const response = await api.post<ApprovalResult>(`/approvals/${item.id}/remove`, { customer_name: name });
      toast(response.message, response.ok ? "info" : "bad");
      onDone();
      if (response.status === "rejected" && pending.length <= 1) onClose();
    } catch (e) {
      setResult({ tone: "bad", text: errorText(e) });
    } finally {
      setRemoving(null);
    }
  }

  async function decide(approve: boolean) {
    if (!item) return;
    setBusy(true);
    try {
      const response = approve
        ? await api.post<ApprovalResult>(`/approvals/${item.id}/approve`, { pin: item.requires_pin ? pin : null })
        : await api.post<ApprovalResult>(`/approvals/${item.id}/reject`);
      if (response.ok) {
        toast(response.message, approve ? "ok" : "info");
        onDone();
        setResult(null);
        setPin("");
        const rest = pending.filter((p) => p.id !== item.id);
        if (rest.length) setSelected(rest[0].id);
        else onClose();
      } else {
        setResult({
          tone: response.status === "wrong_pin" ? "warn" : "bad",
          text: withAttempts(response.message, response.attempts_left),
        });
        if (response.status !== "wrong_pin") onDone();
      }
    } catch (e) {
      setResult({ tone: "bad", text: errorText(e) });
    } finally {
      setPin("");
      setBusy(false);
    }
  }

  const title = !item
    ? "All clear"
    : item.action.payment
      ? "Approve this payment?"
      : item.kind === "loan.submit"
        ? "Send this application?"
        : item.kind === "message.send"
          ? "Send these reminders?"
          : item.action.params?.control === "erase_data"
            ? "Delete all your data?"
            : item.kind === "control.change" || item.kind === "control.block"
              ? "Change this setting?"
            : "Approve this action?";

  return (
    <Modal open={open} onClose={onClose} eyebrow="Waiting for your approval" title={title} wide>
      {!item ? (
        <Empty>Nothing is waiting for you.</Empty>
      ) : (
        <div className="space-y-4">
          {pending.length > 1 && (
            <div className="no-scrollbar -mx-1 flex gap-1.5 overflow-x-auto px-1">
              {pending.map((p) => (
                <button
                  key={p.id}
                  onClick={() => setSelected(p.id)}
                  className={`max-w-56 shrink-0 truncate rounded-full border px-3 py-1 text-xs transition ${
                    p.id === item.id ? "border-ink bg-brand text-brand-ink" : "border-line text-muted hover:border-ink hover:text-ink"
                  }`}
                >
                  {p.summary}
                </button>
              ))}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={item.tier >= 3 ? "warn" : "info"}>
              Tier {item.tier} · {TIER_NAMES[item.tier]}
            </Badge>
            {item.requires_pin && item.tier !== 3 && <Badge tone="warn">PIN required</Badge>}
            <span className="label ml-auto text-[10.5px] text-faint">
              Expires {new Date(item.expires_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}
            </span>
          </div>
          <Details item={item} onRemove={removeRecipient} removing={removing} />
          {item.warnings.length > 0 && (
            <div className="space-y-2">
              {item.warnings.map((w) => (
                <div key={w} className="flex items-start gap-2.5 rounded-xl border border-warn/30 bg-warn-soft px-3.5 py-2.5 text-sm">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" />
                  {w}
                </div>
              ))}
            </div>
          )}
          {item.requires_pin && <PinField value={pin} onChange={setPin} />}
          {result && <Note tone={result.tone}>{result.text}</Note>}
          <div className="flex items-center justify-between gap-2 pt-1">
            <span className="hidden text-xs text-faint sm:block">Nothing moves until you approve.</span>
            <div className="ml-auto flex gap-2">
              <Button variant="ghost" busy={busy} onClick={() => decide(false)}>
                Reject
              </Button>
              <Button variant="primary" busy={busy} disabled={item.requires_pin && pin.length < 4} onClick={() => decide(true)}>
                {item.requires_pin ? "Confirm with PIN" : item.kind === "loan.submit" || item.kind === "message.send" ? "Approve and send" : "Approve"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
