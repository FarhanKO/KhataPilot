import { History, MessageSquare, Trash2 } from "lucide-react";
import { useState } from "react";

import { api, errorText } from "../api";
import { dateTimeLabel, dayLabel, timeLabel } from "../format";
import { useData } from "../hooks";
import { useApp } from "../state";
import type { ConversationSummary } from "../types";
import { Badge, Button, Empty, Loading, Modal, Note } from "../ui";

/** "Mon 12 Oct 10:04", or "Mon 12 Oct 10:04–10:31" when the chat went on for a while that day. */
function when(c: ConversationSummary): string {
  const start = dateTimeLabel(c.started_at);
  const sameDay = dayLabel(c.started_at) === dayLabel(c.last_at);
  const end = timeLabel(c.last_at);
  return sameDay && end !== timeLabel(c.started_at) ? `${start}–${end}` : start;
}

/** The "Past chats" button and its window: earlier conversations to open again or delete. */
export function PastChats() {
  const { conversationId, openConversation, newConversation, toast, sending } = useApp();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const list = useData<ConversationSummary[]>(open ? "/chat/conversations" : null, [open]);

  async function load(id: string) {
    setOpen(false); // close first: the chat appears behind it as soon as it loads
    try {
      await openConversation(id);
    } catch (e) {
      toast(errorText(e), "bad");
    }
  }

  async function remove(id: string) {
    setBusy(id);
    try {
      await api.del(`/chat/conversations/${encodeURIComponent(id)}`);
      list.setData((rows) => (rows ?? []).filter((c) => c.id !== id));
      if (id === conversationId) newConversation();
      toast("Chat deleted. Its payments and decisions stay in the Audit log.", "info");
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setBusy(null);
      setConfirming(null);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setConfirming(null);
          setOpen(true);
        }}
        className="label inline-flex items-center gap-1.5 rounded-full border border-line bg-surface/90 px-3 py-1.5 text-[10.5px] text-muted shadow-sm backdrop-blur transition hover:border-ink hover:text-ink"
      >
        <History className="size-3.5" />
        Past chats
      </button>

      <Modal open={open} onClose={() => setOpen(false)} eyebrow="Your conversations" title="Past chats" wide>
        <div className="max-h-[60vh] overflow-y-auto px-1">
          {list.error ? (
            <Note tone="bad">{list.error}</Note>
          ) : !list.data ? (
            <Loading />
          ) : list.data.length === 0 ? (
            <Empty>No chats yet. What you ask here is kept so you can come back to it.</Empty>
          ) : (
            <ul className="divide-y divide-line">
              {list.data.map((c) => {
                const current = c.id === conversationId;
                return (
                  <li key={c.id} className="flex items-center gap-3 py-3">
                    <MessageSquare className="size-4 shrink-0 text-faint" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-ink">{c.title || "(forwarded items)"}</div>
                      <div className="label mt-0.5 text-[10.5px] text-faint">
                        {when(c)} · {c.messages} message{c.messages === 1 ? "" : "s"}
                      </div>
                    </div>
                    {confirming === c.id ? (
                      <div className="flex shrink-0 items-center gap-1.5">
                        <span className="hidden text-xs text-muted sm:inline">Delete this chat?</span>
                        <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>
                          Keep
                        </Button>
                        <Button size="sm" variant="danger" busy={busy === c.id} onClick={() => void remove(c.id)}>
                          Delete
                        </Button>
                      </div>
                    ) : (
                      <div className="flex shrink-0 items-center gap-1.5">
                        {current ? (
                          <Badge tone="ok">Open now</Badge>
                        ) : (
                          <Button size="sm" disabled={sending} onClick={() => void load(c.id)}>
                            Open
                          </Button>
                        )}
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={`Delete the chat "${c.title}"`}
                          title="Delete"
                          onClick={() => setConfirming(c.id)}
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <p className="mt-3 px-1 text-xs text-faint">
          Deleting a chat removes the conversation only. Every payment, approval and guard decision stays in the Audit log.
        </p>
      </Modal>
    </>
  );
}
