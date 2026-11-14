import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { api, errorText } from "./api";
import type { TraceEvent } from "./types";

// While the live trace stream is connected, every change the guard records reaches the app as an event and
// refreshes the screens, so polling is only a safety net. On a cheap phone, every poll costs battery and data.
export const LIVE_POLL_MS = 30_000;

let streamLive = false;
const liveListeners = new Set<() => void>();
function setStreamLive(live: boolean) {
  if (live === streamLive) return;
  streamLive = live;
  liveListeners.forEach((listener) => listener());
}
function subscribeLive(listener: () => void) {
  liveListeners.add(listener);
  return () => liveListeners.delete(listener);
}
/** Whether the live trace stream is connected (shared by every component). */
export const useStreamLive = () => useSyncExternalStore(subscribeLive, () => streamLive);

function subscribeVisible(listener: () => void) {
  document.addEventListener("visibilitychange", listener);
  return () => document.removeEventListener("visibilitychange", listener);
}
/** False while the app is in the background or the phone's screen is off. */
export const usePageVisible = () => useSyncExternalStore(subscribeVisible, () => document.visibilityState !== "hidden");

/** How often to poll: `intervalMs` while the live stream is down, at least LIVE_POLL_MS while it is up,
 * and never while the app is hidden. */
function pollEvery(intervalMs: number | undefined, live: boolean, visible: boolean): number | undefined {
  if (!intervalMs || !visible) return undefined;
  return live ? Math.max(intervalMs, LIVE_POLL_MS) : intervalMs;
}

/** GET `path` now, again whenever `deps` change, and every `intervalMs` if given (see pollEvery: slower while
 * the live stream is connected, paused while the app is hidden, with a fresh read when it comes back).
 * `null` skips loading. */
export function useData<T>(path: string | null, deps: unknown[] = [], intervalMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const latest = useRef(path);
  latest.current = path;

  const load = useCallback(async () => {
    if (path === null) return;
    try {
      const result = await api.get<T>(path);
      if (latest.current === path) {
        setData(result);
        setError(null);
      }
    } catch (e) {
      if (latest.current === path) setError(errorText(e));
    } finally {
      if (latest.current === path) setLoading(false);
    }
  }, [path]);

  const live = useStreamLive();
  const visible = usePageVisible();
  const every = pollEvery(intervalMs, live, visible);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, ...deps]);

  // A polled read comes back up to date when the app returns to the screen.
  const wasVisible = useRef(visible);
  useEffect(() => {
    if (intervalMs && visible && !wasVisible.current) void load();
    wasVisible.current = visible;
  }, [visible, intervalMs, load]);

  useEffect(() => {
    if (!every) return;
    const timer = setInterval(() => void load(), every);
    return () => clearInterval(timer);
  }, [load, every]);

  return { data, error, loading, reload: load, setData };
}

/**
 * The live decision trace over server-sent events. Each connection replays recent history, then
 * streams; on reconnect (or a backend restart) the history replaces what we had.
 */
export function useTrace(onLive?: (event: TraceEvent) => void) {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const onLiveRef = useRef(onLive);
  onLiveRef.current = onLive;

  useEffect(() => {
    let stopped = false;
    let controller: AbortController | null = null;

    async function connect() {
      let delay = 500;
      while (!stopped) {
        controller = new AbortController();
        try {
          const response = await fetch("/api/trace/stream?replay=200", {
            signal: controller.signal,
            headers: { Accept: "text/event-stream" },
          });
          if (!response.ok || !response.body) throw new Error(`trace stream: ${response.status}`);
          const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
          const seen = new Set<number>();
          let replay: TraceEvent[] | null = [];
          let buffer = "";
          setConnected(true);
          setStreamLive(true);
          delay = 500;
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += value;
            let cut: number;
            while ((cut = buffer.indexOf("\n\n")) >= 0) {
              const chunk = buffer.slice(0, cut);
              buffer = buffer.slice(cut + 2);
              if (chunk.startsWith(":")) {
                if (chunk.startsWith(": live") && replay) {
                  const history = replay;
                  setEvents(history);
                  replay = null;
                }
                continue;
              }
              const data = chunk
                .split("\n")
                .filter((line) => line.startsWith("data: "))
                .map((line) => line.slice(6))
                .join("\n");
              if (!data) continue;
              const event = JSON.parse(data) as TraceEvent;
              if (seen.has(event.trace_id)) continue;
              seen.add(event.trace_id);
              if (event.type === "reset") {
                // The demo shop was reloaded: start the panel afresh.
                if (replay) replay = [];
                else setEvents([]);
                onLiveRef.current?.(event);
              } else if (replay) {
                replay.push(event);
              } else {
                setEvents((previous) => [...previous, event].slice(-800));
                onLiveRef.current?.(event);
              }
            }
          }
        } catch {
          if (stopped) return;
        }
        setConnected(false);
        setStreamLive(false);
        await new Promise((resolve) => setTimeout(resolve, delay));
        delay = Math.min(delay * 2, 8000);
      }
    }

    void connect();
    return () => {
      stopped = true;
      setStreamLive(false);
      controller?.abort();
    };
  }, []);

  return { events, connected };
}
