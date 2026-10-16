import { ChevronRight, Link2, ShieldCheck, ShieldX } from "lucide-react";
import { useState } from "react";

import { api, errorText } from "../api";
import { dateTimeLabel } from "../format";
import { useData } from "../hooks";
import { useApp } from "../state";
import type { AuditEntry } from "../types";
import { Badge, Button, Empty, Loading, Note, Page, PageHeader, Segmented, type Tone } from "../ui";

const EVENTS = ["all", "decision", "execution", "approval", "pin", "alert", "notification", "kill_switch", "owner_control", "consent"];

function tone(decision: string): Tone {
  if (["BLOCKED", "FAILED", "REFUSED", "PIN_WRONG", "PIN_LOCKED", "FROZEN"].includes(decision)) return "bad";
  if (decision.startsWith("HELD") || decision === "ALERT") return "warn";
  if (["INFO", "REJECTED", "WITHDRAWN"].includes(decision)) return "neutral";
  return "ok";
}

interface ChainReport {
  ok: boolean;
  entries: number;
  head_hash: string;
  first_bad_seq?: number | null;
  problem?: string | null;
}

export function AuditTab() {
  const { version } = useApp();
  const [event, setEvent] = useState("all");
  const [onlyRefused, setOnlyRefused] = useState<"all" | "refused">("all");
  const [open, setOpen] = useState<number | null>(null);
  const [chain, setChain] = useState<ChainReport | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const path = `/audit?limit=300${event === "all" ? "" : `&event=${event}`}`;
  const { data, error } = useData<AuditEntry[]>(path, [version]);
  const rows = (data ?? []).filter((e) => onlyRefused === "all" || tone(e.decision) === "bad");

  async function verify() {
    setVerifying(true);
    setProblem(null);
    try {
      setChain(await api.get<ChainReport>("/audit/verify"));
    } catch (e) {
      setProblem(errorText(e));
    } finally {
      setVerifying(false);
    }
  }

  return (
    <Page>
      <PageHeader
        eyebrow="Audit log"
        title="A chain nobody can quietly edit"
        description="Every decision, approval and payment is appended here and chained by hash to the one before, so changing or deleting any entry breaks the chain. The database refuses edits and deletions outright."
        actions={
          <Button variant="primary" busy={verifying} onClick={verify}>
            <Link2 className="size-4" /> Verify chain
          </Button>
        }
      />

      {chain && (
        <div className={`rounded-2xl border px-5 py-4 ${chain.ok ? "border-ok/30 bg-ok-soft" : "border-bad/35 bg-bad-soft"}`}>
          <div className={`flex items-center gap-2 text-[16px] font-medium ${chain.ok ? "text-ok" : "text-bad"}`}>
            {chain.ok ? <ShieldCheck className="size-5" /> : <ShieldX className="size-5" />}
            {chain.ok
              ? `Chain intact: ${chain.entries.toLocaleString()} entries verified.`
              : `Chain broken at entry ${chain.first_bad_seq}: ${chain.problem}`}
          </div>
          <div className="mt-1.5 font-mono text-[11px] break-all text-muted">head {chain.head_hash}</div>
        </div>
      )}
      {problem && <Note tone="bad">{problem}</Note>}

      <div className="flex flex-wrap items-center gap-3">
        <div className="no-scrollbar -mx-1 flex max-w-full gap-1.5 overflow-x-auto px-1">
          {EVENTS.map((e) => (
            <button
              key={e}
              onClick={() => setEvent(e)}
              aria-pressed={event === e}
              className={`label shrink-0 rounded-full border px-3 py-1 text-[10.5px] transition ${
                event === e ? "border-ink bg-brand text-brand-ink" : "border-line text-muted hover:border-ink hover:text-ink"
              }`}
            >
              {e.replace("_", " ")}
            </button>
          ))}
        </div>
        <Segmented
          className="ml-auto"
          value={onlyRefused}
          onChange={setOnlyRefused}
          options={[
            { value: "all", label: "Everything" },
            { value: "refused", label: "Refused only" },
          ]}
        />
      </div>

      {error ? (
        <Note tone="bad">{error}</Note>
      ) : !data ? (
        <Loading />
      ) : rows.length === 0 ? (
        <Empty>No entries.</Empty>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-line bg-surface">
          {rows.map((e) => (
            <div key={e.seq} className="border-b border-line last:border-0">
              <button
                onClick={() => setOpen(open === e.seq ? null : e.seq)}
                aria-expanded={open === e.seq}
                className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 text-left transition hover:bg-raised/50 sm:flex-nowrap"
              >
                <ChevronRight className={`size-3.5 shrink-0 text-faint transition ${open === e.seq ? "rotate-90" : ""}`} />
                <span className="w-12 shrink-0 font-mono text-xs text-faint">#{e.seq}</span>
                <span className="label w-32 shrink-0 text-[10.5px] text-muted">{dateTimeLabel(e.ts)}</span>
                <Badge tone={tone(e.decision)}>{e.decision.replace(/_/g, " ")}</Badge>
                <span className="min-w-0 flex-1 text-sm">
                  <span className="font-medium">{e.summary}</span>
                  <span className="label ml-2 text-[10px] text-faint">
                    {e.event} · {e.actor} · {e.channel}
                    {e.tier !== null ? ` · tier ${e.tier}` : ""}
                  </span>
                </span>
              </button>
              {open === e.seq && (
                <div className="space-y-3 border-t border-dashed border-line bg-page/60 px-5 py-4 text-xs">
                  <div className="text-sm">{e.reason}</div>
                  {e.checks.length > 0 && (
                    <ul className="space-y-1.5">
                      {e.checks.map((c) => (
                        <li key={c.name} className="flex items-start gap-2">
                          <Badge tone={c.status === "block" ? "bad" : c.status === "escalate" ? "warn" : c.status === "pass" ? "ok" : "neutral"}>
                            {c.status}
                          </Badge>
                          <span>
                            <span className="font-medium">{c.label}</span> <span className="text-muted">{c.detail}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {e.sandbox_ref && <div>Provider reference: {e.sandbox_ref}</div>}
                  <div className="font-mono break-all text-faint">
                    prev {e.prev_hash.slice(0, 16)}… → hash {e.hash.slice(0, 16)}…
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Page>
  );
}
