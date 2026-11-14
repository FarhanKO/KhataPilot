import { Building2, FileText, Loader2, UserRound } from "lucide-react";
import { useEffect, useState, type ComponentType } from "react";

import { api, errorText } from "../api";
import type { MonthlyReport } from "../types";
import { Button, Field, inputClass, Modal, Note } from "../ui";

type Kind = MonthlyReport["kind"];

const KINDS: { kind: Kind; icon: ComponentType<{ className?: string }>; title: string; text: string }[] = [
  {
    kind: "owner",
    icon: UserRound,
    title: "For me: monthly statement",
    text: "Everything for one month: money in and out, week by week, baki and bills, payments, savings and goals, what the guard blocked, and what is coming.",
  },
  {
    kind: "lender",
    icon: Building2,
    title: "For a bank or NGO loan",
    text: "Six months of sales and profit, how steady they are, baki collected, supplier record, what you can repay, your credit profile if it is on, and how to check it. No customer names.",
  },
];

interface Months {
  months: { month: string; label: string; finished: boolean }[];
  default: string | null;
}

/** Pick a report and a month, then make it from the books. Nothing is sent: it opens to print or save. */
export function ReportDialog({ open, onClose, onReady }: { open: boolean; onClose: () => void; onReady: (r: MonthlyReport) => void }) {
  const [months, setMonths] = useState<Months | null>(null);
  const [month, setMonth] = useState<string>("");
  const [kind, setKind] = useState<Kind>("owner");
  const [making, setMaking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setProblem(null);
    api
      .get<Months>("/reports/months")
      .then((m) => {
        setMonths(m);
        setMonth((current) => current || m.default || "");
      })
      .catch((e) => setProblem(errorText(e)));
  }, [open]);

  async function make() {
    setMaking(true);
    setProblem(null);
    try {
      const report = await api.get<MonthlyReport>(`/reports/monthly?kind=${kind}&month=${month}`);
      onReady(report);
    } catch (e) {
      setProblem(errorText(e));
    } finally {
      setMaking(false);
    }
  }

  const chosen = months?.months.find((m) => m.month === month);
  return (
    <Modal open={open} onClose={onClose} eyebrow="Monthly report" title="Which report do you need?" wide>
      <div className="space-y-4">
        <div role="radiogroup" aria-label="Report" className="grid gap-2.5">
          {KINDS.map(({ kind: k, icon: Icon, title, text }) => (
            <button
              key={k}
              role="radio"
              aria-checked={kind === k}
              onClick={() => setKind(k)}
              className={`flex items-start gap-3 rounded-xl border p-3.5 text-left transition ${
                kind === k ? "border-ink bg-page ring-1 ring-ink" : "border-line hover:border-line-strong hover:bg-page/60"
              }`}
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-line bg-surface">
                <Icon className="size-4" />
              </span>
              <span>
                <span className="block text-[14.5px] font-medium">{title}</span>
                <span className="mt-0.5 block text-xs leading-relaxed text-muted">{text}</span>
              </span>
            </button>
          ))}
        </div>
        <Field label={kind === "lender" ? "Up to the end of" : "Month"}>
          <select className={inputClass} value={month} disabled={!months} onChange={(e) => setMonth(e.target.value)}>
            {(months?.months ?? []).map((m) => (
              <option key={m.month} value={m.month} disabled={kind === "lender" && !m.finished}>
                {m.label}
                {m.finished ? "" : " (so far)"}
              </option>
            ))}
          </select>
        </Field>
        {kind === "lender" && chosen && (
          <p className="text-xs text-muted">The six finished months up to {chosen.label}: lenders look at whole months.</p>
        )}
        {problem && <Note tone="bad">{problem}</Note>}
        {making && (
          <p className="flex items-center gap-2 text-sm text-muted" role="status">
            <Loader2 className="size-4 animate-spin" /> Putting your report together from the books…
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={making} disabled={!month || (kind === "lender" && chosen ? !chosen.finished : false)} onClick={make}>
            <FileText className="size-4" /> Make the report
          </Button>
        </div>
        <p className="text-[11.5px] text-faint">It opens here to print or save as a PDF. Nothing is sent to anyone; making it is noted in the audit log.</p>
      </div>
    </Modal>
  );
}
