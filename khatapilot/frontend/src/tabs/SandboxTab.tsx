import { useState } from "react";

import { dateTimeLabel } from "../format";
import { useData } from "../hooks";
import { useApp } from "../state";
import type { SandboxAccount, SandboxTxn } from "../types";
import { Amount, Badge, Card, Empty, Loading, Note, Page, PageHeader, tdClass, thClass } from "../ui";

const PROVIDERS: Record<string, string> = { bkash: "bKash wallet", nagad: "Nagad wallet", bank: "Bank account" };

export function SandboxTab() {
  const { version } = useApp();
  const accounts = useData<{ accounts: SandboxAccount[] }>("/sandbox/accounts", [version]);
  const [selected, setSelected] = useState<string | null>(null);
  const account = selected ?? accounts.data?.accounts[0]?.id ?? null;
  const statement = useData<{ transactions: SandboxTxn[] }>(
    account ? `/sandbox/accounts/${encodeURIComponent(account)}/transactions?limit=40` : null,
    [version, account],
  );

  return (
    <Page>
      <PageHeader
        eyebrow="Sandbox provider"
        title="Where the simulated money lives"
        description="KhataPilot's own sandbox (a separate service on port 8100) stands in for bKash, Nagad, the bank and a lender."
      />

      <div className="rounded-2xl border-2 border-dashed border-warn/60 bg-warn-soft px-5 py-4">
        <p className="text-sm leading-relaxed">
          No real money exists here. The provider refuses any payment without a guard-signed authorization bound to the exact
          payee and amount.
        </p>
      </div>

      {accounts.error ? (
        <Note tone="bad">{accounts.error}</Note>
      ) : !accounts.data ? (
        <Loading />
      ) : (
        <div className="grid gap-4 sm:grid-cols-3">
          {accounts.data.accounts.map((a) => {
            const active = a.id === account;
            return (
              <button
                key={a.id}
                onClick={() => setSelected(a.id)}
                aria-pressed={active}
                className={`flex min-h-40 flex-col rounded-2xl border p-5 text-left transition ${
                  active ? "border-bar bg-bar text-bar-ink" : "border-line bg-surface hover:border-ink"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{PROVIDERS[a.provider] ?? a.provider}</span>
                  {a.frozen && <Badge tone="bad">frozen</Badge>}
                  <span className={`ml-auto font-mono text-xs ${active ? "text-bar-muted" : "text-muted"}`}>{a.number_masked}</span>
                </div>
                <Amount paisa={a.balance_paisa} whole={false} className="display mt-auto pt-6 text-[30px]" />
                <div className={`label mt-1 text-[10px] ${active ? "text-bar-muted" : "text-faint"}`}>Available balance</div>
                {a.frozen_reason && <div className="mt-2 text-xs text-bad">{a.frozen_reason}</div>}
              </button>
            );
          })}
        </div>
      )}

      <Card eyebrow="Statement" title="As the provider reports it" flush>
        {!statement.data ? (
          <div className="px-5"><Loading /></div>
        ) : statement.data.transactions.length === 0 ? (
          <Empty>No transactions.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-y border-line bg-raised/50">
                <tr>
                  <th className={thClass}>When</th>
                  <th className={thClass}>TrxID</th>
                  <th className={thClass}>Type</th>
                  <th className={thClass}>Counterparty</th>
                  <th className={`${thClass} text-right`}>Amount</th>
                  <th className={`${thClass} text-right`}>Balance</th>
                </tr>
              </thead>
              <tbody>
                {statement.data.transactions.map((t) => (
                  <tr key={t.trx_id} className="border-t border-line first:border-0 hover:bg-raised/40">
                    <td className={`${tdClass} font-mono text-[11.5px] whitespace-nowrap text-muted`}>{dateTimeLabel(t.occurred_at)}</td>
                    <td className={`${tdClass} font-mono text-xs`}>
                      <div className="flex flex-wrap items-center gap-1.5">
                        {t.trx_id}
                        {t.authorization_id && <Badge tone="brand">guard-signed</Badge>}
                      </div>
                    </td>
                    <td className={`${tdClass} text-xs`}>{t.txn_type.replace(/_/g, " ")}</td>
                    <td className={`${tdClass} text-xs`}>
                      {t.counterparty_name ?? ""} <span className="font-mono text-faint">{t.counterparty_masked}</span>
                    </td>
                    <td className={`${tdClass} text-right font-medium whitespace-nowrap ${t.direction === "credit" ? "text-ok" : ""}`}>
                      {t.direction === "credit" ? "+" : "−"}
                      <Amount paisa={t.amount_paisa} whole={false} />
                    </td>
                    <td className={`${tdClass} text-right text-muted`}>
                      <Amount paisa={t.balance_after_paisa} whole={false} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </Page>
  );
}
