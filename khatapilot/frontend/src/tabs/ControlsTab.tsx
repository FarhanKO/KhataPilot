import { Ban, KeyRound, Lock, Plus, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import { api, errorText } from "../api";
import { withAttempts } from "../format";
import { useApp } from "../state";
import type { ControlResult } from "../types";
import { Badge, Button, Card, Field, inputClass, Loading, Modal, Note, Page, PageHeader, PinField } from "../ui";

interface PinAction {
  title: string;
  description: ReactNode;
  run: (pin: string) => Promise<ControlResult>;
}

const METHOD_NAMES: Record<string, string> = { bkash: "bKash", nagad: "Nagad", bank: "Bank" };

/** Owner-only controls. None of these exist as chat tools: they need the owner here, with the PIN. */
export function ControlsTab() {
  const { status, refresh, toast } = useApp();
  const [action, setAction] = useState<PinAction | null>(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [limits, setLimits] = useState({ per: "", day: "" });
  const [payee, setPayee] = useState({ name: "", number: "", method: "bkash" });
  const [block, setBlock] = useState("");

  async function confirm() {
    if (!action) return;
    setBusy(true);
    setProblem(null);
    try {
      const result = await action.run(pin);
      if (result.ok) {
        toast(result.message, "ok");
        setAction(null);
        refresh();
      } else {
        setProblem(withAttempts(result.message, result.attempts_left));
      }
    } catch (e) {
      setProblem(errorText(e));
    } finally {
      setPin("");
      setBusy(false);
    }
  }

  function ask(next: PinAction) {
    setPin("");
    setProblem(null);
    setAction(next);
  }

  if (!status) return <Page><Loading /></Page>;
  return (
    <Page>
      <PageHeader
        eyebrow="Limits & payees"
        title="Rules only you can change"
        description="These controls live here, behind your PIN. The chat assistant cannot change limits, trusted payees or the PIN, whatever anyone types."
      />
      {status.pin.locked && (
        <Note tone="bad" title="PIN locked">
          Too many wrong PINs: money actions are locked until{" "}
          {status.pin.locked_until ? new Date(status.pin.locked_until).toLocaleTimeString() : "later"}.
        </Note>
      )}

      <div className="grid gap-6 xl:grid-cols-2">
        <Card eyebrow="Limits" title="How much can move without your PIN">
          <div className="mb-5 grid grid-cols-2 gap-3">
            {[
              ["Per payment", status.limits.per_payment],
              ["Per day, all payments", status.limits.per_day],
            ].map(([label, v]) => (
              <div key={label} className="rounded-xl border border-line bg-page/60 px-4 py-3">
                <div className="label text-[10px] text-faint">{label}</div>
                <div className="display mt-2 text-[24px] tabular">{v}</div>
              </div>
            ))}
          </div>
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              const per = Number(limits.per) || undefined;
              const day = Number(limits.day) || undefined;
              ask({
                title: "Change limits",
                description: `New limits: ${per ? `৳${per.toLocaleString()} per payment` : "per payment unchanged"}, ${day ? `৳${day.toLocaleString()} per day` : "per day unchanged"}.`,
                run: (p) => api.put<ControlResult>("/owner/limits", { pin: p, per_txn_taka: per, daily_taka: day }),
              });
            }}
          >
            <div className="w-36">
              <Field label="Per payment (৳)">
                <input className={inputClass} inputMode="numeric" value={limits.per} onChange={(e) => setLimits({ ...limits, per: e.target.value.replace(/\D/g, "") })} />
              </Field>
            </div>
            <div className="w-36">
              <Field label="Per day (৳)">
                <input className={inputClass} inputMode="numeric" value={limits.day} onChange={(e) => setLimits({ ...limits, day: e.target.value.replace(/\D/g, "") })} />
              </Field>
            </div>
            <Button type="submit" disabled={!limits.per && !limits.day}>
              <KeyRound className="size-4" /> Change with PIN
            </Button>
          </form>
        </Card>

        <Card eyebrow="Kill switch" title={status.kill_switch.on ? "STOP is on" : "Running normally"} tone={status.kill_switch.on ? "bad" : undefined}>
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              {status.kill_switch.on ? <Badge tone="bad">frozen</Badge> : <Badge tone="ok">running</Badge>}
              {status.kill_switch.reason && <span className="text-muted">{status.kill_switch.reason}</span>}
            </div>
            <p className="leading-relaxed text-muted">
              Press STOP (top right) or type “stop” in the chat to freeze every payment, message and loan application at
              once, here and at the provider. Scheduled payments are paused, not deleted. Resuming needs your PIN.
            </p>
          </div>
        </Card>
      </div>

      <Card eyebrow="Trusted payees" title={`${status.trusted_payees.length} people you pay often`}>
        <ul className="divide-y divide-line">
          {status.trusted_payees.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center gap-3 py-2.5 text-sm">
              <span className="min-w-0 flex-1">
                <span className="font-medium">{p.name}</span>
                <span className="ml-2 font-mono text-xs text-muted">{p.number}</span>
              </span>
              <Badge tone="neutral">{METHOD_NAMES[p.method] ?? p.method}</Badge>
              <span className="label hidden text-[10px] text-faint sm:inline">via {p.added_via}</span>
              <Button
                variant="dangerGhost"
                size="sm"
                onClick={() =>
                  ask({
                    title: `Remove ${p.name}`,
                    description: `Payments to ${p.name} (${p.number}) will need your PIN again.`,
                    run: (pinValue) => api.post<ControlResult>(`/owner/payees/${p.id}/remove`, { pin: pinValue }),
                  })
                }
              >
                <Trash2 className="size-3.5" /> Remove
              </Button>
            </li>
          ))}
        </ul>
        <form
          className="mt-4 flex flex-wrap items-end gap-2 border-t border-line pt-4"
          onSubmit={(e) => {
            e.preventDefault();
            ask({
              title: `Trust ${payee.name}`,
              description: `${payee.name}, ${payee.number} (${payee.method}). Payments to a trusted payee within your limits can go without the PIN.`,
              run: (p) => api.post<ControlResult>("/owner/payees", { pin: p, ...payee }),
            });
          }}
        >
          <div className="w-48">
            <Field label="Name">
              <input className={inputClass} value={payee.name} onChange={(e) => setPayee({ ...payee, name: e.target.value })} />
            </Field>
          </div>
          <div className="w-44">
            <Field label="Number or account">
              <input className={inputClass} value={payee.number} onChange={(e) => setPayee({ ...payee, number: e.target.value })} />
            </Field>
          </div>
          <div className="w-28">
            <Field label="Method">
              <select className={inputClass} value={payee.method} onChange={(e) => setPayee({ ...payee, method: e.target.value })}>
                <option value="bkash">bKash</option>
                <option value="nagad">Nagad</option>
                <option value="bank">Bank</option>
              </select>
            </Field>
          </div>
          <Button type="submit" disabled={payee.name.length < 2 || payee.number.length < 6}>
            <Plus className="size-4" /> Add with PIN
          </Button>
        </form>
      </Card>

      <Card eyebrow="Block list" title="Block a number">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              const result = await api.post<ControlResult>("/owner/block", { number: block });
              toast(result.message, result.ok ? "ok" : "bad");
              setBlock("");
              refresh();
            } catch (err) {
              toast(errorText(err), "bad");
            }
          }}
        >
          <div className="w-56">
            <Field label="Number that tried to scam you">
              <input className={`${inputClass} font-mono`} value={block} onChange={(e) => setBlock(e.target.value)} placeholder="01XXXXXXXXX" />
            </Field>
          </div>
          <Button type="submit" variant="danger" disabled={block.replace(/\D/g, "").length < 10}>
            <Ban className="size-4" /> Block
          </Button>
        </form>
      </Card>

      <Modal open={action !== null} onClose={() => setAction(null)} eyebrow="Needs your PIN" title={action?.title ?? ""}>
        {action && (
          <div className="space-y-5">
            <div className="flex items-start gap-3 rounded-xl border border-line bg-page/60 p-3.5 text-sm">
              <Lock className="mt-0.5 size-4 shrink-0 text-muted" />
              <p>{action.description}</p>
            </div>
            <PinField value={pin} onChange={setPin} />
            {problem && <Note tone="bad">{problem}</Note>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setAction(null)}>
                Cancel
              </Button>
              <Button variant="primary" busy={busy} disabled={pin.length < 4} onClick={confirm}>
                Confirm
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </Page>
  );
}
