// Formatting shared by every screen. Money arrives in paisa and is shown as ৳12,500.

const TAKA = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const WHOLE = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export function taka(paisa: number | null | undefined, whole = false): string {
  if (paisa === null || paisa === undefined) return "—";
  const value = Math.abs(paisa) / 100;
  return `${paisa < 0 ? "-" : ""}৳${(whole ? WHOLE : TAKA).format(value)}`;
}

export function compactTaka(paisa: number): string {
  const value = paisa / 100;
  if (Math.abs(value) >= 100_000) return `৳${(value / 1000).toFixed(0)}k`;
  if (Math.abs(value) >= 1_000) return `৳${(value / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return `৳${value.toFixed(0)}`;
}

const DHAKA = "Asia/Dhaka";

export function dayLabel(iso: string, long = false): string {
  const date = new Date(iso.length === 10 ? `${iso}T12:00:00+06:00` : iso);
  return date.toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    ...(long ? { year: "numeric" } : {}),
    timeZone: DHAKA,
  });
}

export function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: DHAKA });
}

export function dateTimeLabel(iso: string): string {
  return `${dayLabel(iso)} ${timeLabel(iso)}`;
}

/** "Good morning" through "Good night", by the hour in Dhaka. */
export function greeting(iso: string): string {
  const hour = Number(new Date(iso).toLocaleString("en-GB", { hour: "numeric", hourCycle: "h23", timeZone: DHAKA }));
  if (hour >= 5 && hour < 12) return "Good morning";
  if (hour >= 12 && hour < 14) return "Good noon";
  if (hour >= 14 && hour < 17) return "Good afternoon";
  if (hour >= 17 && hour < 21) return "Good evening";
  return "Good night";
}

export function pct(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function titleCase(text: string): string {
  return text.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export const TIER_NAMES: Record<number, string> = {
  0: "Read only",
  1: "Owner approves",
  2: "Within limits",
  3: "Needs PIN",
};

/** A Bangladeshi mobile number as 017XXXX5678; anything else (a bank account) as ****1234. */
export function maskNumber(number: string | null | undefined): string {
  if (!number) return "";
  const digits = number.replace(/\D/g, "");
  const mobile = digits.match(/^(?:880)?(01\d{9})$/);
  if (mobile) return `${mobile[1].slice(0, 3)}XXXX${mobile[1].slice(-4)}`;
  return `****${digits.slice(-4)}`;
}

/** The backend's message, plus the attempts left only if it did not say so already. */
export function withAttempts(message: string, left: number | null | undefined): string {
  return left != null && !/left/i.test(message) ? `${message} (${left} tries left)` : message;
}
