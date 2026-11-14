// SMS from the owner's own phone: bKash, Nagad and bank SMS the phone received, and what KhataPilot did with
// each. New SMS arrive by themselves when the phone app's webhook is set; "Fetch from phone" reads what is
// already there, and an "SMS Backup & Restore" file or pasted SMS can be imported. Only money SMS are kept;
// a payment SMS from an ordinary number is flagged as a likely fake and never booked.

import { ShieldAlert, Smartphone, Upload } from "lucide-react";
import { useRef, useState } from "react";

import { api, errorText } from "../api";
import { dayLabel, timeLabel } from "../format";
import { useData } from "../hooks";
import { useApp } from "../state";
import { Amount, Badge, Button, Card, Empty, Loading, Note, type Tone } from "../ui";

interface PhoneSms {
  id: string;
  sender: string;
  sender_kind: string;
  source: string;
  body: string;
  received_at: string | null;
  seen_at: string;
  provider: string | null;
  trx_id: string | null;
  direction: string | null;
  amount_paisa: number | null;
  status: "booked" | "asked" | "review" | "duplicate" | "fake_sender" | "not_booked" | "ignored";
  detail: string;
  question_id: string | null;
}

interface PhoneStatus {
  connected: boolean;
  gateway_url: string | null;
  webhook: boolean;
  webhook_url: string;
  trust: "sender_id" | "provider";
  review_over_paisa: number;
  sms_via_phone: boolean;
  owner_alert_sms: boolean;
  last_seen_at: string | null;
  last_pull_at: string | null;
  counts: Record<string, number>;
  recent: PhoneSms[];
}

const STATUS: Record<PhoneSms["status"], { label: string; tone: Tone }> = {
  booked: { label: "booked", tone: "ok" },
  asked: { label: "your answer", tone: "warn" },
  review: { label: "check it", tone: "warn" },
  duplicate: { label: "already in books", tone: "neutral" },
  fake_sender: { label: "likely fake", tone: "bad" },
  not_booked: { label: "not booked", tone: "neutral" },
  ignored: { label: "ignored", tone: "neutral" },
};

function summary(report: Record<string, unknown>): string {
  const n = (k: string) => Number(report[k] ?? 0);
  const parts = [
    n("booked") && `${n("booked")} booked`,
    n("asked") && `${n("asked")} need your answer`,
    n("review") && `${n("review")} to check`,
    n("fake_sender") && `${n("fake_sender")} likely fake`,
    n("duplicate") && `${n("duplicate")} already in the books`,
    n("already_seen") && `${n("already_seen")} seen before`,
    n("not_booked") && `${n("not_booked")} not booked`,
    n("skipped") && `${n("skipped")} not about money (not kept)`,
  ].filter(Boolean);
  return parts.length ? parts.join(", ") + "." : "Nothing new.";
}

export function PhoneSmsCard() {
  const { version, refresh, toast } = useApp();
  const status = useData<PhoneStatus>("/phone", [version], 15_000);
  const [busy, setBusy] = useState<string | null>(null);
  const [pasting, setPasting] = useState(false);
  const [pasted, setPasted] = useState("");
  const file = useRef<HTMLInputElement>(null);

  async function run(key: string, call: () => Promise<Record<string, unknown>>, done?: (r: Record<string, unknown>) => string) {
    setBusy(key);
    try {
      const result = await call();
      toast(done ? done(result) : summary(result), "ok");
      refresh();
      void status.reload();
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setBusy(null);
    }
  }

  const pull = () => run("pull", () => api.post("/phone/pull"));
  const importText = () =>
    run("paste", () => api.post("/phone/import", { text: pasted }), (r) => {
      setPasted("");
      setPasting(false);
      return summary(r);
    });
  async function importFile(chosen: File | undefined) {
    if (!chosen) return;
    const text = await chosen.text();
    await run("file", () => api.post("/phone/import", text.trimStart().startsWith("<") ? { xml: text } : { text }));
    if (file.current) file.current.value = "";
  }
  const decide = (sms: PhoneSms, how: "book" | "ignore") =>
    run(`${how}-${sms.id}`, () => api.post(`/phone/sms/${sms.id}/${how}`), (r) =>
      how === "ignore" ? "Left out of the books." : String(r.detail || "Booked."),
    );
  const answer = (sms: PhoneSms, purpose: "baki" | "other") =>
    run(`ans-${sms.id}`, () => api.post(`/sms-questions/${sms.question_id}/answer`, { purpose }), (r) => String(r.message ?? "Booked."));

  const s = status.data;
  const waiting = s?.recent.filter((r) => r.status === "review" || r.status === "asked").length ?? 0;
  return (
    <Card
      eyebrow="Phone SMS"
      title={
        <span className="inline-flex items-center gap-2">
          <Smartphone className="size-4 text-muted" /> bKash, Nagad and bank SMS from your phone
          {waiting > 0 && <Badge tone="warn">{waiting} waiting</Badge>}
        </span>
      }
      actions={
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" busy={busy === "pull"} disabled={!s?.connected} onClick={pull}
            title={s?.connected ? "Read the SMS already on your phone" : "Connect your phone first (see below)"}>
            Fetch from phone
          </Button>
          <Button size="sm" variant="ghost" busy={busy === "file"} onClick={() => file.current?.click()}>
            <Upload className="size-3.5" /> Import
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setPasting((p) => !p)}>Paste SMS</Button>
          <input ref={file} type="file" accept=".xml,.txt,text/xml,text/plain" className="hidden"
            onChange={(e) => void importFile(e.target.files?.[0])} />
        </div>
      }
    >
      {!s ? (
        status.error ? <Note tone="bad">{status.error}</Note> : <Loading />
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-muted">
            {s.connected
              ? <>Phone connected{s.webhook ? "; new SMS arrive by themselves" : "; tap Fetch to read new SMS"}.</>
              : "No phone connected yet. You can still import a backup file or paste SMS."}{" "}
            {s.last_seen_at && <>Last SMS {dayLabel(s.last_seen_at.slice(0, 10))} {timeLabel(s.last_seen_at)}. </>}
            Only money SMS are kept{s.counts.skipped ? ` (${s.counts.skipped} others were not)` : ""}; an SMS that says you were
            paid but comes from an ordinary number is never booked.
            {s.trust === "sender_id"
              ? <> SMS from bKash, Nagad or your bank are booked; anything over <Amount paisa={s.review_over_paisa} /> the provider
                cannot confirm waits for you.</>
              : <> Only SMS the provider confirms are booked; the rest wait for you.</>}
          </p>
          {pasting && (
            <div className="space-y-2">
              <textarea value={pasted} onChange={(e) => setPasted(e.target.value)} rows={4}
                placeholder="Paste one or more bKash, Nagad or bank SMS here"
                className="w-full rounded-xl border border-line bg-page px-3 py-2 text-sm" />
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={() => setPasting(false)}>Cancel</Button>
                <Button size="sm" variant="primary" busy={busy === "paste"} disabled={!pasted.trim()} onClick={importText}>Book these</Button>
              </div>
            </div>
          )}
          {s.recent.length === 0 ? (
            <Empty>No money SMS from your phone yet.</Empty>
          ) : (
            <ul className="divide-y divide-line">
              {s.recent.slice(0, 12).map((sms) => (
                <li key={sms.id} className="space-y-1.5 py-2.5 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="flex min-w-0 items-center gap-2">
                      {sms.status === "fake_sender" && <ShieldAlert className="size-4 shrink-0 text-bad" />}
                      <span className="font-medium">{sms.sender === "unknown" ? (sms.source === "share" ? "Shared SMS" : "Pasted SMS") : sms.sender}</span>
                      <Badge tone={STATUS[sms.status].tone}>{STATUS[sms.status].label}</Badge>
                      {sms.trx_id && <span className="font-mono text-[11px] text-faint">{sms.trx_id}</span>}
                    </span>
                    <span className="flex items-center gap-3">
                      <span className="label text-[10.5px] text-faint">{dayLabel((sms.received_at ?? sms.seen_at).slice(0, 10))}</span>
                      {sms.amount_paisa != null && (
                        <Amount paisa={sms.amount_paisa} className={`w-20 text-right font-medium ${sms.direction === "out" ? "text-bad" : ""}`} />
                      )}
                    </span>
                  </div>
                  <p className="text-xs text-muted">{sms.detail || sms.body}</p>
                  {sms.status === "review" && (
                    <div className="flex gap-2">
                      <Button size="sm" variant="primary" busy={busy === `book-${sms.id}`} onClick={() => decide(sms, "book")}>Book</Button>
                      <Button size="sm" variant="ghost" busy={busy === `ignore-${sms.id}`} onClick={() => decide(sms, "ignore")}>Ignore</Button>
                    </div>
                  )}
                  {sms.status === "asked" && sms.question_id && (
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" variant="primary" busy={busy === `ans-${sms.id}`} onClick={() => answer(sms, "baki")}>Pays off their baki</Button>
                      <Button size="sm" variant="ghost" busy={busy === `ans-${sms.id}`} onClick={() => answer(sms, "other")}>Something else (a sale)</Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          {!s.connected && (
            <details className="rounded-xl border border-line px-4 py-3 text-sm text-muted">
              <summary className="cursor-pointer font-medium text-ink">Connect your Android phone</summary>
              <ol className="mt-2 list-decimal space-y-1 pl-5">
                <li>Install the free, open-source <b>SMS Gateway for Android</b> app (sms-gate.app) and turn on <b>Local Server</b>.</li>
                <li>Plug the phone in by USB (USB debugging on) and run <code>adb forward tcp:8080 tcp:8080</code> and <code>adb reverse tcp:8000 tcp:8000</code>.</li>
                <li>In <code>.env</code> set <code>PHONE_GATEWAY_URL=http://127.0.0.1:8080</code>, the app's username and password as <code>PHONE_GATEWAY_USER</code> / <code>PHONE_GATEWAY_PASSWORD</code>, and its Settings → Webhooks → Signing Key as <code>PHONE_WEBHOOK_KEY</code>. Restart the backend.</li>
                <li>Tap <b>Fetch from phone</b>: it reads the last 30 days and tells the phone to send new SMS here ({s.webhook_url}).</li>
                <li>Optional: <code>SMS_VIA_PHONE=true</code> sends approved reminders from your SIM; <code>OWNER_PHONE</code> + <code>OWNER_ALERT_SMS=true</code> texts you urgent alerts.</li>
              </ol>
            </details>
          )}
        </div>
      )}
    </Card>
  );
}
