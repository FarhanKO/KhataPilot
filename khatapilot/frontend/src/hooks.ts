import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorText } from "./api";
import type { TraceEvent } from "./types";

/** GET `path` now, again whenever `deps` change, and every `intervalMs` if given. `null` skips loading. */
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

  useEffect(() => {
    void load();
    if (!intervalMs) return;
    const timer = setInterval(() => void load(), intervalMs);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, intervalMs, ...deps]);

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
        await new Promise((resolve) => setTimeout(resolve, delay));
        delay = Math.min(delay * 2, 8000);
      }
    }

    void connect();
    return () => {
      stopped = true;
      controller?.abort();
    };
  }, []);

  return { events, connected };
}
