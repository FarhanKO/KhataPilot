import { ArrowUpRight, Lock } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useData } from "../hooks";
import { useApp } from "../state";
import type { SandboxAccount } from "../types";
import { Amount, Eyebrow } from "../ui";

const ORDER = ["bkash", "nagad", "bank"];
const NAMES: Record<string, string> = { bkash: "bKash", nagad: "Nagad", bank: "Bank" };
const SHOW_CHANGE_MS = 8000;

interface Change {
  delta: number;
  key: number;
}

/** The simulated provider's balances, live beside the trace: when the guard lets a payment through the money
 *  visibly moves, and when it refuses one nothing changes here. */
export function ProviderStrip() {
  const { version, setTab } = useApp();
  const accounts = useData<{ accounts: SandboxAccount[] }>("/sandbox/accounts", [version], 5000);
  const previous = useRef<Record<string, number> | null>(null);
  const [changes, setChanges] = useState<Record<string, Change>>({});

  useEffect(() => {
    if (!accounts.data) return;
    const now = Object.fromEntries(accounts.data.accounts.map((a) => [a.id, a.balance_paisa]));
    const before = previous.current;
    previous.current = now;
    if (!before) return;
    const moved = Object.entries(now).filter(([id, paisa]) => before[id] !== undefined && before[id] !== paisa);
    if (!moved.length) return;
    const key = Date.now();
    setChanges((c) => ({ ...c, ...Object.fromEntries(moved.map(([id, paisa]) => [id, { delta: paisa - before[id], key }])) }));
    setTimeout(() => setChanges((c) => Object.fromEntries(Object.entries(c).filter(([, v]) => v.key !== key))), SHOW_CHANGE_MS);
  }, [accounts.data]);

  const list = [...(accounts.data?.accounts ?? [])].sort((a, b) => ORDER.indexOf(a.provider) - ORDER.indexOf(b.provider));
  const frozen = list.some((a) => a.frozen);
  const cells: (SandboxAccount | null)[] = list.length ? list : ORDER.map(() => null);

  return (
    <section aria-label="Simulated provider balances">
      <button onClick={() => setTab("sandbox")} className="group mb-2 flex w-full items-center gap-2 text-left">
        <Eyebrow tone={frozen ? "bad" : "info"} className="text-ink">
          Provider balances
        </Eyebrow>
        <span className={`label text-[10px] ${frozen ? "text-bad" : "text-warn"}`}>{frozen ? "frozen" : "simulated"}</span>
        <span className="label ml-auto inline-flex items-center gap-1 text-[10px] text-faint transition group-hover:text-ink">
          Statement <ArrowUpRight className="size-3" />
        </span>
      </button>
      {accounts.error ? (
        <p className="text-xs text-muted">The simulated provider is not reachable.</p>
      ) : (
        <div className="grid grid-cols-3 gap-2">
          {cells.map((a, i) => {
            const change = a ? changes[a.id] : undefined;
            return (
              <div
                key={a ? `${a.id}-${change?.key ?? 0}` : i}
                className={`rounded-xl border bg-surface px-3 py-2 ${change ? "balance-moved border-line-strong" : "border-line"}`}
              >
                <div className="label flex items-center gap-1 text-[10px] text-faint">
                  {a ? (NAMES[a.provider] ?? a.provider) : NAMES[ORDER[i]]}
                  {a?.frozen && <Lock className="size-3 text-bad" aria-label="frozen" />}
                  {change && (
                    <span className={`ml-auto font-medium normal-case ${change.delta > 0 ? "text-ok" : "text-ink"}`}>
                      {change.delta > 0 ? "+" : "−"}
                      <Amount paisa={Math.abs(change.delta)} />
                    </span>
                  )}
                </div>
                <Amount paisa={a?.balance_paisa} className="mt-0.5 block text-[17px] leading-tight font-medium" />
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
