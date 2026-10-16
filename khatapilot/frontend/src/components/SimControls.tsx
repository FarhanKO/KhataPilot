import { CalendarClock, Gauge, Loader2, PlayCircle } from "lucide-react";
import { useState, type ReactNode } from "react";

import { api, errorText } from "../api";
import { useApp } from "../state";

/** Demo controls for the simulated clock; they sit next to the clock they move. */
export function SimControls() {
  const { toast, refresh } = useApp();
  const [busy, setBusy] = useState<string | null>(null);

  async function run(id: string, action: () => Promise<unknown>) {
    setBusy(id);
    try {
      await action();
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setBusy(null);
      refresh();
    }
  }

  const controls: { id: string; label: string; icon: ReactNode; go: () => Promise<unknown> }[] = [
    {
      id: "day",
      label: "Next day",
      icon: <CalendarClock className="size-3.5" />,
      go: async () => {
        await api.post("/sim/advance", { hours: 24 });
        toast("Moved the simulated clock forward one day.", "info");
      },
    },
    {
      id: "due",
      label: "Run due payments",
      icon: <PlayCircle className="size-3.5" />,
      go: async () => {
        const result = await api.post<{ ran: number; results: { message: string }[] }>("/sim/run-due");
        toast(result.ran ? result.results.map((r) => r.message).join(" ") : "No payments were due.", "info");
      },
    },
    {
      id: "cash",
      label: "Daily checks",
      icon: <Gauge className="size-3.5" />,
      go: async () => {
        // What the background loop does once a simulated day: look ahead at cash, then at overdue baki.
        const cash = await api.post<{ alerted: boolean; headline: string }>("/sim/cash-check");
        toast(cash.headline, cash.alerted ? "warn" : "info");
        const baki = await api.post<{ offered: boolean; headline: string }>("/sim/baki-check");
        if (baki.offered) toast(`${baki.headline} Prepare reminders from the Ledger tab.`, "info");
      },
    },
  ];

  return (
    <span className="inline-flex items-center gap-1">
      {controls.map((c) => (
        <button
          key={c.id}
          disabled={busy !== null}
          onClick={() => run(c.id, c.go)}
          title={c.label}
          className="label inline-flex h-7 items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 text-muted transition hover:border-ink hover:text-ink disabled:opacity-50"
        >
          {busy === c.id ? <Loader2 className="size-3.5 animate-spin" /> : c.icon}
          {c.label}
        </button>
      ))}
    </span>
  );
}
