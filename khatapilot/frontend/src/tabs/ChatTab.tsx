import { ArrowRight, ArrowUp, Camera, Forward, Images, Plus, ShieldAlert, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useRef, useState, type ChangeEvent, type ReactNode } from "react";

import { errorText } from "../api";
import { PastChats } from "../components/PastChats";
import { StatusIsland, type IslandTask } from "../components/StatusIsland";
import { VoiceButton } from "../components/VoiceButton";
import { dayLabel, greeting, taka, timeLabel } from "../format";
import { useData } from "../hooks";
import { replyLines, type Span } from "../markdown";
import { MAX_FORWARDED, readInvoicePhotos, SAMPLE_PHOTOS } from "../photo";
import { LANG_NAMES, useVoice } from "../voice";
import { useApp, type ChatMessage, type Forwarded, type TabId } from "../state";
import type { CashForecast, DayTotals, Debtor } from "../types";
import { Amount, Badge, Button, Eyebrow, Marker, Segmented, Stat } from "../ui";

const GROUPS: { label: string; items: string[] }[] = [
  { label: "Record", items: ["sold 3 bags of rice ৳1,500 cash", "Jamal ke 450 taka baki dilam"] },
  {
    label: "Ask",
    items: ["Will I run short of cash?", "who owes me money?", "Can I get a loan for Eid stock?", "what did you do this week?"],
  },
  { label: "Pay", items: ["pay Rahim Traders 5000", "pay Meghna Distributors 42175 on 20 Oct"] },
];
const SUGGESTIONS = GROUPS.flatMap((g) => g.items);

const OUTCOME_TONE = { ok: "ok", held: "warn", blocked: "bad", error: "bad" } as const;

/** The shop's numbers right now, from the same endpoints the Ledger and Forecast screens use. */
function useToday() {
  const { version } = useApp();
  const today = useData<{ totals: DayTotals; total_on_hand: number }>("/ledger/today", [version]);
  const debtors = useData<Debtor[]>("/debtors", [version]);
  const forecast = useData<CashForecast>("/forecast?days=14", [version]);
  return {
    onHand: today.data?.total_on_hand ?? null,
    sales: today.data?.totals.sales_paisa ?? null,
    owed: debtors.data ? debtors.data.reduce((sum, d) => sum + d.outstanding_paisa, 0) : null,
    debtorCount: debtors.data?.length ?? 0,
    forecast: forecast.data,
  };
}

function TodayStats() {
  const { setTab } = useApp();
  const t = useToday();
  const short = t.forecast?.first_shortfall;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Stat label="On hand now" value={<Amount paisa={t.onHand} />} hint="all accounts together" onClick={() => setTab("ledger")} />
      <Stat label="Sales today" value={<Amount paisa={t.sales} />} hint="from your books" onClick={() => setTab("ledger")} />
      <Stat
        label="Customers owe you"
        value={<Amount paisa={t.owed} />}
        hint={`${t.debtorCount} people with baki`}
        onClick={() => setTab("ledger")}
      />
      <Stat
        label="Cash outlook"
        value={t.forecast ? (short ? dayLabel(short.day) : "All clear") : "…"}
        hint={
          t.forecast ? (short ? `${taka(short.short_by_paisa, true)} below your buffer` : "14 days above your buffer") : "projecting"
        }
        tone={t.forecast ? (short ? "bad" : "ok") : undefined}
        onClick={() => setTab("forecast")}
      />
    </div>
  );
}

/** A slim version of the same numbers that stays above the conversation. */
function TodayStrip() {
  const { setTab } = useApp();
  const t = useToday();
  const short = t.forecast?.first_shortfall;
  const item = (label: string, value: ReactNode, tab: TabId, tone?: "bad" | "ok") => (
    <button onClick={() => setTab(tab)} className="label inline-flex shrink-0 items-center gap-2 text-faint transition hover:text-ink">
      <Marker tone={tone ?? "brand"} />
      {label}
      <span className={`font-sans text-[13px] tracking-normal normal-case ${tone === "bad" ? "text-bad" : "text-ink"}`}>{value}</span>
    </button>
  );
  return (
    <div className="no-scrollbar flex gap-6 overflow-x-auto border-b border-line bg-page px-4 py-2.5 sm:px-6">
      {item("On hand", <Amount paisa={t.onHand} />, "ledger")}
      {item("Sales today", <Amount paisa={t.sales} />, "ledger")}
      {item("Owed to you", <Amount paisa={t.owed} />, "ledger")}
      {t.forecast &&
        item("Cash", short ? `short ${dayLabel(short.day)}` : "no shortfall in 14 days", "forecast", short ? "bad" : "ok")}
    </div>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  const { review, pending } = useApp();
  const waiting = new Set(pending.map((p) => p.id));
  if (message.role === "system") {
    return (
      <div className="mx-auto max-w-lg rounded-xl border border-bad/35 bg-bad-soft px-4 py-2.5 text-center text-sm">
        {message.text}
      </div>
    );
  }
  if (message.role === "inbound") {
    return (
      <div className="mx-auto w-full max-w-xl rounded-2xl border-2 border-dashed border-bad/50 bg-bad-soft/50 p-4">
        <div className="label flex items-center gap-1.5 text-[10.5px] text-bad">
          <ShieldAlert className="size-3.5" /> Incoming message · {message.from} · not the owner
        </div>
        <p className="mt-2 font-mono text-[12.5px] leading-relaxed">{message.text}</p>
        <div className="label mt-2 text-[10px] text-faint">{timeLabel(message.at)}</div>
      </div>
    );
  }
  if (message.role === "owner") {
    return (
      <div className="flex flex-col items-end gap-1.5">
        {message.text && (
          <div className="max-w-[min(85%,34rem)] rounded-2xl rounded-br-md bg-brand px-4 py-2.5 text-[14.5px] leading-relaxed whitespace-pre-line text-brand-ink">
            {message.text}
          </div>
        )}
        {message.forwarded?.map((f, i) => (
          <div key={i} className="w-full max-w-[min(85%,34rem)] rounded-xl border border-dashed border-warn/60 bg-warn-soft/60 p-3">
            <div className="label flex items-center gap-1.5 text-[10px] text-warn">
              <Marker tone="warn" />
              {f.source === "invoice_text" ? "Forwarded invoice" : "Forwarded SMS"} · read as data, never as orders
            </div>
            <p className="mt-1.5 font-mono text-[11.5px] leading-relaxed whitespace-pre-line text-muted">{f.text}</p>
          </div>
        ))}
        <span className="label text-[10px] text-faint">You · {timeLabel(message.at)}</span>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-start gap-2">
      <span className="label flex items-center gap-1.5 text-[10px] text-faint">
        <Marker />
        KhataPilot · {timeLabel(message.at)}
      </span>
      {message.text && (
        <div className="max-w-[min(92%,38rem)] rounded-2xl rounded-tl-md border border-line bg-surface px-4 py-3 text-[14.5px] leading-relaxed">
          <ReplyText text={message.text} />
        </div>
      )}
      {!!message.outcomes?.length && (
        <div className="flex flex-wrap gap-1">
          {message.outcomes.map((o, i) => (
            <Badge key={i} tone={OUTCOME_TONE[o.status]}>
              {o.tool.replace(/_/g, " ")}: {o.status}
              {o.tier !== null ? ` · T${o.tier}` : ""}
            </Badge>
          ))}
        </div>
      )}
      {message.pending?.map((p) =>
        waiting.has(p.id) ? (
          <div key={p.id} className="w-full max-w-[min(92%,38rem)] rounded-2xl border border-warn/40 bg-warn-soft p-4">
            <div className="label flex items-center gap-1.5 text-[10.5px] text-warn">
              <Marker tone="warn" />
              Waiting for you{p.requires_pin ? " · needs your PIN" : ""}
            </div>
            <div className="mt-1.5 text-[14.5px] font-medium">{p.summary}</div>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button variant="primary" size="sm" onClick={() => review(p.id)}>
                Review{p.requires_pin ? " and enter PIN" : ""} <ArrowRight className="size-3.5" />
              </Button>
              <span className="text-xs text-muted">Nothing happens until you approve.</span>
            </div>
          </div>
        ) : (
          <div key={p.id} className="flex max-w-[min(92%,38rem)] items-start gap-2 text-xs text-muted">
            <Badge tone="neutral">settled</Badge>
            <span>{p.summary} (the decision trace shows what happened)</span>
          </div>
        ),
      )}
    </div>
  );
}

function Welcome() {
  const { shop, sendChat, sending } = useApp();
  return (
    <div className="mx-auto w-full max-w-4xl px-4 pt-14 pb-8 sm:px-8">
      <div className="relative rounded-[28px] border border-line px-4 py-9 sm:px-10 sm:py-12">
        {["-top-1.5 -left-1.5", "-top-1.5 -right-1.5", "-bottom-1.5 -left-1.5", "-bottom-1.5 -right-1.5"].map((pos) => (
          <span key={pos} aria-hidden className={`absolute ${pos} size-3 rounded-full border border-line-strong bg-page`} />
        ))}
        <div className="flex justify-center">
          <span className="inline-flex items-center gap-2.5 rounded-full border border-line bg-surface py-1 pr-3.5 pl-1">
            <span className="rounded-full bg-brand px-2.5 py-0.5 text-xs font-medium text-brand-ink">Guard on</span>
            <span className="font-mono text-[12px] text-muted">Every taka is checked before it moves</span>
          </span>
        </div>
        <h1 className="display mt-6 text-center text-[38px] sm:text-[52px]">
          {/* The greeting waits for the shop, so it always carries the owner's name; the line keeps its height meanwhile. */}
          {shop ? `${greeting(shop.now)}, ${shop.owner_name.split(" ")[0]}.` : <span className="invisible">Hello</span>}
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-center text-[15px] leading-relaxed text-muted">
          Tell me what you sold or bought, forward a bKash or Nagad SMS, ask about cash or a loan, or ask me to pay a supplier.
          English or Banglish both work.
        </p>

        <div className="mt-9">
          <TodayStats />
        </div>

        <div className="mt-9 grid gap-6 md:grid-cols-3">
          {GROUPS.map((g) => (
            <div key={g.label} className="space-y-3">
              <Eyebrow>{g.label}</Eyebrow>
              <div className="flex flex-wrap gap-2">
                {g.items.map((s) => (
                  <button
                    key={s}
                    onClick={() => sendChat(s)}
                    disabled={sending}
                    className="rounded-full border border-line-strong bg-surface px-3.5 py-1.5 text-left text-[13px] transition hover:border-ink disabled:opacity-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>

        <p className="mt-9 text-center text-xs text-faint">
          Anything that moves money goes through the guard, and every decision appears in the decision trace. Type “stop” at
          any time to freeze everything.
        </p>
      </div>
    </div>
  );
}

/** One block in the forward box. Invoice photos each get their own, with the photo beside the text read from it. */
interface Draft extends Forwarded {
  id: string;
  photo?: string; // object URL of the photo the text was read from
}

let draftCount = 0;
const blank = (source: Forwarded["source"] = "forwarded_sms"): Draft => ({ id: `draft-${++draftCount}`, source, text: "" });
const photos = (n: number) => `${n} invoice photo${n === 1 ? "" : "s"}`;

/** Camera and Gallery slide up over the Photo button. Camera opens the phone's camera (a file picker on a computer); Gallery lets the owner pick several photos at once. */
function PhotoMenu({ busy, onCamera, onGallery }: { busy: boolean; onCamera: () => void; onGallery: () => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const options = [
    { key: "camera", label: "Camera", Icon: Camera, run: onCamera },
    { key: "gallery", label: "Gallery", Icon: Images, run: onGallery },
  ];
  return (
    <div ref={box} className="relative">
      <div className="pointer-events-none absolute bottom-full left-0 z-20 mb-2 flex flex-col-reverse items-start gap-2">
        <AnimatePresence>
          {open &&
            options.map(({ key, label, Icon, run }, i) => (
              <motion.button
                key={key}
                type="button"
                initial={{ opacity: 0, y: 12, scale: 0.9 }}
                animate={{ opacity: 1, y: 0, scale: 1, transition: { type: "spring", duration: 0.35, bounce: 0.25, delay: i * 0.05 } }}
                exit={{ opacity: 0, y: 8, scale: 0.95, transition: { duration: 0.14, delay: (options.length - 1 - i) * 0.03 } }}
                onClick={() => {
                  setOpen(false);
                  run();
                }}
                className="pointer-events-auto inline-flex items-center gap-2 rounded-full border border-line-strong bg-surface py-2 pr-4 pl-3 text-[13px] font-medium shadow-[0_10px_24px_-14px_rgba(40,36,32,0.55)] transition-colors hover:border-ink"
              >
                <Icon className="size-4" />
                {label}
              </motion.button>
            ))}
        </AnimatePresence>
      </div>
      <Button
        type="button"
        variant={open ? "secondary" : "ghost"}
        size="sm"
        title="Photograph an invoice"
        aria-expanded={open}
        busy={busy}
        onClick={() => setOpen((o) => !o)}
      >
        {!busy && <Camera className="size-4" />}
        <span className="hidden sm:inline">{busy ? "Reading…" : "Photo"}</span>
      </Button>
    </div>
  );
}

export function ChatTab() {
  const { messages, sending, sendChat, newConversation, toast } = useApp();
  const [text, setText] = useState("");
  const [forwardOpen, setForwardOpen] = useState(false);
  const [drafts, setDrafts] = useState<Draft[]>(() => [blank()]);
  const [scanning, setScanning] = useState(false);
  const [tasks, setTasks] = useState<IslandTask[]>([]);
  const [hurry, setHurry] = useState(0); // back at the message box: finished island tasks go sooner
  const latest = useRef(drafts); // a Retry pressed later must count the blocks there are by then
  latest.current = drafts;
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const gallery = useRef<HTMLInputElement>(null);
  const voice = useVoice({
    text,
    onText: setText,
    onError: (message) => toast(message, "bad"),
    recent: messages.filter((m) => m.role === "owner").slice(-3).map((m) => m.text).join(" "),
  });

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, sending]);

  const paste = drafts.find((d) => !d.photo); // the block the owner types or pastes into
  const photoCount = drafts.filter((d) => d.photo).length;

  function patchDraft(id: string, patch: Partial<Draft>) {
    setDrafts((ds) => ds.map((d) => (d.id === id ? { ...d, ...patch } : d)));
  }

  function dropDraft(draft: Draft) {
    if (draft.photo) URL.revokeObjectURL(draft.photo);
    setDrafts((ds) => {
      const left = ds.filter((d) => d.id !== draft.id);
      return left.length ? left : [blank("invoice_text")];
    });
  }

  function patchTask(id: string, patch: Partial<IslandTask>) {
    setTasks((ts) => ts.map((t) => (t.id === id ? { ...t, ...patch } : t)));
  }

  const dropTask = useCallback((id: string) => setTasks((ts) => ts.filter((t) => t.id !== id)), []);

  /** Read the photos, then put each one's text in the forward box for the owner to check. Nothing is sent yet. */
  async function scan(files: Blob[]) {
    const room = MAX_FORWARDED - latest.current.filter((d) => d.text.trim()).length;
    const batch = files.slice(0, Math.max(0, room));
    if (batch.length < files.length) {
      toast(
        batch.length
          ? `One message can carry ${MAX_FORWARDED} invoices, so I am reading the first ${batch.length}. Send these, then add the rest.`
          : `One message can carry ${MAX_FORWARDED} invoices. Send these first, then add the rest.`,
        "info",
      );
    }
    if (!batch.length) return;

    const id = `scan-${Date.now()}`;
    const many = batch.length > 1;
    setTasks((ts) => [
      ...ts.filter((t) => t.status === "running"),
      { id, status: "running", label: `Reading ${photos(batch.length)}`, progress: many ? 0 : undefined },
    ]);
    setScanning(true);
    try {
      const results = await readInvoicePhotos(batch, (done) => {
        if (many) patchTask(id, { progress: done / batch.length, detail: `${done} of ${batch.length}` });
      });
      const read: Draft[] = [];
      const failed: Blob[] = [];
      let why = "";
      results.forEach(({ reading, error }, i) => {
        if (reading?.readable) {
          read.push({ ...blank("invoice_text"), text: reading.text, photo: URL.createObjectURL(batch[i]) });
        } else {
          failed.push(batch[i]);
          why ||= reading ? reading.message : errorText(error);
        }
      });

      if (read.length) {
        setDrafts((ds) => [...ds.filter((d) => d.text.trim() || d.photo), ...read]);
        setForwardOpen(true);
      }
      const retry = { label: "Retry", onClick: () => (dropTask(id), void scan(failed)) };
      if (!failed.length) {
        patchTask(id, { status: "done", label: `Read ${photos(read.length)}`, detail: "check, then send", progress: undefined });
      } else if (read.length) {
        patchTask(id, {
          status: "error",
          label: `Read ${read.length} of ${batch.length}`,
          detail: `${failed.length} unreadable`,
          note: why,
          progress: undefined,
          action: retry,
        });
      } else {
        patchTask(id, {
          status: "error",
          label: `Could not read ${photos(batch.length)}`,
          detail: "tap for why",
          note: why,
          progress: undefined,
          action: retry,
        });
      }
      if (results.some((r) => r.reading?.truncated)) {
        toast(`A long invoice was cut to its first 2000 characters. Check it against the paper.`, "info");
      }
    } finally {
      setScanning(false);
    }
  }

  async function scanSample(url: string) {
    try {
      await scan([await (await fetch(url)).blob()]);
    } catch (e) {
      toast(errorText(e), "bad");
    }
  }

  function pick(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (files.length) void scan(files);
  }

  async function submit() {
    voice.finish();
    const forwarded = forwardOpen
      ? drafts.filter((d) => d.text.trim()).map((d) => ({ source: d.source, text: d.text.trim() }))
      : [];
    if (!text.trim() && !forwarded.length) return;
    const message = text.trim();
    setText("");
    drafts.forEach((d) => d.photo && URL.revokeObjectURL(d.photo));
    setDrafts([blank(paste?.source)]);
    setForwardOpen(false);
    await sendChat(message, forwarded);
    input.current?.focus();
  }

  const empty = messages.length === 0;
  return (
    <div className="flex h-full min-h-[480px] flex-col">
      {!empty && <TodayStrip />}
      <div className="relative min-h-0 flex-1">
        <div className="absolute top-2.5 right-3 z-10 sm:right-5">
          <PastChats />
        </div>
        <div className="thin-scrollbar h-full overflow-y-auto">
          {empty ? (
            <Welcome />
          ) : (
            <div className="mx-auto max-w-3xl space-y-6 px-4 pt-14 pb-6 sm:px-6">
              {messages.map((m) => (
                <Bubble key={m.id} message={m} />
              ))}
              {sending && (
                <div className="flex items-center gap-3">
                  <span className="typing inline-flex gap-1" aria-hidden>
                    <span className="size-1.5 rounded-full bg-ink" />
                    <span className="size-1.5 rounded-full bg-ink" />
                    <span className="size-1.5 rounded-full bg-ink" />
                  </span>
                  <span className="label text-[10.5px] text-muted">Thinking · the guard checks every step</span>
                </div>
              )}
              <div ref={end} />
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-line bg-page px-4 pt-3 pb-4 sm:px-6">
        <div className="relative mx-auto max-w-3xl">
          {/* Floats over the end of the conversation, so the message box never moves. */}
          <div className="pointer-events-none absolute inset-x-0 bottom-full z-30 mb-3 flex justify-center">
            <StatusIsland tasks={tasks} onDismiss={dropTask} hurry={hurry} className="pointer-events-auto" />
          </div>
          {!empty && (
            <div className="no-scrollbar mb-2.5 flex gap-1.5 overflow-x-auto">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => sendChat(s)}
                  disabled={sending}
                  className="shrink-0 rounded-full border border-line px-3 py-1 text-xs text-muted transition hover:border-ink hover:text-ink disabled:opacity-50"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          <div className="glow-ring rounded-2xl">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void submit();
              }}
              onPointerDown={() => setHurry((h) => h + 1)}
              className="relative rounded-2xl border border-line-strong bg-surface p-2 shadow-[0_10px_30px_-18px_rgba(40,36,32,0.45)]"
            >
              {forwardOpen && (
                <div className="mb-2 rounded-xl border border-dashed border-warn/60 bg-warn-soft/50 p-2.5">
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <span className="label flex items-center gap-1.5 text-[10.5px] text-warn">
                      <Marker tone="warn" />
                      Forward{photoCount > 1 ? ` · ${photos(photoCount)}` : ""}
                    </span>
                    {paste && (
                      <Segmented
                        value={paste.source}
                        onChange={(source) => patchDraft(paste.id, { source })}
                        options={[
                          { value: "forwarded_sms", label: "bKash / Nagad / bank SMS" },
                          { value: "invoice_text", label: "Invoice text" },
                        ]}
                      />
                    )}
                    <span className="text-xs text-muted">Read as data only, never as orders.</span>
                  </div>
                  <div className="thin-scrollbar max-h-[45vh] space-y-2 overflow-y-auto">
                    {drafts.map((d, i) => (
                      <div key={d.id} className="flex gap-2.5">
                        {d.photo && (
                          <a href={d.photo} target="_blank" rel="noreferrer" title="Open the photo" className="shrink-0">
                            <img src={d.photo} alt={`Invoice photo ${i + 1}`} className="h-28 w-20 rounded-lg border border-line object-cover sm:w-24" />
                          </a>
                        )}
                        <textarea
                          value={d.text}
                          onChange={(e) => patchDraft(d.id, { text: e.target.value })}
                          rows={d.photo ? 5 : 3}
                          aria-label={d.photo ? `Text read from invoice photo ${i + 1}` : "Forwarded text"}
                          placeholder="Paste the SMS or invoice here"
                          className="w-full min-w-0 rounded-lg border border-line bg-surface px-3 py-2 font-mono text-xs outline-none placeholder:text-faint focus:border-ink focus-visible:outline-none"
                        />
                        {d.photo && (
                          <button
                            type="button"
                            onClick={() => dropDraft(d)}
                            title="Leave this invoice out"
                            aria-label={`Remove invoice photo ${i + 1}`}
                            className="-ml-1 grid size-7 shrink-0 place-items-center rounded-full text-muted transition hover:bg-raised hover:text-ink"
                          >
                            <X className="size-4" />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                  {photoCount > 0 ? (
                    <p className="mt-2 text-xs text-muted">
                      Read from your {photoCount > 1 ? "photos" : "photo"}. Check {photoCount > 1 ? "each one" : "it"} against the paper and fix
                      anything misread before you send.
                    </p>
                  ) : (
                    paste?.source === "invoice_text" && (
                      <p className="mt-2 text-xs text-muted">
                        Or take a photo with <Camera className="inline size-3.5 align-[-2px]" />. No invoice to hand? Try{" "}
                        {SAMPLE_PHOTOS.map((s, i) => (
                          <span key={s.url}>
                            {i > 0 && " or "}
                            <button
                              type="button"
                              disabled={scanning}
                              onClick={() => void scanSample(s.url)}
                              className="text-ink underline underline-offset-2 disabled:opacity-50"
                            >
                              {s.label}
                            </button>
                          </span>
                        ))}
                        .
                      </p>
                    )
                  )}
                </div>
              )}
              <textarea
                ref={input}
                value={text}
                onFocus={() => setHurry((h) => h + 1)}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void submit();
                  }
                }}
                rows={1}
                aria-label="Message"
                placeholder={voice.listening ? "Listening… speak now" : "Type or talk, or say “stop” to freeze everything…"}
                className="field-sizing-content block max-h-40 min-h-11 w-full resize-none bg-transparent px-2.5 py-2 text-[15px] outline-none placeholder:text-faint focus-visible:outline-none"
              />
              <div className="flex items-center gap-1 pt-1">
                <Button type="button" variant="ghost" size="sm" title="New conversation" onClick={newConversation}>
                  <Plus className="size-4" />
                  <span className="hidden sm:inline">New</span>
                </Button>
                <PhotoMenu busy={scanning} onCamera={() => camera.current?.click()} onGallery={() => gallery.current?.click()} />
                <Button
                  type="button"
                  variant={forwardOpen ? "secondary" : "ghost"}
                  size="sm"
                  title="Forward an SMS or invoice"
                  onClick={() => setForwardOpen((o) => !o)}
                >
                  <Forward className="size-4" />
                  <span className={voice.listening ? "hidden" : "hidden sm:inline"}>Forward SMS or invoice</span>
                </Button>
                <div className="ml-auto flex items-center gap-1.5">
                  {voice.supported && (
                    <VoiceButton
                      listening={voice.listening}
                      level={voice.level}
                      language={LANG_NAMES[voice.lang] ?? "English"}
                      onStart={voice.start}
                      onStop={voice.stop}
                    />
                  )}
                  <Button type="submit" variant="primary" size="icon" busy={sending} aria-label="Send">
                    {!sending && <ArrowUp className="size-4" />}
                  </Button>
                </div>
              </div>
              <input ref={camera} type="file" accept="image/*" capture="environment" hidden onChange={pick} />
              <input ref={gallery} type="file" accept="image/*" multiple hidden onChange={pick} />
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}

function Inline({ spans }: { spans: Span[] }) {
  return (
    <>
      {spans.map((s, i) =>
        s.bold ? (
          <strong key={i} className="font-semibold">
            {s.text}
          </strong>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  );
}

/** A reply with the model's light markdown (**bold**, "- " bullets) shown as formatting, never as markup. */
function ReplyText({ text }: { text: string }) {
  return (
    <>
      {replyLines(text).map((line, i) =>
        line.bullet ? (
          <div key={i} className="flex gap-2 pl-1">
            <span aria-hidden className="text-faint">
              •
            </span>
            <span className="min-w-0">
              <Inline spans={line.spans} />
            </span>
          </div>
        ) : line.spans.length ? (
          <div key={i}>
            <Inline spans={line.spans} />
          </div>
        ) : (
          <div key={i} aria-hidden className="h-2.5" />
        ),
      )}
    </>
  );
}
