import { Bell, Moon, OctagonX, Sun } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, errorText } from "./api";
import { ApprovalDialog } from "./components/ApprovalDialog";
import { LogoMark } from "./components/Logo";
import { AttackSimulator } from "./components/AttackSimulator";
import { ProviderStrip } from "./components/ProviderStrip";
import { SimControls } from "./components/SimControls";
import { StopButton } from "./components/StopButton";
import { TabScroller } from "./components/TabScroller";
import { TracePanel } from "./components/TracePanel";
import { dateTimeLabel } from "./format";
import { useData, useTrace } from "./hooks";
import { AppContext, type AppContextValue, type ChatMessage, type Forwarded, type TabId } from "./state";
import { AuditTab } from "./tabs/AuditTab";
import { ChatTab } from "./tabs/ChatTab";
import { ControlsTab } from "./tabs/ControlsTab";
import { CreditTab } from "./tabs/CreditTab";
import { ForecastTab } from "./tabs/ForecastTab";
import { LedgerTab } from "./tabs/LedgerTab";
import { LoansTab } from "./tabs/LoansTab";
import { SandboxTab } from "./tabs/SandboxTab";
import type { ChatResponse, OwnerStatus, Pending, Shop, ShownMessage } from "./types";
import { Marker, type Tone } from "./ui";

const TABS: { id: TabId; label: string; narrowOnly?: boolean }[] = [
  { id: "chat", label: "Chat" },
  { id: "trace", label: "Trace", narrowOnly: true },
  { id: "ledger", label: "Ledger" },
  { id: "forecast", label: "Forecast" },
  { id: "credit", label: "Credit" },
  { id: "loans", label: "Loans" },
  { id: "controls", label: "Limits" },
  { id: "audit", label: "Audit" },
  { id: "sandbox", label: "Sandbox" },
];

// The open tab lives in the URL hash, so a refresh keeps it and Back returns to the previous one.
function tabFromHash(): TabId {
  const id = window.location.hash.slice(1);
  return TABS.some((t) => t.id === id) ? (id as TabId) : "chat";
}

let messageSeq = 0;
const nextId = () => `m${++messageSeq}`;

function newConversationId(): string {
  return `web-${Date.now().toString(36)}`;
}

function storedConversation(): string {
  try {
    const existing = sessionStorage.getItem("kp.conversation");
    if (existing) return existing;
    const fresh = newConversationId();
    sessionStorage.setItem("kp.conversation", fresh);
    return fresh;
  } catch {
    return newConversationId();
  }
}

type Theme = "light" | "dark";

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme === "dark" ? "dark" : "light"));
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("kp.theme", theme);
    } catch {
      /* private mode */
    }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

const TOAST_LABEL: Partial<Record<Tone, string>> = { bad: "Refused", ok: "Done", warn: "Heads up" };

export default function App() {
  const [tab, setTabState] = useState<TabId>(tabFromHash);
  const [version, setVersion] = useState(0);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [reviewing, setReviewing] = useState<{ open: boolean; id?: string }>({ open: false });
  const [toasts, setToasts] = useState<{ id: number; text: string; tone: Tone }[]>([]);
  const [theme, toggleTheme] = useTheme();
  const conversation = useRef(storedConversation());
  const [conversationId, setConversationId] = useState(conversation.current);
  const switchConversation = useCallback((id: string) => {
    conversation.current = id;
    setConversationId(id);
    try {
      sessionStorage.setItem("kp.conversation", id);
    } catch {
      /* private mode: the id simply lives in memory */
    }
  }, []);

  useEffect(() => {
    const follow = () => setTabState(tabFromHash());
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
  }, []);
  const setTab = useCallback((next: TabId) => {
    if (window.location.hash.slice(1) !== next) window.location.hash = next;
    setTabState(next);
  }, []);

  // Re-read now and then: it carries the simulated clock, and a backend that was still starting gets picked up.
  const shop = useData<Shop>("/shop", [version], 15000);
  const status = useData<OwnerStatus>("/owner/status", [version], 5000);
  const pending = useData<Pending[]>("/approvals", [version], 4000);

  // Anything the guard records (a payment, an approval, STOP) refreshes the screens, at most twice a second.
  const bumpTimer = useRef<number | null>(null);
  const refresh = useCallback(() => {
    if (bumpTimer.current !== null) return;
    bumpTimer.current = window.setTimeout(() => {
      bumpTimer.current = null;
      setVersion((v) => v + 1);
    }, 400);
  }, []);
  const trace = useTrace((event) => {
    if (event.type === "audit") refresh();
    if (event.type === "reset") {
      // The demo shop was reloaded (scripts/demo_run.py): a fresh conversation and fresh numbers.
      setMessages([]);
      switchConversation(newConversationId());
      refresh();
    }
  });

  const toast = useCallback((text: string, tone: Tone = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5000);
  }, []);

  const sendChat = useCallback(
    async (message: string, forwarded: Forwarded[] = []): Promise<ChatResponse | null> => {
      setMessages((m) => [...m, { id: nextId(), role: "owner", text: message, forwarded, at: new Date().toISOString() }]);
      setSending(true);
      try {
        const response = await api.post<ChatResponse>("/chat", {
          conversation_id: conversation.current,
          message,
          forwarded,
        });
        setMessages((m) => [
          ...m,
          {
            id: nextId(),
            role: "assistant",
            text: response.reply,
            outcomes: response.outcomes,
            pending: response.pending,
            requestId: response.request_id,
            at: new Date().toISOString(),
          },
        ]);
        if (response.pending.length) setReviewing({ open: true, id: response.pending[0].id });
        refresh();
        return response;
      } catch (e) {
        setMessages((m) => [...m, { id: nextId(), role: "system", text: errorText(e), at: new Date().toISOString() }]);
        return null;
      } finally {
        setSending(false);
      }
    },
    [refresh],
  );

  const sendInbound = useCallback(
    async (fromNumber: string, text: string, channel: "sms_gateway" | "messenger" = "sms_gateway") => {
      const masked = `${fromNumber.slice(0, 3)}XXXX${fromNumber.slice(-4)}`;
      setMessages((m) => [...m, { id: nextId(), role: "inbound", from: masked, text, at: new Date().toISOString() }]);
      setSending(true);
      try {
        const response = await api.post<ChatResponse>("/sim/inbound", { from_number: fromNumber, text, channel });
        setMessages((m) => [
          ...m,
          {
            id: nextId(),
            role: "assistant",
            text: response.reply,
            outcomes: response.outcomes,
            requestId: response.request_id,
            at: new Date().toISOString(),
          },
        ]);
        refresh();
      } catch (e) {
        setMessages((m) => [...m, { id: nextId(), role: "system", text: errorText(e), at: new Date().toISOString() }]);
      } finally {
        setSending(false);
      }
    },
    [refresh],
  );

  const newConversation = useCallback(() => {
    switchConversation(newConversationId());
    setMessages([]);
  }, [switchConversation]);

  /** Show an earlier chat as it was, and carry on in it (the model keeps its history). */
  const openConversation = useCallback(
    async (id: string) => {
      const shown = await api.get<ShownMessage[]>(`/chat/conversations/${encodeURIComponent(id)}`);
      switchConversation(id);
      setMessages(
        shown.map((m) => ({
          id: nextId(),
          role: m.role === "user" ? "owner" : "assistant",
          text: m.text,
          forwarded: m.forwarded,
          outcomes: m.outcomes,
          requestId: m.request_id ?? undefined,
          at: m.at,
        })),
      );
    },
    [switchConversation],
  );

  // A reload keeps the open chat on screen, not just in the model's memory.
  useEffect(() => {
    openConversation(conversation.current).catch(() => {
      /* the backend is not up yet; the chat starts empty */
    });
  }, [openConversation]);

  const value: AppContextValue = useMemo(
    () => ({
      shop: shop.data,
      status: status.data,
      pending: pending.data ?? [],
      version,
      refresh,
      tab,
      setTab,
      messages,
      sending,
      sendChat,
      sendInbound,
      newConversation,
      conversationId,
      openConversation,
      review: (id?: string) => setReviewing({ open: true, id }),
      toast,
    }),
    [shop.data, status.data, pending.data, version, refresh, tab, setTab, messages, sending, sendChat, sendInbound,
      newConversation, conversationId, openConversation, toast],
  );

  // Keep status and approvals fresh right after the version changes (they also poll on their own).
  useEffect(() => {
    void status.reload();
    void pending.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);

  const waiting = pending.data?.length ?? 0;
  const frozen = status.data?.kill_switch.on ?? false;

  return (
    <AppContext.Provider value={value}>
      <div className="flex h-full flex-col">
        <header className="bg-bar text-bar-ink">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-3 px-4 py-3 sm:px-5">
            <div className="flex min-w-0 flex-1 basis-0 items-center gap-3 xl:flex-none xl:basis-auto">
              <div className="grid size-9 shrink-0 place-items-center rounded-[10px] bg-bar-ink text-bar" aria-hidden>
                <LogoMark className="size-7" />
              </div>
              <div className="min-w-0 leading-tight">
                <div className="text-[17px] font-medium tracking-[-0.02em]">KhataPilot</div>
                {shop.data && (
                  <div className="truncate text-xs text-bar-muted">{`${shop.data.name} · ${shop.data.area}`}</div>
                )}
              </div>
            </div>

            <TabScroller
              label="Sections"
              activeKey={tab}
              className="order-last -mx-4 w-[calc(100%+2rem)] sm:-mx-5 sm:w-[calc(100%+2.5rem)] xl:order-none xl:mx-0 xl:w-auto xl:flex-1"
              innerClassName="gap-1.5 px-4 sm:px-5 xl:justify-center-safe xl:px-0"
            >
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  aria-current={tab === t.id ? "page" : undefined}
                  className={`shrink-0 rounded-full border px-3.5 py-1.5 text-[13px] transition ${t.narrowOnly ? "lg:hidden" : ""} ${
                    tab === t.id
                      ? "border-bar-ink bg-bar-ink font-medium text-bar"
                      : "border-bar-line text-bar-ink/80 hover:border-bar-muted hover:text-bar-ink"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </TabScroller>

            <div className="ml-auto flex shrink-0 items-center gap-2">
              <button
                onClick={toggleTheme}
                aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
                title={theme === "dark" ? "Light mode" : "Dark mode"}
                className="grid size-9 place-items-center rounded-full border border-bar-line text-bar-muted transition hover:border-bar-muted hover:text-bar-ink"
              >
                {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
              </button>
              <button
                aria-label={waiting ? `${waiting} waiting for your approval` : "Approvals"}
                onClick={() => setReviewing({ open: true })}
                className={`inline-flex h-9 items-center gap-2 rounded-full border px-3.5 text-[13px] font-medium transition ${
                  waiting
                    ? "border-bar-ink bg-bar-ink text-bar hover:opacity-90"
                    : "border-bar-line text-bar-ink/80 hover:border-bar-muted hover:text-bar-ink"
                }`}
              >
                {waiting ? (
                  <span className="relative grid size-2 place-items-center">
                    <span className="absolute size-2 animate-ping rounded-full bg-warn opacity-70" />
                    <span className="size-2 rounded-full bg-warn" />
                  </span>
                ) : (
                  <Bell className="size-4" />
                )}
                <span className="hidden sm:inline">{waiting ? `${waiting} waiting for you` : "Approvals"}</span>
                {waiting > 0 && <span className="sm:hidden">{waiting}</span>}
              </button>
              <StopButton frozen={frozen} />
            </div>
          </div>
        </header>

        <div className="no-scrollbar flex items-center gap-x-5 overflow-x-auto border-b border-line px-4 py-2 whitespace-nowrap sm:px-5">
          {shop.data && (
            <span className="label hidden text-muted sm:inline">
              Clock <span className="text-ink">{dateTimeLabel(shop.data.now)}</span>
            </span>
          )}
          <SimControls />
          <span className="ml-auto hidden items-center gap-5 md:flex">
            {shop.data && (
              <span className="label text-muted">
                LLM <span className="text-ink">{shop.data.llm_mode === "mock" ? "mock (offline)" : shop.data.model}</span>
              </span>
            )}
            <span className={`label inline-flex items-center gap-2 ${trace.connected ? "text-ok" : "text-warn"}`}>
              <span className={`size-1.5 rounded-full ${trace.connected ? "animate-pulse bg-ok" : "bg-warn"}`} />
              {trace.connected ? "Guard live" : "Reconnecting"}
            </span>
          </span>
        </div>

        {frozen && (
          <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 bg-stop px-4 py-2 text-center text-sm text-white">
            <span className="label inline-flex items-center gap-1.5 font-semibold">
              <OctagonX className="size-4" /> STOP is on
            </span>
            <span>Every payment, message and loan application is frozen, here and at the provider. Resume with your PIN.</span>
          </div>
        )}

        <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_400px] xl:grid-cols-[minmax(0,1fr)_440px]">
          <main className="thin-scrollbar min-h-0 overflow-y-auto">
            {tab === "chat" && <ChatTab />}
            {tab === "trace" && (
              <div className="space-y-6 px-4 py-6 lg:hidden">
                <ProviderStrip />
                <AttackSimulator />
                <TracePanel events={trace.events} connected={trace.connected} />
              </div>
            )}
            {tab === "ledger" && <LedgerTab />}
            {tab === "forecast" && <ForecastTab />}
            {tab === "credit" && <CreditTab />}
            {tab === "loans" && <LoansTab />}
            {tab === "controls" && <ControlsTab />}
            {tab === "audit" && <AuditTab />}
            {tab === "sandbox" && <SandboxTab />}
          </main>
          <aside className="hidden min-h-0 flex-col border-l border-line bg-raised/45 lg:flex">
            <div className="border-b border-line px-4 pt-4 pb-3">
              <ProviderStrip />
            </div>
            <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto px-4 pt-5 pb-4">
              <TracePanel events={trace.events} connected={trace.connected} />
            </div>
            <div className="border-t border-line bg-page px-4 py-4">
              <AttackSimulator compact />
            </div>
          </aside>
        </div>

        <ApprovalDialog
          open={reviewing.open}
          focusId={reviewing.id}
          pending={pending.data ?? []}
          onClose={() => setReviewing({ open: false })}
          onDone={() => {
            void pending.reload();
            refresh();
          }}
        />

        <div className="pointer-events-none fixed bottom-5 left-1/2 z-50 flex w-[min(92vw,30rem)] -translate-x-1/2 flex-col gap-2">
          {toasts.map((t) => (
            <div
              key={t.id}
              role="status"
              className="flash-in pointer-events-auto rounded-2xl bg-bar px-4 py-3 text-sm text-bar-ink shadow-[0_18px_40px_-12px_rgba(20,15,10,0.5)]"
            >
              <div className="label mb-1 flex items-center gap-2 text-bar-muted">
                <Marker tone={t.tone === "neutral" || t.tone === "brand" ? "info" : t.tone} />
                {TOAST_LABEL[t.tone] ?? "Note"}
              </div>
              {t.text}
            </div>
          ))}
        </div>
      </div>
    </AppContext.Provider>
  );
}
