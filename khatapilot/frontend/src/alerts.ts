// The alerts feed and what this browser has marked read. Read marks are a convenience kept in
// localStorage: per segment, "read up to audit entry #n", plus single cards opened since.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useData } from "./hooks";
import type { AlertFeed, AlertItem, AlertSegmentId } from "./types";
import type { Tone } from "./ui";

interface ReadMarks {
  chain: string;
  upTo: Partial<Record<AlertSegmentId, number>>;
  seen: Record<string, number>; // card id -> the seq it had when opened
}

const KEY = "kp.alerts.read";
const EMPTY: ReadMarks = { chain: "", upTo: {}, seen: {} };

function loadMarks(): ReadMarks {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...EMPTY, ...(JSON.parse(raw) as ReadMarks) } : EMPTY;
  } catch {
    return EMPTY;
  }
}

function saveMarks(marks: ReadMarks) {
  try {
    localStorage.setItem(KEY, JSON.stringify(marks));
  } catch {
    /* private mode: marks last until the tab closes */
  }
}

export const SEGMENT_TONE: Record<AlertSegmentId, Tone> = {
  security: "bad",
  money: "info",
  cash: "warn",
  customers: "ok",
};

/** Red only for security (refusals and STOP); everything else urgent is amber. */
export function priorityTone(item: Pick<AlertItem, "priority" | "segment">): Tone {
  if (item.priority === "urgent") return item.segment === "security" ? "bad" : "warn";
  return item.priority === "important" ? "info" : "neutral";
}

export interface AlertsState {
  feed: AlertFeed | null;
  error: string | null;
  isUnread: (item: AlertItem) => boolean;
  /** Unread cards that are urgent or important: the number on the Alerts tab. */
  needsEye: number;
  markRead: (segment?: AlertSegmentId) => void;
  markSeen: (item: AlertItem) => void;
}

/**
 * Loads the feed whenever the guard records something (`version`), and calls `onNew` once for each
 * load that brings urgent or important cards newer than anything seen before (not on the first load).
 */
export function useAlerts(version: number, onNew: (items: AlertItem[]) => void): AlertsState {
  const { data: feed, error } = useData<AlertFeed>("/notifications", [version], 15000);
  const [marks, setMarks] = useState<ReadMarks>(loadMarks);
  const lastSeq = useRef<number | null>(null);
  const onNewRef = useRef(onNew);
  onNewRef.current = onNew;

  // A reloaded demo shop starts a new audit chain: old read marks would hide the new alerts.
  useEffect(() => {
    if (!feed) return;
    if (marks.chain !== feed.chain_id) {
      const fresh = { ...EMPTY, chain: feed.chain_id };
      setMarks(fresh);
      saveMarks(fresh);
      lastSeq.current = null;
    }
    const items = feed.segments.flatMap((s) => s.items);
    if (lastSeq.current !== null) {
      const since = lastSeq.current;
      const fresh = items.filter((i) => i.seq > since && i.priority !== "info").sort((a, b) => b.seq - a.seq);
      if (fresh.length) onNewRef.current(fresh);
    }
    lastSeq.current = feed.latest_seq;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed]);

  const update = useCallback((change: (m: ReadMarks) => ReadMarks) => {
    setMarks((m) => {
      const next = change(m);
      saveMarks(next);
      return next;
    });
  }, []);

  const isUnread = useCallback(
    (item: AlertItem) => item.seq > (marks.upTo[item.segment] ?? 0) && (marks.seen[item.id] ?? 0) < item.seq,
    [marks],
  );

  const markRead = useCallback(
    (segment?: AlertSegmentId) => {
      if (!feed) return;
      update((m) => {
        const upTo = { ...m.upTo };
        for (const s of feed.segments) {
          if (segment && s.id !== segment) continue;
          upTo[s.id] = Math.max(upTo[s.id] ?? 0, ...s.items.map((i) => i.seq));
        }
        return { ...m, upTo };
      });
    },
    [feed, update],
  );

  const markSeen = useCallback(
    (item: AlertItem) => update((m) => ({ ...m, seen: { ...m.seen, [item.id]: item.seq } })),
    [update],
  );

  const needsEye = useMemo(
    () => (feed?.segments ?? []).flatMap((s) => s.items).filter((i) => i.priority !== "info" && isUnread(i)).length,
    [feed, isUnread],
  );

  return useMemo(
    () => ({ feed, error, isUnread, needsEye, markRead, markSeen }),
    [feed, error, isUnread, needsEye, markRead, markSeen],
  );
}
