import { ChevronDown, KeyRound, Lock, PiggyBank, TrendingUp } from "lucide-react";
import { useState } from "react";

import { api, errorText } from "../api";
import { GoalsCard } from "../components/GoalsCard";
import { GrowthSection } from "../components/GrowthSection";
import { SavingsProducts } from "../components/SavingsProducts";
import { dayLabel, taka, withAttempts } from "../format";
import { useData } from "../hooks";
import { useApp } from "../state";
import type { ControlResult, SavingsMonth, SavingsStatus, ToolOutcome } from "../types";
import { Amount, Badge, Button, Card, Field, inputClass, Loading, Marker, Modal, Note, Page, PageHeader, PinField, Segmented, Stat, type Tone } from "../ui";

const SHARES = [5, 10, 15, 20, 25, 30];
const METHOD_NAMES: Record<string, string> = { bkash: "bKash", nagad: "Nagad", bank: "Bank" };

const STATE_LOOK: Record<SavingsStatus["state"], { tone: Tone; label: string; banner: string }> = {
  no_plan: { tone: "info", label: "No plan yet", banner: "border-line bg-surface" },
  no_account: { tone: "info", label: "Add your savings account", banner: "border-line bg-surface" },
  no_profit: { tone: "neutral", label: "Nothing to save this month", banner: "border-line bg-surface" },
  ready: { tone: "ok", label: "Ready to move", banner: "border-ok/30 bg-ok-soft" },
  partial: { tone: "warn", label: "Lean month", banner: "border-warn/35 bg-warn-soft" },
  wait: { tone: "warn", label: "Hold for now", banner: "border-warn/35 bg-warn-soft" },
  done: { tone: "ok", label: "Done this month", banner: "border-ok/30 bg-ok-soft" },
};

function monthName(iso: string): string {
  return new Date(`${iso}T12:00:00+06:00`).toLocaleDateString("en-GB", { month: "long", timeZone: "Asia/Dhaka" });
}

/** One month: profit as a quiet bar, with what was saved against that month's target. */
function MonthRow({ m, scale }: { m: SavingsMonth; scale: number }) {
  const profitWidth = scale > 0 ? Math.max(0, (m.profit_paisa / scale) * 100) : 0;
  const met = m.target_paisa > 0 && m.saved_paisa >= m.target_paisa;
  return (
    <li className="grid grid-cols-[4.5rem_1fr] items-center gap-x-3 gap-y-1 py-2.5 text-sm sm:grid-cols-[5rem_1fr_9rem]">
      <span className="text-muted">
        {m.label.split(" ")[0]}
        {m.so_far && <span className="ml-1 text-[11px] text-faint">so far</span>}
      </span>
      <div className="relative h-5 overflow-hidden rounded-md bg-page">
        <div
          className={`absolute inset-y-0 left-0 rounded-md ${m.profit_paisa < 0 ? "bg-bad/25" : "bg-ink/12"}`}
          style={{ width: `${Math.min(100, m.profit_paisa < 0 ? 4 : profitWidth)}%` }}
        />
        <span className="absolute inset-y-0 left-2 flex items-center text-[11px] text-muted tabular">
          profit {taka(m.profit_paisa, true)}
        </span>
      </div>
      <span className="col-start-2 text-xs text-muted sm:col-start-auto sm:text-right">
        {m.target_paisa > 0 ? (
          <>
            saved <span className={met ? "font-medium text-ok" : "text-ink"}>{taka(m.saved_paisa, true)}</span> of{" "}
            {taka(m.target_paisa, true)}
          </>
        ) : (
          <span className="text-faint">no target</span>
        )}
      </span>
    </li>
  );
}

export function SavingsTab() {
  const { version, toast, refresh, review } = useApp();
  const { data, error } = useData<SavingsStatus>("/savings", [version]);
  const [moving, setMoving] = useState(false);
  const [savingShare, setSavingShare] = useState(false);
  const [account, setAccount] = useState({ name: "My savings", number: "", method: "bkash" });
  const [askPin, setAskPin] = useState(false);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function setShare(share: number | null) {
    setSavingShare(true);
    try {
      await api.put<SavingsStatus>("/savings/share", { share_pct: share });
      toast(share ? `You'll save ${share}% of each month's profit.` : "Savings paused.", "ok");
      refresh();
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setSavingShare(false);
    }
  }

  async function setBasis(basis: SavingsStatus["basis"]) {
    setSavingShare(true);
    try {
      await api.put<SavingsStatus>("/savings/basis", { basis });
      toast(basis === "three_months" ? "Savings now follow the middle of your last three months." : "Savings now follow last month's profit.", "ok");
      refresh();
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setSavingShare(false);
    }
  }

  async function move() {
    setMoving(true);
    try {
      const out = await api.post<ToolOutcome>("/savings/prepare", {});
      if (out.status === "held" && out.pending_id) review(out.pending_id);
      else toast(out.message, out.status === "blocked" || out.status === "error" ? "bad" : "info");
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setMoving(false);
      refresh();
    }
  }

  async function saveAccount() {
    setBusy(true);
    setProblem(null);
    try {
      const result = await api.put<ControlResult>("/owner/savings-account", { pin, ...account });
      if (result.ok) {
        toast(result.message, "ok");
        setAskPin(false);
        setAccount({ ...account, number: "" });
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

  if (error) return <Page><Note tone="bad">{error}</Note></Page>;
  if (!data) return <Page><Loading label="Working out your savings" /></Page>;
  const look = STATE_LOOK[data.state];
  const canMove = (data.state === "ready" || data.state === "partial") && data.suggested_paisa > 0;
  const scale = Math.max(1, ...data.history.map((m) => m.profit_paisa));
  const moved = data.saved_paisa + data.pending_paisa;

  return (
    <Page>
      <PageHeader
        eyebrow="Personal savings"
        title="Pay yourself, grow the shop"
        description="A share of last month's profit goes to your own savings. A good month saves more, a lean month less, and never so much that cash drops below your safety buffer. Every transfer waits for your PIN."
        actions={
          canMove && (
            <Button variant="primary" busy={moving} onClick={move}>
              <PiggyBank className="size-4" /> Move {taka(data.suggested_paisa, true)} to savings
            </Button>
          )
        }
      />

      <div className={`rounded-2xl border px-5 py-4 ${look.banner}`}>
        <div className={`label flex items-center gap-2 ${look.tone === "ok" ? "text-ok" : look.tone === "warn" ? "text-warn" : "text-muted"}`}>
          <Marker tone={look.tone} />
          {look.label}
        </div>
        <p className="mt-1.5 text-[16px] leading-snug font-medium">{data.headline}</p>
        {data.jump && share_ok(data) && <p className="mt-2 text-sm text-muted">{data.jump.text}</p>}
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label={`${monthName(data.month)}'s savings`}
          value={<Amount paisa={data.target_paisa} />}
          hint={
            data.share_pct
              ? `${data.share_pct}% of ${data.basis_text}`
              : "pick a share below"
          }
        />
        <Stat
          label="Moved"
          value={<Amount paisa={moved} />}
          hint={data.pending_paisa ? `${taka(data.pending_paisa, true)} on its way` : data.remaining_paisa ? `${taka(data.remaining_paisa, true)} to go` : "nothing left this month"}
          tone={data.state === "done" ? "ok" : undefined}
        />
        <Stat
          label="Safe to move today"
          value={<Amount paisa={data.safe_now_paisa} />}
          hint={`keeps cash above your ${taka(data.buffer_paisa, true)} buffer`}
          tone={data.safe_now_paisa === 0 ? "warn" : undefined}
        />
        <Stat
          label="Lowest cash ahead"
          value={<Amount paisa={data.lowest_balance_paisa} />}
          hint={dayLabel(data.lowest_day)}
          tone={data.lowest_balance_paisa < data.buffer_paisa ? "bad" : undefined}
        />
      </div>

      {data.scale_up && (
        <Note tone="ok" title={<span className="flex items-center gap-2"><TrendingUp className="size-4" /> Time to save more?</span>}>
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex-1">{data.scale_up.text}</span>
            <Button size="sm" variant="secondary" busy={savingShare} onClick={() => setShare(data.scale_up!.to_pct)}>
              Save {data.scale_up.to_pct}%
            </Button>
          </div>
        </Note>
      )}

      <div className="grid gap-6 xl:grid-cols-2">
        <Card eyebrow="Your share" title="How much of each month's profit to save">
          <div className="space-y-4">
            <Segmented
              value={data.share_pct ?? 0}
              onChange={(v) => setShare(v === 0 ? null : v)}
              options={[...SHARES.map((s) => ({ value: s, label: `${s}%` })), { value: 0, label: "Off" }]}
            />
            <div className="flex flex-wrap items-center gap-3">
              <span className="label text-[10px] text-faint">A share of</span>
              <Segmented
                value={data.basis}
                onChange={setBasis}
                options={[
                  { value: "last_month", label: "Last month" },
                  { value: "three_months", label: "Last 3 months (steadier)" },
                ]}
              />
            </div>
            <p className="text-sm leading-relaxed text-muted">
              You can also say it in chat: “save 10% of my profit every month”. The share moves no money by itself;
              KhataPilot works out each month's amount from your books and asks for your PIN before anything leaves.
            </p>
          </div>
        </Card>

        <Card
          eyebrow="Savings account"
          title={data.account ? data.account.name : "Where your savings go"}
          actions={data.account && <Badge tone="neutral">{METHOD_NAMES[data.account.method] ?? data.account.method}</Badge>}
        >
          {data.account && (
            <p className="mb-4 text-sm text-muted">
              Savings go to <span className="font-mono text-ink">{data.account.number_masked}</span>, saved{" "}
              {dayLabel(data.account.set_at)} with your PIN. Chat can never change it.
            </p>
          )}
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setPin("");
              setProblem(null);
              setAskPin(true);
            }}
          >
            <div className="w-40">
              <Field label="Name">
                <input className={inputClass} value={account.name} onChange={(e) => setAccount({ ...account, name: e.target.value })} />
              </Field>
            </div>
            <div className="w-40">
              <Field label="Number or account">
                <input className={`${inputClass} font-mono`} value={account.number} placeholder="01XXXXXXXXX" onChange={(e) => setAccount({ ...account, number: e.target.value })} />
              </Field>
            </div>
            <div className="w-28">
              <Field label="Method">
                <select className={inputClass} value={account.method} onChange={(e) => setAccount({ ...account, method: e.target.value })}>
                  <option value="bkash">bKash</option>
                  <option value="nagad">Nagad</option>
                  <option value="bank">Bank</option>
                </select>
              </Field>
            </div>
            <Button type="submit" disabled={account.name.trim().length < 2 || account.number.replace(/\D/g, "").length < 6}>
              <KeyRound className="size-4" /> {data.account ? "Change" : "Save"} with PIN
            </Button>
          </form>
        </Card>
      </div>

      <GoalsCard data={data} />

      <SavingsProducts />

      <Card eyebrow="Month by month" title="Profit, and what you saved from it">
        <ul className="divide-y divide-line">
          {data.history.map((m) => (
            <MonthRow key={m.month} m={m} scale={scale} />
          ))}
        </ul>
        <p className="mt-3 text-xs text-faint">
          Each month's target is your share of the month before's profit{data.share_pct ? ` (${data.share_pct}% now)` : ""}.
        </p>
      </Card>

      <details className="group rounded-2xl border border-line bg-surface">
        <summary className="flex cursor-pointer list-none items-center gap-3 px-5 py-4 [&::-webkit-details-marker]:hidden">
          <span className="label flex items-center gap-2 text-muted">
            <Marker />
            Method
          </span>
          <span className="text-[15px] font-medium">How the amount is worked out</span>
          <ChevronDown className="ml-auto size-4 text-faint transition group-open:rotate-180" />
        </summary>
        <ul className="list-disc space-y-1.5 px-5 pb-5 pl-10 text-sm text-muted">
          {data.method.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      </details>

      <GrowthSection />

      <Modal open={askPin} onClose={() => setAskPin(false)} eyebrow="Needs your PIN" title="Set the savings account">
        <div className="space-y-5">
          <div className="flex items-start gap-3 rounded-xl border border-line bg-page/60 p-3.5 text-sm">
            <Lock className="mt-0.5 size-4 shrink-0 text-muted" />
            <p>
              Savings will go to {account.name}, {account.number} ({METHOD_NAMES[account.method]}). Check the number
              yourself: a message asking you to change where money goes is a common scam.
            </p>
          </div>
          <PinField value={pin} onChange={setPin} />
          {problem && <Note tone="bad">{problem}</Note>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setAskPin(false)}>
              Cancel
            </Button>
            <Button variant="primary" busy={busy} disabled={pin.length < 4} onClick={saveAccount}>
              Confirm
            </Button>
          </div>
        </div>
      </Modal>
    </Page>
  );
}

/** A jump note only matters once there is a plan to follow it. */
function share_ok(data: { share_pct: number | null }): boolean {
  return data.share_pct !== null;
}
