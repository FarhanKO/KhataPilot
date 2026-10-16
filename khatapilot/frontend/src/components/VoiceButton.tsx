// The mic in the message box. Tapped, it grows a little and turns into a pause button that breathes with
// the owner's voice, and a small waveform with a timer slides out beside it. The waveform's motion is
// adapted from kokonutui's "AI Voice" (MIT, https://kokonutui.com, @kokonutui): the newest sound enters
// on the right and the row slides left a fraction of a bar at a time, with fading edges.

import { Mic, Pause } from "lucide-react";
import { useEffect, useRef, useState, type RefObject } from "react";

const BAR_COUNT = 36;
const BAR_IDS = Array.from({ length: BAR_COUNT + 1 }, (_, i) => i); // one waits off the right edge
const BAR_PITCH = 4; // bar width + gap, px
const SAMPLE_MS = 70; // a new sample enters on the right this often
const REST_LEVEL = 0.14;
const STILL_LEVELS = BAR_IDS.map((i) => 0.25 + 0.5 * Math.abs(Math.sin(i * 0.55) * Math.cos(i * 0.21)));
const EDGE_MASK = "linear-gradient(to right, transparent, black 25%, black 92%, transparent)";

const FACE = "absolute transition-[opacity,filter,scale] duration-200 ease-[cubic-bezier(0.23,1,0.32,1)] motion-reduce:transition-opacity";
const face = (visible: boolean) => (visible ? "scale-100 opacity-100 blur-0" : "scale-50 opacity-0 blur-[3px]");

const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

export function VoiceButton({ listening, level, language, onStart, onStop }: {
  listening: boolean;
  level: RefObject<number>;
  language: string;
  onStart: () => void;
  onStop: () => void;
}) {
  const [seconds, setSeconds] = useState(0);
  const rowRef = useRef<HTMLDivElement>(null);
  const ringRef = useRef<HTMLSpanElement>(null);
  const bars = useRef<(HTMLSpanElement | null)[]>([]);

  // One animation loop writes transforms straight to the bars and the ring, so a frame never re-renders React.
  useEffect(() => {
    const row = rowRef.current;
    const ring = ringRef.current;
    if (!(listening && row && ring)) return;
    const setScales = (levels: readonly number[]) =>
      bars.current.forEach((bar, i) => {
        if (bar) bar.style.transform = `scaleY(${(levels[i] ?? REST_LEVEL).toFixed(3)})`;
      });
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      setScales(STILL_LEVELS);
      return;
    }
    const samples = BAR_IDS.map(() => REST_LEVEL);
    let phase = 0;
    let smooth = 0;
    let last = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const dt = Math.min(now - last, 100); // a backgrounded tab does not fast-forward on return
      last = now;
      phase += dt / SAMPLE_MS;
      while (phase >= 1) {
        phase -= 1;
        samples.shift();
        samples.push(Math.max(REST_LEVEL, Math.min(1, level.current ?? 0)));
      }
      row.style.transform = `translateX(${(-phase * BAR_PITCH).toFixed(2)}px)`;
      setScales(samples);
      smooth += ((samples.at(-1) ?? REST_LEVEL) - smooth) * 0.18; // the ring breathes with the voice
      const lift = Math.max(0, smooth - REST_LEVEL);
      ring.style.transform = `scale(${(1 + lift * 0.35).toFixed(3)})`;
      ring.style.opacity = (0.5 + lift * 0.8).toFixed(3);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      row.style.transform = "translateX(0px)";
      setScales(BAR_IDS.map(() => REST_LEVEL));
      ring.style.transform = "scale(1)";
      ring.style.opacity = "0";
    };
  }, [listening, level]);

  useEffect(() => {
    if (!listening) return;
    setSeconds(0);
    const startedAt = Date.now();
    const id = setInterval(() => setSeconds(Math.floor((Date.now() - startedAt) / 1000)), 250);
    return () => clearInterval(id);
  }, [listening]);

  const label = listening ? "Pause listening" : "Talk instead of typing";
  return (
    <div className="flex items-center gap-1.5">
      {/* The waveform and timer slide out beside the mic while listening. */}
      <div
        aria-hidden
        className={`flex items-center gap-2 overflow-hidden transition-[max-width,opacity] duration-300 ease-[cubic-bezier(0.23,1,0.32,1)] ${
          listening ? "max-w-48 opacity-100" : "max-w-0 opacity-0"
        }`}
      >
        <div className="h-6 w-20 shrink-0 overflow-hidden sm:w-32" style={{ maskImage: EDGE_MASK, WebkitMaskImage: EDGE_MASK }}>
          <div ref={rowRef} className="flex h-full items-center justify-end gap-px">
            {BAR_IDS.map((i) => (
              <span
                key={i}
                ref={(el) => {
                  bars.current[i] = el;
                }}
                className="h-full w-[3px] shrink-0 rounded-full bg-ink/55"
                style={{ transform: `scaleY(${REST_LEVEL})` }}
              />
            ))}
          </div>
        </div>
        <span className="w-8 shrink-0 font-mono text-[11px] text-muted tabular-nums">{clock(seconds)}</span>
      </div>

      <div className="relative">
        <span aria-hidden ref={ringRef} className="pointer-events-none absolute inset-0 rounded-full bg-ink/10 opacity-0" />
        <button
          type="button"
          aria-label={label}
          aria-pressed={listening}
          title={listening ? `Listening in ${language}. Tap to pause.` : label}
          onClick={listening ? onStop : onStart}
          className={`relative flex size-9 items-center justify-center rounded-full transition-[scale,background-color,color] duration-200 ease-[cubic-bezier(0.23,1,0.32,1)] ${
            listening ? "scale-110 bg-ink/10 text-ink" : "text-muted hover:bg-raised hover:text-ink"
          }`}
        >
          <Mic aria-hidden className={`${FACE} size-4 ${face(!listening)}`} />
          <Pause aria-hidden className={`${FACE} size-4 fill-current ${face(listening)}`} />
        </button>
      </div>
      <output aria-live="polite" className="sr-only">
        {listening ? `Listening in ${language}` : ""}
      </output>
    </div>
  );
}
