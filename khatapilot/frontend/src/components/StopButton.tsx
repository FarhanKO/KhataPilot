import { Lock, OctagonX } from "lucide-react";
import { useState } from "react";

import { api, errorText } from "../api";
import { withAttempts } from "../format";
import { useApp } from "../state";
import type { ControlResult } from "../types";
import { Button, Modal, Note, PinField } from "../ui";

/** The kill switch: one press freezes everything at once; resuming needs the PIN. */
export function StopButton({ frozen }: { frozen: boolean }) {
  const { refresh, toast } = useApp();
  const [busy, setBusy] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [pin, setPin] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  async function stop() {
    setBusy(true);
    try {
      const result = await api.post<ControlResult>("/owner/stop", { reason: "The owner pressed STOP" });
      toast(result.message, "warn");
      refresh();
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setBusy(false);
    }
  }

  async function resume() {
    setBusy(true);
    setProblem(null);
    try {
      const result = await api.post<ControlResult>("/owner/resume", { pin });
      if (result.ok) {
        toast(result.message, "ok");
        setResuming(false);
        refresh();
      } else {
        setProblem(withAttempts(result.message, result.attempts_left));
      }
    } catch (e) {
      setProblem(errorText(e));
    } finally {
      setPin("");
      setBusy(false);
    }
  }

  if (!frozen) {
    return (
      <button
        onClick={stop}
        disabled={busy}
        title="Freeze every payment, message and loan application at once"
        aria-label="STOP: freeze everything"
        className="inline-flex h-9 items-center gap-1.5 rounded-full bg-stop pr-4 pl-3 font-mono text-[13px] font-bold tracking-[0.14em] text-white shadow-[0_0_0_3px_color-mix(in_srgb,var(--stop)_30%,transparent)] transition hover:brightness-110 disabled:opacity-60"
      >
        <OctagonX className="size-[18px]" />
        STOP
      </button>
    );
  }
  return (
    <>
      <button
        onClick={() => setResuming(true)}
        className="inline-flex h-9 items-center gap-2 rounded-full border border-white/70 px-3.5 text-[13px] font-medium text-white transition hover:bg-white/10"
      >
        <span className="size-2 animate-pulse rounded-full bg-stop" />
        Stopped · Resume
        <Lock className="size-3.5" />
      </button>
      <Modal open={resuming} onClose={() => setResuming(false)} eyebrow="Kill switch" title="Resume KhataPilot">
        <div className="space-y-5">
          <p className="text-sm text-muted">
            Payments, messages and loan applications are frozen. Scheduled payments are paused, not deleted. Enter your
            PIN to resume them.
          </p>
          <PinField value={pin} onChange={setPin} />
          {problem && <Note tone="bad">{problem}</Note>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setResuming(false)}>
              Stay stopped
            </Button>
            <Button variant="primary" busy={busy} disabled={pin.length < 4} onClick={resume}>
              Resume
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}
