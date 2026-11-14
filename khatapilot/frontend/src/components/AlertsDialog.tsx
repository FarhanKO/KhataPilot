import { ArrowRight, Banknote, BellOff, Bot, CheckCheck, FileText, OctagonX, ShieldAlert, TrendingDown, Users, X } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState, type ComponentType } from "react";

import { priorityTone, SEGMENT_TONE } from "../alerts";
import { dateTimeLabel, dayLabel, timeLabel } from "../format";
import { useData } from "../hooks";
import { useApp, type TabId } from "../state";
import type { AlertItem, AlertSegment, AlertSegmentId, LlmMonth, MonthlyReport } from "../types";
import { Badge, Button, Eyebrow, Loading, Note, Segmented, TONE_TEXT } from "../ui";
// Loaded the first time a report is asked for, so the alerts board stays light.
const ReportDialog = lazy(() => import("./ReportDialog").then((m) => ({ default: m.ReportDialog })));
const ReportView = lazy(() => import("./ReportView").then((m) => ({ default: m.ReportView })));

const ICONS: Record<AlertSegmentId, ComponentType<{ className?: string }>> = {
  security: ShieldAlert,
  money: Banknote,
  cash: TrendingDown,
  customers: Users,
};

const TAB_NAMES: Record<AlertItem["tab"], string> = {
  chat: "Chat",
  ledger: "Ledger",
  forecast: "Forecast",
  savings: "Savings",
  credit: "Credit",
  loans: "Loans",
  controls: "Limits",
  audit: "Audit",
};

const STRIPE = { bad: "bg-bad", warn: "bg-warn", info: "bg-info", neutral: "bg-line-strong", ok: "bg-ok", brand: "bg-ink" };

type Filter = "all" | "important";

/** Unread urgent cards first, then the newest. */
function ordered(items: AlertItem[], isUnread: (i: AlertItem) => boolean): AlertItem[] {
  const pinned = (i: AlertItem) => (i.priority === "urgent" && isUnread(i) ? 0 : 1);
  return [...items].sort((a, b) => pinned(a) - pinned(b) || b.seq - a.seq);
}

/** "10:02" for today on the shop's (simulated) clock, "12 Oct" before that; the full time is in the tooltip. */
function shortWhen(iso: string, now: string | undefined): string {
  if (now && dayLabel(iso) === dayLabel(now)) return timeLabel(iso);
  return dayLabel(iso).replace(/^\w+ /, "");
}

function AlertCard({ item, unread, now, onOpen }: { item: AlertItem; unread: boolean; now?: string; onOpen: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const tone = priorityTone(item);
  return (
    <article
      className={`relative shrink-0 overflow-hidden rounded-xl border pl-4 transition ${
        unread ? "border-line-strong bg-surface" : "border-line bg-page/40"
      }`}
    >
      <span aria-hidden className={`absolute inset-y-0 left-0 w-1 ${STRIPE[tone]} ${unread ? "" : "opacity-40"}`} />
      <button onClick={() => setExpanded((e) => !e)} aria-expanded={expanded} className="block w-full py-3 pr-3 text-left">
        <div className="flex items-center gap-2">
          <Badge tone={tone}>{item.priority}</Badge>
          {unread && (
            <span className="label inline-flex items-center gap-1 text-[10px] text-ink">
              <span className="size-1.5 rounded-full bg-ink" /> new
            </span>
          )}
          <time dateTime={item.ts} title={dateTimeLabel(item.ts)} className="label ml-auto text-[10px] whitespace-nowrap text-faint">
            {shortWhen(item.ts, now)}
          </time>
        </div>
        <h4 className={`mt-2 text-[13.5px] leading-snug ${unread ? "font-medium" : ""} ${expanded ? "" : "line-clamp-2"}`}>
          {item.title}
        </h4>
        {item.body && (
          <p className={`mt-1 text-xs leading-relaxed text-muted ${expanded ? "" : "line-clamp-2"}`}>{item.body}</p>
        )}
      </button>
      <div className="flex items-center gap-2 border-t border-dashed border-line py-2 pr-2">
        <span className={`label text-[10px] ${item.count > 1 ? "text-muted" : "text-faint"}`}>
          {item.count > 1 ? `${item.count} times · since ${timeLabel(item.first_ts)}` : item.channel}
        </span>
        <button
          onClick={onOpen}
          className="ml-auto inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium text-ink transition hover:bg-raised"
        >
          {TAB_NAMES[item.tab]} <ArrowRight className="size-3" />
        </button>
      </div>
    </article>
  );
}

function SegmentColumn({ segment, filter, onGo }: { segment: AlertSegment; filter: Filter; onGo: (item: AlertItem) => void }) {
  const { alerts, shop } = useApp();
  const Icon = ICONS[segment.id];
  const items = ordered(
    segment.items.filter((i) => filter === "all" || i.priority !== "info"),
    alerts.isUnread,
  );
  const unread = items.filter(alerts.isUnread).length;
  const urgent = items.some((i) => i.priority === "urgent" && alerts.isUnread(i));

  return (
    <section
      aria-label={segment.label}
      className="flex h-full w-[86%] shrink-0 snap-start flex-col overflow-hidden rounded-2xl border border-line bg-raised/40 @2xl:w-[calc(50%-6px)] @4xl:w-auto @4xl:min-w-0 @4xl:flex-1 @4xl:basis-0"
    >
      <header className="shrink-0 border-b border-line px-4 pt-3.5 pb-3">
        <div className="flex items-center gap-2.5">
          <span
            className={`grid size-8 shrink-0 place-items-center rounded-lg border border-line bg-surface ${TONE_TEXT[SEGMENT_TONE[segment.id]]}`}
          >
            <Icon className="size-4" />
          </span>
          <h3 className="min-w-0 truncate text-[15px] font-medium tracking-[-0.01em]">{segment.label}</h3>
          {unread > 0 && (
            <span
              className={`shrink-0 rounded-full px-1.5 font-mono text-[10.5px] leading-[18px] font-medium ${
                urgent ? (segment.id === "security" ? "bg-bad text-white" : "bg-warn text-white") : "bg-ink text-page"
              }`}
            >
              {unread}
            </span>
          )}
        </div>
        <div className="mt-2 flex items-start gap-2">
          <p className="min-w-0 flex-1 text-xs leading-snug text-muted">{segment.hint}</p>
          {unread > 0 && (
            <button
              onClick={() => alerts.markRead(segment.id)}
              aria-label={`Mark ${segment.label} read`}
              title="Mark read"
              className="-mt-1 -mr-1.5 grid size-7 shrink-0 place-items-center rounded-full text-muted transition hover:bg-surface hover:text-ink"
            >
              <CheckCheck className="size-4" />
            </button>
          )}
        </div>
      </header>
      <div className="thin-scrollbar flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto p-3">
        {items.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 py-8 text-center text-xs text-faint">
            <BellOff className="size-4" />
            {filter === "important" && segment.items.length ? "Nothing important." : "All quiet."}
          </div>
        ) : (
          items.map((item) => (
            <AlertCard key={item.id} item={item} unread={alerts.isUnread(item)} now={shop?.now} onOpen={() => onGo(item)} />
          ))
        )}
      </div>
    </section>
  );
}

/** A one-line strip of what needs the owner right now; hidden when nothing does. */
function NowStrip({ onReview }: { onReview: () => void }) {
  const { alerts, pending, status } = useApp();
  const frozen = status?.kill_switch.on ?? false;
  const urgent = (alerts.feed?.segments ?? []).flatMap((s) => s.items).filter((i) => i.priority === "urgent" && alerts.isUnread(i));
  if (!pending.length && !frozen && !urgent.length) return null;
  return (
    <div className="no-scrollbar flex shrink-0 items-center gap-2 overflow-x-auto px-5 pb-3 whitespace-nowrap sm:px-6">
      {frozen && (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-stop px-3 py-1 text-xs font-medium text-white">
          <OctagonX className="size-3.5" /> STOP is on: everything is frozen
        </span>
      )}
      {urgent.length > 0 && (
        <span
          className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium ${
            urgent.some((i) => i.segment === "security") ? "border-bad/35 bg-bad-soft text-bad" : "border-warn/30 bg-warn-soft text-warn"
          }`}
        >
          {urgent.length} urgent unread · <span className="max-w-[16rem] truncate font-normal text-ink">{urgent[0].title}</span>
        </span>
      )}
      {pending.length > 0 && (
        <button
          onClick={onReview}
          className="inline-flex items-center gap-1.5 rounded-full border border-ink bg-brand px-3 py-1 text-xs font-medium text-brand-ink transition hover:opacity-90"
        >
          {pending.length} waiting for your approval · Review <ArrowRight className="size-3" />
        </button>
      )}
    </div>
  );
}

/** This month's AI use against the shop's monthly budget, in one line with a meter. */
function AiUse({ open }: { open: boolean }) {
  const { version } = useApp();
  const { data } = useData<{ month: LlmMonth | null }>(open ? "/llm/usage" : null, [open, version]);
  const m = data?.month;
  if (!m) return null;
  const used = m.used_pct === null ? null : Math.min(100, m.used_pct);
  const tone = m.capped ? "bg-bad" : m.warn ? "bg-warn" : "bg-ok";
  return (
    <div className="mx-5 mb-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-line bg-page/60 px-3.5 py-2 sm:mx-6">
      <span className="label flex shrink-0 items-center gap-1.5 text-[10px] text-muted">
        <Bot className="size-3.5" /> AI use · {m.month_label}
      </span>
      {used !== null && !m.offline_only && (
        <span
          className="h-1.5 w-28 shrink-0 overflow-hidden rounded-full bg-line"
          role="meter"
          aria-label="Share of this month's AI budget used"
          aria-valuenow={used}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <span className={`block h-full ${tone}`} style={{ width: `${Math.max(used, 2)}%` }} />
        </span>
      )}
      <span className={`min-w-0 flex-1 text-xs ${m.capped ? "text-bad" : "text-muted"}`}>{m.text}</span>
      {!m.offline_only && (
        <span className="label shrink-0 text-[10px] text-faint" title="Turns this month: planned by the model / answered free offline">
          {m.turns.model} model · {m.free_turns} free
        </span>
      )}
    </div>
  );
}

/** The alerts board as a pop-up: four segments side by side, each scrolling on its own. */
export function AlertsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { alerts, setTab, review } = useApp();
  const [filter, setFilter] = useState<Filter>("all");
  const [choosing, setChoosing] = useState(false);
  const [report, setReport] = useState<MonthlyReport | null>(null);
  const ref = useRef<HTMLDialogElement>(null);
  const feed = alerts.feed;

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const go = (item: AlertItem) => {
    alerts.markSeen(item);
    onClose();
    setTab(item.tab as TabId);
  };

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      // A click on the dimmed backdrop lands on the dialog element itself.
      onClick={(e) => e.target === e.currentTarget && onClose()}
      aria-label="Alerts"
      className="m-0 h-full max-h-none w-full max-w-none border-line bg-surface p-0 text-ink shadow-[0_30px_80px_-20px_rgba(20,15,10,0.45)] backdrop:bg-[#1a1714]/55 backdrop:backdrop-blur-[2px] sm:m-auto sm:h-[min(88vh,880px)] sm:w-[calc(100%-3rem)] sm:max-w-[1280px] sm:rounded-3xl sm:border"
    >
      {open && (
        <div className="flex h-full flex-col">
          <header className="flex shrink-0 flex-wrap items-start gap-x-4 gap-y-3 px-5 pt-5 pb-4 sm:px-6">
            <div className="min-w-0 flex-1 space-y-1.5">
              <Eyebrow>Alerts</Eyebrow>
              <h2 className="display text-[22px]">What needs your eye</h2>
              <p className="hidden max-w-2xl text-xs text-muted md:block">
                Everything the guard, the scheduler and the daily checks told you, in four segments. Repeats of the same
                alert on the same day fold into one card. Read marks are kept in this browser.
              </p>
            </div>
            <button
              aria-label="Close"
              onClick={onClose}
              className="order-none -mr-2 rounded-full p-2 text-muted hover:bg-raised hover:text-ink sm:order-last"
            >
              <X className="size-4" />
            </button>
            <div className="flex w-full items-center gap-2 sm:w-auto">
              <Segmented
                value={filter}
                onChange={setFilter}
                options={[
                  { value: "all", label: "Everything" },
                  { value: "important", label: "Important" },
                ]}
              />
              <Button size="sm" variant="secondary" disabled={!feed} onClick={() => alerts.markRead()}>
                <CheckCheck className="size-4" /> Mark all read
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setChoosing(true)}>
                <FileText className="size-4" /> Monthly report
              </Button>
            </div>
          </header>

          <AiUse open={open} />

          <NowStrip
            onReview={() => {
              onClose();
              review();
            }}
          />

          <div className="min-h-0 flex-1 border-t border-line bg-page/50 pt-4 pb-4 sm:pb-5">
            {alerts.error ? (
              <div className="px-5 sm:px-6">
                <Note tone="bad">{alerts.error}</Note>
              </div>
            ) : !feed ? (
              <div className="px-5 sm:px-6">
                <Loading />
              </div>
            ) : (
              <div className="@container h-full">
                <div className="no-scrollbar flex h-full snap-x snap-mandatory scroll-px-5 gap-3 overflow-x-auto px-5 sm:scroll-px-6 sm:px-6">
                  {feed.segments.map((segment) => (
                    <SegmentColumn key={segment.id} segment={segment} filter={filter} onGo={go} />
                  ))}
                </div>
              </div>
            )}
          </div>
          {choosing && (
            <Suspense fallback={null}>
              <ReportDialog
                open={choosing}
                onClose={() => setChoosing(false)}
                onReady={(r) => {
                  setChoosing(false);
                  setReport(r);
                  onClose(); // the report opens over the app, where it can print
                }}
              />
            </Suspense>
          )}
        </div>
      )}
      {report && (
        <Suspense fallback={null}>
          <ReportView report={report} onClose={() => setReport(null)} />
        </Suspense>
      )}
    </dialog>
  );
}
