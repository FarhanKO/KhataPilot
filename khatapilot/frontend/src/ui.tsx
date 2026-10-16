// Small building blocks used by every screen.

import { Loader2, X } from "lucide-react";
import { useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import { taka } from "./format";

export type Tone = "neutral" | "brand" | "ok" | "warn" | "bad" | "info";

const TONES: Record<Tone, string> = {
  neutral: "bg-raised text-muted border-line",
  brand: "bg-brand-soft text-ink border-line-strong",
  ok: "bg-ok-soft text-ok border-ok/25",
  warn: "bg-warn-soft text-warn border-warn/30",
  bad: "bg-bad-soft text-bad border-bad/35",
  info: "bg-info-soft text-info border-info/25",
};

const MARKERS: Record<Tone, string> = {
  neutral: "bg-faint",
  brand: "bg-ink",
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  info: "bg-info",
};

export const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-ink",
  brand: "text-ink",
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
  info: "text-info",
};

/** The small square that marks a label, as on a printed form. */
export function Marker({ tone = "brand", className = "" }: { tone?: Tone; className?: string }) {
  return <span aria-hidden className={`inline-block size-1.5 shrink-0 ${MARKERS[tone]} ${className}`} />;
}

/** A mono, upper-case section label with a marker: "■ DECISION TRACE". */
export function Eyebrow({ tone = "brand", children, className = "" }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={`label inline-flex items-center gap-2 text-muted ${className}`}>
      <Marker tone={tone} />
      {children}
    </span>
  );
}

export function Badge({ tone = "neutral", children, className = "" }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-[5px] border px-1.5 py-px font-mono text-[10.5px] font-medium tracking-[0.04em] whitespace-nowrap uppercase ${TONES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

type Variant = "primary" | "secondary" | "danger" | "ghost" | "dangerGhost";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-brand text-brand-ink border-transparent hover:opacity-90",
  secondary: "bg-surface text-ink border-line-strong hover:border-ink",
  danger: "bg-stop text-white border-transparent hover:opacity-90",
  ghost: "bg-transparent text-muted border-transparent hover:bg-raised hover:text-ink",
  dangerGhost: "bg-transparent text-bad border-transparent hover:bg-bad-soft",
};
const SIZES = {
  sm: "h-8 px-3 text-[13px]",
  md: "h-10 px-4 text-sm",
  icon: "size-9",
};

export function Button({
  variant = "secondary",
  size = "md",
  busy = false,
  className = "",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: keyof typeof SIZES; busy?: boolean }) {
  return (
    <button
      {...rest}
      disabled={busy || rest.disabled}
      className={`inline-flex shrink-0 items-center justify-center gap-1.5 rounded-full border font-medium whitespace-nowrap transition disabled:cursor-not-allowed disabled:opacity-45 ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
    >
      {busy && <Loader2 className="size-4 animate-spin" />}
      {children}
    </button>
  );
}

export function Card({ title, eyebrow, actions, children, className = "", tone, flush = false }: {
  title?: ReactNode;
  eyebrow?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  tone?: Tone;
  /** No inner padding, for tables that run edge to edge. */
  flush?: boolean;
}) {
  const border = tone && tone !== "neutral" ? TONES[tone].split(" ").find((c) => c.startsWith("border-")) : "border-line";
  return (
    <section className={`overflow-hidden rounded-2xl border bg-surface ${border} ${className}`}>
      {(title || actions || eyebrow) && (
        <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-5 pt-4 pb-3">
          <div className="min-w-0 space-y-1">
            {eyebrow && <Eyebrow>{eyebrow}</Eyebrow>}
            {title && <h3 className="text-[15px] font-medium tracking-[-0.01em]">{title}</h3>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={flush ? "" : `px-5 pb-5 ${title || actions || eyebrow ? "" : "pt-5"}`}>{children}</div>
    </section>
  );
}

/** Money with a quiet taka sign and faded paisa: ৳12,121.50 */
export function Amount({ paisa, whole = true, className = "" }: { paisa: number | null | undefined; whole?: boolean; className?: string }) {
  if (paisa === null || paisa === undefined) return <span className={className}>—</span>;
  const [int, dec] = taka(Math.abs(paisa), whole).replace("৳", "").split(".");
  return (
    <span className={`tabular whitespace-nowrap ${className}`}>
      {paisa < 0 && "−"}
      <span className="mr-[0.08em] text-[0.72em] font-normal opacity-55">৳</span>
      {int}
      {dec && <span className="opacity-40">.{dec}</span>}
    </span>
  );
}

export function Stat({ label, value, hint, tone, onClick }: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
  onClick?: () => void;
}) {
  const color = tone === "bad" ? "text-bad" : tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : "text-ink";
  const body = (
    <>
      <div className="label flex items-center gap-2 text-faint">
        <Marker tone={tone && tone !== "neutral" ? tone : "brand"} />
        {label}
      </div>
      <div className={`display mt-3 text-[26px] ${color}`}>{value}</div>
      {hint && <div className="mt-1.5 text-xs text-muted">{hint}</div>}
    </>
  );
  const frame = "flex flex-col justify-start rounded-2xl border border-line bg-surface px-4 py-3.5 text-left";
  return onClick ? (
    <button onClick={onClick} className={`${frame} transition hover:border-ink`}>
      {body}
    </button>
  ) : (
    <div className={frame}>{body}</div>
  );
}

export function Note({ tone = "info", title, children }: { tone?: Tone; title?: ReactNode; children: ReactNode }) {
  return (
    <div className={`rounded-xl border px-4 py-3 text-sm ${TONES[tone]}`}>
      {title && (
        <div className="label mb-1 flex items-center gap-2">
          <Marker tone={tone} />
          {title}
        </div>
      )}
      <div className={tone === "neutral" ? "" : "text-ink"}>{children}</div>
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="label flex items-center gap-2 py-8 text-faint">
      <Loader2 className="size-3.5 animate-spin" /> {label}…
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-8 text-center text-sm text-faint">{children}</div>;
}

/** The column every screen except the chat sits in. */
export function Page({ children, narrow = false }: { children: ReactNode; narrow?: boolean }) {
  return <div className={`mx-auto ${narrow ? "max-w-3xl" : "max-w-6xl"} space-y-6 px-4 py-7 sm:px-6 lg:px-8`}>{children}</div>;
}

export function PageHeader({ eyebrow, title, description, actions }: {
  eyebrow: string;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0 space-y-2.5">
        <Eyebrow>{eyebrow}</Eyebrow>
        <h1 className="display text-[30px] sm:text-[34px]">{title}</h1>
        {description && <p className="max-w-2xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** A row of mutually exclusive pills: 14 / 21 / 28 days, All / Flagged. */
export function Segmented<T extends string | number>({ options, value, onChange, className = "" }: {
  options: { value: T; label: ReactNode }[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <div className={`inline-flex shrink-0 rounded-full border border-line bg-surface p-0.5 ${className}`}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          onClick={() => onChange(o.value)}
          aria-pressed={o.value === value}
          className={`rounded-full px-3 py-1 text-xs font-medium whitespace-nowrap transition ${
            o.value === value ? "bg-brand text-brand-ink" : "text-muted hover:text-ink"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Modal({ open, onClose, title, eyebrow, children, wide = false }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  eyebrow?: ReactNode;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      className={`m-auto w-[calc(100%-2rem)] ${wide ? "max-w-xl" : "max-w-md"} rounded-3xl border border-line bg-surface p-0 text-ink shadow-[0_30px_80px_-20px_rgba(20,15,10,0.45)] backdrop:bg-[#1a1714]/55 backdrop:backdrop-blur-[2px]`}
    >
      {open && (
        <div>
          <header className="flex items-start justify-between gap-4 px-6 pt-5 pb-1">
            <div className="space-y-1.5">
              {eyebrow && <Eyebrow>{eyebrow}</Eyebrow>}
              <h2 className="display text-[22px]">{title}</h2>
            </div>
            <button aria-label="Close" onClick={onClose} className="-mr-2 rounded-full p-2 text-muted hover:bg-raised hover:text-ink">
              <X className="size-4" />
            </button>
          </header>
          <div className="thin-scrollbar max-h-[78vh] overflow-y-auto px-6 pt-3 pb-6">{children}</div>
        </div>
      )}
    </dialog>
  );
}

/** PIN entry shown as boxes; the real input sits on top, invisible, so paste and mobile keypads work. */
export function PinField({ value, onChange, autoFocus = true }: { value: string; onChange: (v: string) => void; autoFocus?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  // A dialog's showModal() runs after this mounts and moves focus to its first button, so focus on the next frame.
  useEffect(() => {
    if (!autoFocus) return;
    const frame = requestAnimationFrame(() => input.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [autoFocus]);
  const slots = Math.min(6, Math.max(4, value.length + 1));
  return (
    <label className="block">
      <span className="label text-faint">Your PIN</span>
      <div className="group relative mt-2 flex gap-2 rounded-2xl">
        {Array.from({ length: slots }, (_, i) => (
          <span
            key={i}
            className={`grid h-14 flex-1 place-items-center rounded-xl border text-2xl transition ${
              i < value.length
                ? "border-ink bg-surface"
                : i === value.length
                  ? "border-line-strong bg-raised group-focus-within:border-ink group-focus-within:bg-surface group-focus-within:ring-4 group-focus-within:ring-ink/10"
                  : "border-line bg-raised"
            } ${i >= 4 ? "border-dashed" : ""}`}
          >
            {i < value.length && <span className="size-3 rounded-full bg-ink" />}
          </span>
        ))}
        <input
          ref={input}
          type="password"
          inputMode="numeric"
          autoComplete="off"
          aria-label="PIN"
          maxLength={6}
          value={value}
          onChange={(e) => onChange(e.target.value.replace(/\D/g, ""))}
          className="absolute inset-0 cursor-text rounded-2xl opacity-0"
        />
      </div>
      <span className="mt-1.5 block text-xs text-faint">4 to 6 digits.</span>
    </label>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label text-faint">{label}</span>
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

export const inputClass =
  "h-10 w-full rounded-xl border border-line-strong bg-surface px-3.5 text-sm text-ink outline-none transition placeholder:text-faint focus:border-ink";

/** Column heading in a ledger table. */
export const thClass = "label px-3 py-2.5 text-left font-normal whitespace-nowrap text-faint first:pl-5 last:pr-5";
export const tdClass = "px-3 py-2.5 first:pl-5 last:pr-5";
