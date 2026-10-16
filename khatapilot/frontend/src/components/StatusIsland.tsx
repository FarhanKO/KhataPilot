/**
 * Status Island: a pill that reports background work, reshapes as tasks change and opens into a list
 * of everything in flight. Adapted from KokonutUI (https://kokonutui.com/docs/navigation/status-island,
 * by @dorianbaffier, MIT licence) to the app's colours; the demo playground is left out.
 */

import { AnimatePresence, MotionConfig, motion, type Transition } from "motion/react";
import {
  type FocusEvent,
  type KeyboardEvent,
  type PointerEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

export type IslandTaskStatus = "running" | "done" | "error";

export interface IslandTask {
  id: string;
  /** What is happening, e.g. "Reading 12 invoice photos". */
  label: string;
  /** Optional second part, shown dimmed after the label. */
  detail?: string;
  /** A longer explanation, e.g. why a photo could not be read. Shown under the task when the island is open. */
  note?: string;
  status: IslandTaskStatus;
  /** 0 to 1 while running. Leave it out for work without a known length. */
  progress?: number;
  /** A follow-up, like Retry. */
  action?: { label: string; onClick: () => void };
}

const MORPH: Transition = { type: "spring", duration: 0.45, bounce: 0.18 };
const SWAP: Transition = { duration: 0.22, ease: [0.23, 1, 0.32, 1] };
const EASE_OUT = "cubic-bezier(0.23, 1, 0.32, 1)";
const MAX_PEEK = 2; // tasks that peek out behind the island when it is closed

/** A number whose digits roll to their new value. */
function RollingNumber({ value }: { value: number }) {
  const text = String(value);
  return (
    <span aria-hidden="true" className="inline-flex tabular-nums">
      {text.split("").map((char, i) => (
        // Keyed from the right, so the ones stay the ones when a digit is added.
        <span className="relative inline-block h-4 overflow-hidden" key={text.length - i}>
          <span
            className="flex flex-col transition-transform duration-200 motion-reduce:transition-none"
            style={{ transform: `translateY(${-Number(char) * 10}%)`, transitionTimingFunction: EASE_OUT }}
          >
            {"0123456789".split("").map((d) => (
              <span className="h-4 leading-4" key={d}>
                {d}
              </span>
            ))}
          </span>
        </span>
      ))}
    </span>
  );
}

function TaskIcon({ task }: { task: IslandTask }) {
  if (task.status === "done") {
    return (
      <svg aria-hidden="true" className="size-4 shrink-0 text-emerald-400" fill="none" viewBox="0 0 16 16">
        <circle cx="8" cy="8" fill="currentColor" opacity="0.18" r="8" />
        <motion.path
          animate={{ pathLength: 1 }}
          d="M4.75 8.25 7 10.5l4.25-4.75"
          initial={{ pathLength: 0 }}
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.75"
          transition={{ duration: 0.3, ease: [0.23, 1, 0.32, 1], delay: 0.08 }}
        />
      </svg>
    );
  }
  if (task.status === "error") {
    return (
      <svg aria-hidden="true" className="size-4 shrink-0 text-red-400" fill="none" viewBox="0 0 16 16">
        <circle cx="8" cy="8" fill="currentColor" opacity="0.18" r="8" />
        <path d="M8 4.5v4" stroke="currentColor" strokeLinecap="round" strokeWidth="1.75" />
        <circle cx="8" cy="11.25" fill="currentColor" r="1" />
      </svg>
    );
  }
  const known = typeof task.progress === "number";
  const circumference = 2 * Math.PI * 6;
  const progress = known ? Math.min(1, Math.max(0, task.progress ?? 0)) : 0.25;
  return (
    <svg
      aria-hidden="true"
      className={`size-4 shrink-0 -rotate-90 ${known ? "" : "animate-spin motion-reduce:animate-none"}`}
      fill="none"
      viewBox="0 0 16 16"
    >
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeOpacity="0.18" strokeWidth="2" />
      <circle
        cx="8"
        cy="8"
        r="6"
        stroke="currentColor"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - progress)}
        strokeLinecap="round"
        strokeWidth="2"
        style={{ transition: `stroke-dashoffset 250ms ${EASE_OUT}` }}
      />
    </svg>
  );
}

function TaskTrailing({ task }: { task: IslandTask }) {
  if (task.action) {
    const { label, onClick } = task.action;
    return (
      <button
        className="-my-1 shrink-0 rounded-full bg-white/12 px-2.5 py-1 text-xs font-medium text-bar-ink transition-colors duration-150 hover:bg-white/20 focus-visible:outline-2 focus-visible:outline-white/60"
        onClick={(event) => {
          event.stopPropagation();
          onClick();
        }}
        type="button"
      >
        {label}
      </button>
    );
  }
  if (task.status === "running" && typeof task.progress === "number") {
    const percent = Math.round(Math.min(1, Math.max(0, task.progress)) * 100);
    return (
      <span className="flex shrink-0 items-center text-xs text-bar-muted">
        <RollingNumber value={percent} />%
      </span>
    );
  }
  return null;
}

function TaskRow({ task }: { task: IslandTask }) {
  return (
    <>
      <div className="flex min-w-0 items-center gap-2.5">
        <TaskIcon task={task} />
        <span className="min-w-0 flex-1 truncate text-[13px] leading-4">
          {task.label}
          {task.detail ? <span className="text-bar-muted"> · {task.detail}</span> : null}
        </span>
        <TaskTrailing task={task} />
      </div>
      {task.note ? <p className="mt-1.5 pl-6.5 text-xs leading-snug text-bar-muted">{task.note}</p> : null}
    </>
  );
}

/** The trailing slot inside the closed pill. The pill itself is a button, so the action shows as a
 * label here and becomes a real button in the open list. */
function TaskTrailingInert({ task }: { task: IslandTask }) {
  if (task.action) {
    return <span className="-my-1 inline-block rounded-full bg-white/12 px-2.5 py-1 text-xs font-medium">{task.action.label}</span>;
  }
  return <TaskTrailing task={task} />;
}

function describe(task: IslandTask) {
  return [task.label, task.detail, task.note].filter(Boolean).join(", ");
}

/** A pill that reports background work. It opens into a list of everything in flight on hover, focus or tap.
 * Finished tasks (done or failed) leave after `dismissAfter`, or `hurryAfter` once `hurry` changes. */
export function StatusIsland({
  tasks,
  onDismiss,
  dismissAfter = 5000,
  hurry = 0,
  hurryAfter = 3000,
  className = "",
}: {
  /** Oldest first; the newest task is the one the island shows. */
  tasks: IslandTask[];
  onDismiss?: (id: string) => void;
  dismissAfter?: number;
  /** Bump it (e.g. when the owner goes back to the message box) and finished tasks leave within `hurryAfter`. */
  hurry?: number;
  hurryAfter?: number;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const doneAt = useRef(new Map<string, number>());
  const lastStatus = useRef(new Map<string, IslandTaskStatus>());
  const current = tasks.at(-1);
  const behind = Math.min(tasks.length - 1, MAX_PEEK);
  const expanded = open && tasks.length > 0;
  const islandRef = useRef<HTMLDivElement>(null);

  // A tap anywhere else closes the list on touch screens.
  useEffect(() => {
    if (!expanded) return;
    const close = (event: globalThis.PointerEvent) => {
      if (!islandRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [expanded]);

  // Announce new tasks and status changes, not every progress tick.
  useEffect(() => {
    for (const task of tasks) {
      if (lastStatus.current.get(task.id) !== task.status) {
        lastStatus.current.set(task.id, task.status);
        setAnnouncement(describe(task));
      }
    }
    const ids = new Set(tasks.map((task) => task.id));
    for (const id of lastStatus.current.keys()) {
      if (!ids.has(id)) {
        lastStatus.current.delete(id);
        doneAt.current.delete(id);
      }
    }
  }, [tasks]);

  // Moving the clocks back here lets the effect below reschedule with less time left.
  useEffect(() => {
    if (!hurry) return;
    const early = Date.now() - (dismissAfter - hurryAfter);
    for (const [id, since] of doneAt.current) doneAt.current.set(id, Math.min(since, early));
  }, [hurry, dismissAfter, hurryAfter]);

  // Finished tasks leave on their own, but never while someone is reading.
  useEffect(() => {
    if (!onDismiss || expanded) return;
    const now = Date.now();
    const timers: number[] = [];
    for (const task of tasks) {
      if (task.status === "running") continue;
      const since = doneAt.current.get(task.id) ?? now;
      doneAt.current.set(task.id, since);
      timers.push(window.setTimeout(() => onDismiss(task.id), Math.max(0, dismissAfter - (now - since))));
    }
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [tasks, expanded, onDismiss, dismissAfter, hurry]);

  // Reading restarts the clock, so a task doesn't vanish as the pointer leaves.
  useEffect(() => {
    if (!expanded) return;
    const now = Date.now();
    for (const id of doneAt.current.keys()) doneAt.current.set(id, now);
  }, [expanded]);

  const handlePointerEnter = useCallback((event: PointerEvent) => {
    if (event.pointerType === "mouse") setOpen(true);
  }, []);
  const handlePointerLeave = useCallback((event: PointerEvent) => {
    if (event.pointerType === "mouse") setOpen(false);
  }, []);
  const handleBlur = useCallback((event: FocusEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
  }, []);
  const handleKeyDown = useCallback((event: KeyboardEvent) => {
    if (event.key === "Escape") setOpen(false);
  }, []);

  return (
    <MotionConfig reducedMotion="user">
      <div className={`relative flex flex-col items-center ${className}`}>
        <output aria-live="polite" className="sr-only">
          {announcement}
        </output>
        <AnimatePresence>
          {current ? (
            <motion.div
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              className="relative"
              exit={{ opacity: 0, y: 8, filter: "blur(4px)" }}
              initial={{ opacity: 0, y: 8, filter: "blur(4px)" }}
              key="island"
              transition={SWAP}
            >
              {/* Tasks waiting behind the current one peek out above it. */}
              {Array.from({ length: behind }, (_, i) => (
                <motion.div
                  animate={{
                    opacity: expanded ? 0 : 1 - (i + 1) * 0.3,
                    y: expanded ? 0 : -(i + 1) * 5,
                    scale: 1 - (i + 1) * 0.06,
                  }}
                  aria-hidden="true"
                  className="absolute inset-0 rounded-[20px] bg-bar ring-1 ring-bar-line"
                  initial={false}
                  key={i}
                  style={{ zIndex: -1 - i }}
                  transition={MORPH}
                />
              ))}
              <motion.div
                className="relative overflow-hidden bg-bar text-bar-ink shadow-lg shadow-black/20 ring-1 ring-bar-line"
                layout
                onBlur={handleBlur}
                onFocus={() => setOpen(true)}
                onKeyDown={handleKeyDown}
                onPointerEnter={handlePointerEnter}
                onPointerLeave={handlePointerLeave}
                ref={islandRef}
                style={{ borderRadius: 20 }}
                transition={MORPH}
              >
                {expanded ? (
                  <motion.ul
                    animate={{ opacity: 1, filter: "blur(0px)" }}
                    className="flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-3.5 px-4 py-3.5"
                    initial={{ opacity: 0, filter: "blur(4px)" }}
                    key="list"
                    layout="position"
                    transition={{ ...SWAP, delay: 0.05 }}
                  >
                    {tasks.map((task) => (
                      <li key={task.id}>
                        <TaskRow task={task} />
                      </li>
                    ))}
                  </motion.ul>
                ) : (
                  <button
                    aria-expanded={false}
                    aria-label={`${describe(current)}. ${tasks.length} ${tasks.length === 1 ? "task" : "tasks"}, show all`}
                    className="flex h-10 max-w-[calc(100vw-2rem)] items-center px-4 text-left focus-visible:outline-none"
                    onClick={() => setOpen(true)}
                    type="button"
                  >
                    <AnimatePresence initial={false} mode="popLayout">
                      <motion.div
                        animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                        className="min-w-0"
                        exit={{ opacity: 0, y: -6, filter: "blur(4px)" }}
                        initial={{ opacity: 0, y: 6, filter: "blur(4px)" }}
                        key={`${current.id}-${current.status}`}
                        layout="position"
                        transition={SWAP}
                      >
                        <div className="flex min-w-48 items-center gap-2.5">
                          <TaskIcon task={current} />
                          <span className="min-w-0 flex-1 truncate text-[13px] leading-4">
                            {current.label}
                            {current.detail ? <span className="text-bar-muted"> · {current.detail}</span> : null}
                          </span>
                          <span className="pl-3">
                            <TaskTrailingInert task={current} />
                          </span>
                        </div>
                      </motion.div>
                    </AnimatePresence>
                  </button>
                )}
              </motion.div>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    </MotionConfig>
  );
}
