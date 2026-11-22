import { FileDown, FileText, ShieldAlert, ShieldCheck, TriangleAlert } from "lucide-react";
import { lazy, Suspense, useState } from "react";

import { api, errorText } from "../api";
import { useApp } from "../state";
import type { InvoiceCheckData, MonthlyReport, ReportReady, ToolOutcome } from "../types";
import { Badge, Button, Marker } from "../ui";
import { ExportDataButton } from "./DataActions";

// Loaded only when a report is opened, so the chat stays light.
const ReportView = lazy(() => import("./ReportView").then((m) => ({ default: m.ReportView })));

const CARD = "w-full max-w-[min(92%,38rem)] rounded-2xl border p-4";

/** A monthly report made in chat: open it, or save it as a PDF (the browser's print dialog). */
function ReportCard({ data }: { data: ReportReady }) {
  const { toast } = useApp();
  const r = data.report;
  const [busy, setBusy] = useState<"open" | "pdf" | null>(null);
  const [shown, setShown] = useState<{ report: MonthlyReport; print: boolean } | null>(null);

  async function open(print: boolean) {
    setBusy(print ? "pdf" : "open");
    try {
      const report = await api.get<MonthlyReport>(`/reports/monthly?kind=${r.kind}&month=${r.month}`);
      setShown({ report, print });
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={`${CARD} border-line bg-surface`}>
      <div className="label flex items-center gap-1.5 text-[10.5px] text-faint">
        <FileText className="size-3.5" />
        {r.kind === "lender" ? "Report for a bank or NGO" : "Your monthly report"} · {r.period}
      </div>
      <div className="mt-1.5 text-[15px] font-medium">{r.title}</div>
      {r.headline && <p className="mt-1 text-sm leading-relaxed text-muted">{r.headline}</p>}
      {r.figures.length > 0 && (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-dashed border-line-strong pt-3">
          {r.figures.map((f) => (
            <div key={f.label}>
              <dt className="label text-[10px] text-faint">{f.label}</dt>
              <dd className="tabular text-sm font-medium">{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="primary" size="sm" busy={busy === "pdf"} disabled={busy !== null} onClick={() => open(true)}>
          <FileDown className="size-3.5" /> Save as PDF
        </Button>
        <Button variant="secondary" size="sm" busy={busy === "open"} disabled={busy !== null} onClick={() => open(false)}>
          Open
        </Button>
        <span className="text-xs text-muted">Nothing is sent to anyone.</span>
      </div>
      {shown && (
        <Suspense fallback={null}>
          <ReportView report={shown.report} autoPrint={shown.print} onClose={() => setShown(null)} />
        </Suspense>
      )}
    </div>
  );
}

function ExportCard() {
  return (
    <div className={`${CARD} border-line bg-surface`}>
      <div className="label flex items-center gap-1.5 text-[10.5px] text-faint">
        <Marker /> Your data · one file, phone numbers masked
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <ExportDataButton size="sm" />
        <span className="text-xs text-muted">It downloads to this device; nothing is sent anywhere.</span>
      </div>
    </div>
  );
}

const VERDICT = {
  ok: { tone: "ok", label: "Looks consistent", border: "border-ok/40 bg-ok-soft/60", Icon: ShieldCheck },
  check: { tone: "warn", label: "Check before paying", border: "border-warn/40 bg-warn-soft", Icon: TriangleAlert },
  stop: { tone: "bad", label: "Do not pay yet", border: "border-bad/45 bg-bad-soft", Icon: ShieldAlert },
} as const;

/** The invoice check: what the invoice says, and what the books say about it. */
function InvoiceCard({ data }: { data: InvoiceCheckData }) {
  const v = VERDICT[data.verdict] ?? VERDICT.check;
  return (
    <div className={`${CARD} ${v.border}`}>
      <div className="flex items-center gap-2">
        <v.Icon className="size-4" />
        <span className="label text-[10.5px]">Invoice check</span>
        <Badge tone={v.tone} className="ml-auto">
          {v.label}
        </Badge>
      </div>
      <div className="mt-2 text-[15px] font-medium">
        {data.matched_supplier ?? data.supplier}
        {data.invoice_number ? <span className="font-normal text-muted"> · #{data.invoice_number}</span> : null}
      </div>
      <div className="mt-0.5 text-sm text-muted">
        {data.total ?? "no total found"}
        {data.due ? ` · due ${data.due}` : ""}
        {data.pay_to_numbers.length ? ` · pay to ${data.pay_to_numbers.join(", ")}` : ""}
      </div>
      {data.findings.length > 0 && (
        <ul className="mt-3 space-y-1.5 border-t border-dashed border-line-strong pt-3 text-sm">
          {data.findings.map((f) => (
            <li key={f.text} className="flex items-start gap-2">
              <Marker tone={f.level === "stop" ? "bad" : f.level === "check" ? "warn" : "neutral"} className="mt-1.5" />
              <span>{f.text}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-muted">A check only: nothing was paid, booked or sent.</p>
    </div>
  );
}

/** The cards some tool results add under a reply. */
export function OutcomeCards({ outcomes }: { outcomes: ToolOutcome[] }) {
  return (
    <>
      {outcomes
        .filter((o) => o.status === "ok")
        .map((o, i) => {
          if (o.tool === "make_report" && (o.data as ReportReady | null)?.report)
            return <ReportCard key={`r${i}`} data={o.data as ReportReady} />;
          if (o.tool === "export_my_data") return <ExportCard key={`e${i}`} />;
          if (o.tool === "check_invoice" && (o.data as InvoiceCheckData | null)?.verdict)
            return <InvoiceCard key={`i${i}`} data={o.data as InvoiceCheckData} />;
          return null;
        })}
    </>
  );
}
