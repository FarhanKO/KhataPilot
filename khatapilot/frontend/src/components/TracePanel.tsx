import { ChevronDown } from "lucide-react";
import { useMemo, useState } from "react";

import { timeLabel, TIER_NAMES } from "../format";
import type { AuditEntry, Check, Segment, TraceEvent } from "../types";
import { Badge, Empty, Eyebrow, Marker, Segmented, TONE_TEXT, type Tone } from "../ui";

type Audit = Extract<TraceEvent, { type: "audit" }>;
type Turn = Extract<TraceEvent, { type: "turn" }>;
type Reply = Extract<TraceEvent, { type: "reply" }>;
type Quarantine = Extract<TraceEvent, { type: "quarantine" }>;

interface Thread {
  id: string;
  last: number;
  turn?: Turn;
  reply?: Reply;
  quarantine: Quarantine[];
  audits: Audit[];
}

const SOURCE_NAMES: Record<string, string> = {
  chat: "Owner's message",
  forwarded_sms: "Forwarded SMS",
  invoice_text: "Invoice text",
  inbound_message: "Message",
};

// The newest few cards open with every check showing; older ones fold to their first line.
const OPEN_BY_DEFAULT = 3;

function threads(events: TraceEvent[]): Thread[] {
  const byId = new Map<string, Thread>();
  // An approval, PIN entry or later execution belongs on the card of the decision it settles.
  const byDecision = new Map<string, string>();
  for (const event of events) {
    let id = event.request_id;
    if (event.type === "audit" && event.event !== "decision" && event.decision_id && byDecision.has(event.decision_id)) {
      id = byDecision.get(event.decision_id);
    }
    if (event.type === "audit" && event.event === "decision" && event.decision_id && id) byDecision.set(event.decision_id, id);
    if (!id || event.type === "llm_step" || event.type === "tool") continue;
    if (event.type === "audit" && (event.event === "system" || id === "seed")) continue;
    const thread = byId.get(id) ?? { id, last: 0, quarantine: [], audits: [] };
    thread.last = Math.max(thread.last, event.trace_id);
    if (event.type === "turn") thread.turn = event;
    else if (event.type === "reply") thread.reply = event;
    else if (event.type === "quarantine") thread.quarantine.push(event);
    else if (event.type === "audit") thread.audits.push(event);
    byId.set(id, thread);
  }
  return [...byId.values()].sort((a, b) => b.last - a.last).slice(0, 40);
}

function verdict(thread: Thread): { tone: Tone; label: string } {
  const decisions = thread.audits.filter((a) => a.event === "decision").map((a) => a.decision);
  const results = thread.audits.map((a) => a.decision);
  if (decisions.includes("BLOCKED") || results.includes("REFUSED")) return { tone: "bad", label: "BLOCKED" };
  if (results.includes("FROZEN")) return { tone: "bad", label: "STOPPED" };
  if (results.includes("FAILED")) return { tone: "bad", label: "FAILED" };
  if (results.some((d) => d === "EXECUTED" || d === "SUBMITTED")) return { tone: "ok", label: "DONE" };
  if (results.includes("SCHEDULED")) return { tone: "ok", label: "SCHEDULED" };
  if (results.includes("REJECTED")) return { tone: "neutral", label: "REJECTED" };
  if (decisions.some((d) => d.startsWith("HELD"))) return { tone: "warn", label: "WAITING FOR YOU" };
  if (results.includes("ALERT")) return { tone: "warn", label: "ALERT" };
  if (decisions.length || results.includes("RECORDED")) return { tone: "ok", label: "ALLOWED" };
  return { tone: "neutral", label: "INFO" };
}

const CHECK_DOT: Record<Check["status"], string> = {
  pass: "bg-ok",
  block: "bg-bad",
  escalate: "bg-warn",
  info: "bg-info",
  skip: "bg-surface border border-line-strong",
};

function tierTone(tier: number | null): Tone {
  return tier === 3 ? "warn" : tier === 2 ? "ok" : tier === 1 ? "info" : "neutral";
}

function decisionTone(decision: string): Tone {
  if (decision === "BLOCKED" || decision === "FAILED" || decision === "REFUSED" || decision === "FROZEN") return "bad";
  if (decision.startsWith("HELD") || decision === "ALERT" || decision.startsWith("PIN_")) return decision === "PIN_OK" ? "ok" : "warn";
  if (decision === "INFO" || decision === "REJECTED" || decision === "WITHDRAWN") return "neutral";
  return "ok";
}

/** The verdict, inked like a rubber stamp; refusals land with a thump. */
function Stamp({ tone, label, big = false }: { tone: Tone; label: string; big?: boolean }) {
  return (
    <span
      className={`label inline-block shrink-0 rounded-md border-current font-semibold ${tone === "neutral" ? "text-muted" : TONE_TEXT[tone]} ${
        big ? "stamp-in border-2 px-2 py-1 text-[12px] tracking-[0.14em]" : "stamp border-[1.5px] px-1.5 py-0.5 text-[10px] tracking-[0.1em]"
      }`}
    >
      {label}
    </span>
  );
}

function Decision({ entry }: { entry: AuditEntry }) {
  const [showAll, setShowAll] = useState(false);
  const relevant = entry.checks.filter((c) => c.status !== "skip");
  const skipped = entry.checks.length - relevant.length;
  const blocked = entry.decision === "BLOCKED";
  const checks = showAll ? entry.checks : relevant;
  return (
    <div className={`rounded-xl border p-3 ${blocked ? "border-bad/40 bg-bad-soft/70" : "border-line bg-page/60"}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-auto text-[13px] font-medium">{entry.summary}</span>
        {entry.tier !== null && (
          <Badge tone={tierTone(entry.tier)}>
            T{entry.tier} · {TIER_NAMES[entry.tier]}
          </Badge>
        )}
        <Badge tone={decisionTone(entry.decision)}>{entry.decision.replace(/_/g, " ")}</Badge>
      </div>
      {checks.length > 0 && (
        <ol className="relative mt-3 space-y-2 pl-5 before:absolute before:top-1.5 before:bottom-1.5 before:left-[4.5px] before:w-px before:bg-line-strong">
          {checks.map((check) => (
            <li key={check.name} className="relative text-xs leading-snug">
              <span className={`absolute top-[3px] -left-5 size-2.5 rounded-full ring-2 ring-surface ${CHECK_DOT[check.status]}`} />
              <span className={`font-medium ${check.status === "block" ? "text-bad" : check.status === "skip" ? "text-faint" : ""}`}>
                {check.label}
              </span>
              <span className="text-muted"> · {check.detail}</span>
            </li>
          ))}
        </ol>
      )}
      {skipped > 0 && (
        <button onClick={() => setShowAll((s) => !s)} className="label mt-2 text-[10px] text-faint hover:text-ink">
          {showAll ? "Hide checks that did not apply" : `+${skipped} checks did not apply`}
        </button>
      )}
      <div className={`mt-2.5 border-t border-line pt-2 text-xs ${blocked ? "font-medium text-bad" : "text-muted"}`}>{entry.reason}</div>
    </div>
  );
}

function EventLine({ entry }: { entry: AuditEntry }) {
  return (
    <div className="flex items-start gap-2 text-xs leading-snug">
      <Badge tone={decisionTone(entry.decision)}>{entry.decision.replace(/_/g, " ")}</Badge>
      <span className="text-muted">
        <span className="font-medium text-ink">{entry.summary}</span>
        {entry.reason ? ` · ${entry.reason}` : ""}
      </span>
    </div>
  );
}

function Asked({ segments, folded }: { segments: Segment[]; folded: boolean }) {
  return (
    <div className="space-y-2">
      {segments.map((s, i) =>
        s.provenance === "owner" ? (
          <p key={i} className={`text-[14px] leading-snug ${folded ? "line-clamp-1" : ""}`}>
            “{s.text}”
          </p>
        ) : folded ? null : (
          <div key={i} className="rounded-lg border border-dashed border-warn/60 bg-warn-soft/60 px-2.5 py-2">
            <div className="label mb-1 flex items-center gap-1.5 text-[10px] text-warn">
              <Marker tone="warn" />
              {SOURCE_NAMES[s.source] ?? s.source} · untrusted data
            </div>
            <p className="line-clamp-4 font-mono text-[11px] leading-relaxed whitespace-pre-line text-muted">{s.text}</p>
          </div>
        ),
      )}
    </div>
  );
}

function ThreadCard({ thread, defaultOpen }: { thread: Thread; defaultOpen: boolean }) {
  const [override, setOverride] = useState<boolean | null>(null);
  const open = override ?? defaultOpen;
  const v = verdict(thread);
  const turn = thread.turn;
  const first = thread.audits[0];
  const external = turn?.sender === "external";
  const when = turn?.ts ?? first?.ts;
  const decisions = thread.audits.filter((a) => a.event === "decision");
  const others = thread.audits.filter((a) => a.event !== "decision");
  const blocked = v.tone === "bad";
  const refused = decisions.filter((d) => d.decision === "BLOCKED").length;
  const who = turn
    ? external
      ? `Not the owner · ${turn.sender_display}`
      : `Owner · ${turn.channel}`
    : first
      ? `${first.actor} · ${first.channel}`
      : "System";
  return (
    <article
      className={`flash-in relative overflow-hidden rounded-2xl border bg-surface ${
        blocked ? "border-bad/55 shadow-[0_0_0_3px_color-mix(in_srgb,var(--bad)_12%,transparent)]" : "border-line"
      }`}
    >
      {blocked && <span aria-hidden className="absolute inset-y-0 left-0 w-1 bg-bad" />}
      <header className="flex items-center gap-2 px-4 pt-3">
        <span className={`label flex min-w-0 items-center gap-1.5 text-[10.5px] ${external ? "text-bad" : "text-faint"}`}>
          <Marker tone={external ? "bad" : turn ? "brand" : "neutral"} />
          <span className="truncate">{who}</span>
        </span>
        <span className="ml-auto" />
        {!blocked && <Stamp tone={v.tone} label={v.label} />}
        <span className="label shrink-0 pl-1 text-[10.5px] text-faint">{when ? timeLabel(when) : ""}</span>
        <button
          onClick={() => setOverride(!open)}
          className="-mr-1 rounded p-0.5 text-faint hover:text-ink"
          aria-label={open ? "Fold this card" : "Show every check"}
          aria-expanded={open}
        >
          <ChevronDown className={`size-4 transition ${open ? "" : "-rotate-90"}`} />
        </button>
      </header>
      <div className="space-y-3 px-4 pt-2 pb-3.5">
        {(turn || blocked) && (
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">{turn && <Asked segments={turn.segments} folded={!open} />}</div>
            {blocked && <Stamp tone="bad" label={v.label} big />}
          </div>
        )}
        {blocked && v.label === "BLOCKED" && (
          <div className="label flex items-center gap-1.5 text-[10.5px] text-bad">
            <Marker tone="bad" />
            The guard refused {refused || 1} action{refused > 1 ? "s" : ""}
          </div>
        )}
        {open && (
          <>
            {thread.quarantine.map((q) => (
              <div key={q.trace_id} className="flex items-start gap-2 text-xs leading-snug">
                {q.suspected ? <Badge tone="bad">Hidden instruction</Badge> : <Badge tone="info">Parsed</Badge>}
                <span className="text-muted">
                  Block {q.block}: {q.summary}
                </span>
              </div>
            ))}
            {decisions.map((d) => (
              <Decision key={d.trace_id} entry={d} />
            ))}
            {others.length > 0 && (
              <div className="space-y-2">
                {others.map((o) => (
                  <EventLine key={o.trace_id} entry={o} />
                ))}
              </div>
            )}
          </>
        )}
        {!open && !turn && first && <p className="line-clamp-1 text-[13px] text-muted">{first.summary}</p>}
        {open && thread.reply && (
          <div className="border-t border-line pt-2.5 text-xs leading-relaxed text-muted">
            <span className="label mr-1.5 text-[10px] text-ink">Reply</span>
            {thread.reply.text}
          </div>
        )}
      </div>
    </article>
  );
}

export function TracePanel({ events, connected }: { events: TraceEvent[]; connected: boolean }) {
  const [filter, setFilter] = useState<"all" | "flagged">("all");
  const all = useMemo(() => threads(events), [events]);
  const flagged = all.filter((t) => ["bad", "warn"].includes(verdict(t).tone));
  const shown = filter === "flagged" ? flagged : all;
  const blockedCount = all.filter((t) => verdict(t).tone === "bad").length;
  return (
    <div className="space-y-4">
      <div className="space-y-2.5">
        <div className="flex items-center gap-3">
          <Eyebrow tone={connected ? "ok" : "warn"} className="text-ink">
            Decision trace
          </Eyebrow>
          <span className={`label text-[10px] ${connected ? "text-ok" : "text-warn"}`}>{connected ? "live" : "reconnecting"}</span>
          <Segmented
            className="ml-auto"
            value={filter}
            onChange={setFilter}
            options={[
              { value: "all", label: `All ${all.length}` },
              { value: "flagged", label: `Flagged ${flagged.length}` },
            ]}
          />
        </div>
        <p className="text-xs leading-relaxed text-muted">
          Every action the AI proposes goes through the guard. Each card shows who asked, the tier, every check and the
          decision.
          {blockedCount > 0 && (
            <span className="font-medium text-bad">
              {" "}
              {blockedCount} refused so far.
            </span>
          )}
        </p>
      </div>
      {shown.length === 0 ? (
        <Empty>{filter === "flagged" ? "Nothing refused or waiting." : "Nothing yet. Say something in the chat or press an attack button."}</Empty>
      ) : (
        <div className="space-y-3">
          {shown.map((t, i) => (
            <ThreadCard key={t.id} thread={t} defaultOpen={i < OPEN_BY_DEFAULT} />
          ))}
        </div>
      )}
    </div>
  );
}
