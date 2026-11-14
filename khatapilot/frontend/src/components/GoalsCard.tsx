import { Plus, Sparkles, Target, X } from "lucide-react";
import { useState } from "react";

import { api, errorText } from "../api";
import { taka } from "../format";
import { useApp } from "../state";
import type { SavingsGoal, SavingsStatus, ToolOutcome } from "../types";
import { Badge, Button, Card, Field, inputClass } from "../ui";

function monthLabel(iso: string): string {
  return new Date(`${iso}T12:00:00+06:00`).toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "Asia/Dhaka" });
}

function GoalRow({ goal, onRemove, busy }: { goal: SavingsGoal; onRemove: () => void; busy: boolean }) {
  const late = goal.on_track === false;
  return (
    <li className="py-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14.5px] font-medium">{goal.name}</span>
            {goal.set_by === "khatapilot" && (
              <Badge tone="info">
                <Sparkles className="size-3" /> suggested
              </Badge>
            )}
            {goal.reached && <Badge tone="ok">reached</Badge>}
            {goal.by_month && !goal.reached && (
              <span className={`label text-[10.5px] ${late ? "text-warn" : "text-faint"}`}>by {monthLabel(goal.by_month)}</span>
            )}
          </div>
          <div
            className="mt-2 h-2 overflow-hidden rounded-full bg-page"
            role="progressbar"
            aria-label={`${goal.name}: ${goal.pct}% saved`}
            aria-valuenow={goal.pct}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div className={`h-full rounded-full ${goal.reached ? "bg-ok" : late ? "bg-warn" : "bg-ink"}`} style={{ width: `${Math.max(goal.pct, 2)}%` }} />
          </div>
          <div className="mt-1.5 flex flex-wrap items-baseline justify-between gap-x-3 text-xs">
            <span className="text-muted tabular">
              {taka(goal.saved_paisa, true)} of {taka(goal.target_paisa, true)}
            </span>
            <span className={late ? "text-warn" : "text-faint"}>
              {goal.reached
                ? "done"
                : goal.months_left === null
                  ? "no date yet: nothing is being saved"
                  : `about ${goal.months_left} month${goal.months_left === 1 ? "" : "s"} at this pace${late ? ", later than wanted" : ""}`}
            </span>
          </div>
          {goal.why && <p className="mt-1.5 text-xs leading-relaxed text-faint">{goal.why}</p>}
        </div>
        <button
          onClick={onRemove}
          disabled={busy}
          aria-label={`Remove the goal ${goal.name}`}
          title="Remove this goal (the money saved stays saved)"
          className="-mt-1 -mr-1.5 grid size-7 shrink-0 place-items-center rounded-full text-faint transition hover:bg-raised hover:text-ink disabled:opacity-40"
        >
          <X className="size-4" />
        </button>
      </div>
    </li>
  );
}

/** What the savings are for: goals filled from the top, set in chat or here, with starter goals from the books. */
export function GoalsCard({ data }: { data: SavingsStatus }) {
  const { toast, refresh } = useApp();
  const [form, setForm] = useState({ name: "", amount: "", by: "" });
  const [busy, setBusy] = useState<string | null>(null);

  async function run(key: string, call: () => Promise<ToolOutcome>, ok: (out: ToolOutcome) => string) {
    setBusy(key);
    try {
      const out = await call();
      if (out.status === "ok") toast(ok(out), "ok");
      else toast(out.message, "bad");
      refresh();
      return out.status === "ok";
    } catch (e) {
      toast(errorText(e), "bad");
      return false;
    } finally {
      setBusy(null);
    }
  }

  const amount = Number(form.amount.replace(/[^\d]/g, ""));
  const canAdd = form.name.trim().length >= 2 && amount >= 100;

  return (
    <Card
      eyebrow="Goals"
      title={
        <span className="flex items-center gap-2">
          <Target className="size-4 text-muted" /> What you are saving for
        </span>
      }
      actions={
        <Button
          size="sm"
          variant="secondary"
          busy={busy === "starters"}
          onClick={() =>
            run(
              "starters",
              () => api.post<ToolOutcome>("/savings/goals/starters", {}),
              (out) => {
                const added = (out.data as { added?: unknown[] } | null)?.added?.length ?? 0;
                return added ? `Added ${added} goal${added === 1 ? "" : "s"} worked out from your books.` : "You already have the suggested goals.";
              },
            )
          }
        >
          <Sparkles className="size-3.5" /> Suggest from my books
        </Button>
      }
    >
      <p className="text-sm leading-relaxed text-muted">{data.goals_headline}</p>
      {data.goals.length > 0 && (
        <ul className="mt-2 divide-y divide-line">
          {data.goals.map((g) => (
            <GoalRow
              key={g.id}
              goal={g}
              busy={busy === g.id}
              onRemove={() => run(g.id, () => api.del<ToolOutcome>(`/savings/goals/${g.id}`), () => `Removed “${g.name}”. What you saved stays saved.`)}
            />
          ))}
        </ul>
      )}
      <form
        className="mt-4 flex flex-wrap items-end gap-2 border-t border-dashed border-line pt-4"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!canAdd) return;
          const done = await run(
            "add",
            () => api.post<ToolOutcome>("/savings/goals", { name: form.name.trim(), amount_taka: amount, by_month: form.by || null }),
            () => `Goal “${form.name.trim()}” saved.`,
          );
          if (done) setForm({ name: "", amount: "", by: "" });
        }}
      >
        <div className="w-40">
          <Field label="Goal">
            <input className={inputClass} value={form.name} placeholder="Fridge" maxLength={48} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
        </div>
        <div className="w-32">
          <Field label="Amount (৳)">
            <input className={`${inputClass} tabular`} inputMode="numeric" value={form.amount} placeholder="35000" onChange={(e) => setForm({ ...form, amount: e.target.value })} />
          </Field>
        </div>
        <div className="w-40">
          <Field label="By (optional)">
            <input type="month" className={inputClass} value={form.by} onChange={(e) => setForm({ ...form, by: e.target.value })} />
          </Field>
        </div>
        <Button type="submit" busy={busy === "add"} disabled={!canAdd}>
          <Plus className="size-4" /> Add goal
        </Button>
      </form>
      <p className="mt-3 text-xs text-faint">
        Savings fill your goals from the top. In chat: “goal 35,000 for a fridge by March”, “remove the Eid goal”, or
        “suggest some goals”. Goals move no money.
      </p>
    </Card>
  );
}
