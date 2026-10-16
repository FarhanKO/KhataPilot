import { Download, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";

import { api, errorText } from "../api";
import { dateTimeLabel, pct, taka } from "../format";
import { useData } from "../hooks";
import { useApp } from "../state";
import type { Consent, CreditProfile, Reason } from "../types";
import { Badge, Button, Card, Eyebrow, Loading, Note, Page, PageHeader, tdClass, thClass, TONE_TEXT, type Tone } from "../ui";

const BAND_TONE: Record<string, Tone> = { A: "ok", B: "ok", C: "warn", D: "bad", E: "bad" };
const BAND_FILL: Record<Tone, string> = {
  ok: "bg-ok text-white",
  warn: "bg-warn text-white",
  bad: "bg-bad text-white",
  neutral: "bg-raised",
  brand: "bg-brand",
  info: "bg-info",
};
const BANDS = ["A", "B", "C", "D", "E"];

function value(unit: string, v: number): string {
  if (unit === "taka") return taka(Math.round(v * 100), true);
  if (unit === "pct") return pct(v);
  if (unit === "days") return `${v.toFixed(1)} days`;
  if (unit === "months") return `${v.toFixed(0)} months`;
  return v.toFixed(0);
}

function Reasons({ title, items, tone }: { title: string; items: Reason[]; tone: "ok" | "bad" }) {
  return (
    <Card eyebrow={tone === "ok" ? "Helps" : "Hurts"} title={title}>
      {items.length === 0 ? (
        <p className="text-sm text-faint">Nothing significant.</p>
      ) : (
        <ol className="space-y-4">
          {items.map((r, i) => (
            <li key={r.feature} className="flex gap-3 text-sm">
              <span className="label w-5 shrink-0 pt-0.5 text-faint">{String(i + 1).padStart(2, "0")}</span>
              <div className="min-w-0 flex-1">
                <div className="leading-snug">{r.text}</div>
                <div className="mt-2 flex items-center gap-3">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-raised">
                    <div
                      className={`h-full rounded-full ${tone === "ok" ? "bg-ok" : "bg-bad"}`}
                      style={{ width: `${Math.min(100, Math.abs(r.impact) * 140)}%` }}
                    />
                  </div>
                  <span className="label w-24 text-right text-[10px] text-faint">
                    {r.impact > 0 ? "+" : ""}
                    {r.impact.toFixed(2)} log-odds
                  </span>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

export function CreditTab() {
  const { version, refresh, toast } = useApp();
  const consent = useData<Consent>("/credit/consent", [version]);
  const profile = useData<CreditProfile | null>(consent.data?.granted ? "/credit/profile" : null, [version, consent.data?.granted]);
  const [busy, setBusy] = useState<string | null>(null);

  async function act(id: string, fn: () => Promise<unknown>, done?: string) {
    setBusy(id);
    try {
      await fn();
      if (done) toast(done, "ok");
      await consent.reload();
      await profile.reload();
      refresh();
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setBusy(null);
    }
  }

  async function exportData() {
    setBusy("export");
    try {
      const data = await api.get<unknown>("/data/export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = "khatapilot-my-data.json";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast(errorText(e), "bad");
    } finally {
      setBusy(null);
    }
  }

  if (consent.error) return <Page><Note tone="bad">{consent.error}</Note></Page>;
  if (!consent.data) return <Page><Loading /></Page>;

  if (!consent.data.granted) {
    return (
      <Page narrow>
        <PageHeader
          eyebrow="Credit profile"
          title="Your consent comes first"
          description="KhataPilot can turn your books into a credit profile that lenders understand. Nothing is built or shared until you agree here."
        />
        <Card>
          <div className="space-y-5">
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 size-5 shrink-0" />
              <p className="text-[15px] leading-relaxed">{consent.data.text}</p>
            </div>
            <Note tone="neutral" title="What is used">
              Sales, customer credit (baki) and repayments, supplier payments, cash and mobile-money balances, household
              withdrawals and how long your books go back. Never used: your shop type, area or district. Consent text version{" "}
              {consent.data.version}.
            </Note>
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" busy={busy === "grant"} onClick={() => act("grant", () => api.post("/credit/consent"), "Consent recorded.")}>
                I agree: build my credit profile
              </Button>
              <Button variant="ghost" busy={busy === "export"} onClick={exportData}>
                <Download className="size-4" /> Export my data
              </Button>
            </div>
            <p className="text-xs text-faint">Only you can give or withdraw consent, here. The chat assistant cannot do it for you.</p>
          </div>
        </Card>
      </Page>
    );
  }

  const p = profile.data;
  const tone = p ? (BAND_TONE[p.band] ?? "neutral") : "neutral";
  return (
    <Page>
      <PageHeader
        eyebrow="Credit profile"
        title="What a lender would see"
        description={
          <>
            Consent given {consent.data.granted_at ? dateTimeLabel(consent.data.granted_at) : ""}. Shop type, area and district
            are never inputs.
          </>
        }
        actions={
          <>
            <Button variant="primary" size="sm" busy={busy === "build"} onClick={() => act("build", () => api.post("/credit/profile"))}>
              <RefreshCw className="size-3.5" />
              {p ? "Rebuild from today's books" : "Build my profile"}
            </Button>
            <Button variant="secondary" size="sm" busy={busy === "export"} onClick={exportData}>
              <Download className="size-3.5" /> Export my data
            </Button>
            <Button
              variant="dangerGhost"
              size="sm"
              busy={busy === "withdraw"}
              onClick={() => {
                if (confirm("Withdraw consent? Your stored credit profiles are deleted and unsent loan applications cancelled."))
                  void act("withdraw", () => api.del("/credit/consent"), "Consent withdrawn; profiles deleted.");
              }}
            >
              <Trash2 className="size-3.5" /> Withdraw consent
            </Button>
          </>
        }
      />

      {profile.loading && !p ? (
        <Loading />
      ) : !p ? (
        <Note tone="info">No profile yet. Press “Build my profile”: it reads your books up to yesterday and takes a second.</Note>
      ) : (
        <>
          <div className="grid gap-6 md:grid-cols-[minmax(0,320px)_1fr]">
            <section className="rounded-2xl border border-line bg-surface p-5">
              <Eyebrow tone={tone}>Band</Eyebrow>
              <div className="mt-4 flex items-center gap-5">
                <div className={`stamp grid size-24 shrink-0 place-items-center rounded-2xl border-[3px] border-current ${TONE_TEXT[tone]}`}>
                  <span className="display text-[64px] leading-none">{p.band}</span>
                </div>
                <div>
                  <div className="text-sm font-medium capitalize">{p.band_words}</div>
                  <div className="display mt-1 text-[30px]">{pct(p.probability_of_default, 1)}</div>
                  <div className="text-xs text-muted">chance of falling behind</div>
                </div>
              </div>
              <div className="mt-5 grid grid-cols-5 gap-1">
                {BANDS.map((b) => (
                  <span
                    key={b}
                    className={`label grid h-7 place-items-center rounded-md text-[11px] font-semibold ${
                      b === p.band ? BAND_FILL[BAND_TONE[b]] : "bg-raised text-faint"
                    }`}
                  >
                    {b}
                  </span>
                ))}
              </div>
              <div className="label mt-1.5 flex justify-between text-[9.5px] text-faint">
                <span>lowest risk</span>
                <span>highest risk</span>
              </div>
            </section>
            <section className="rounded-2xl border border-line bg-surface p-5">
              <Eyebrow>Summary</Eyebrow>
              <p className="mt-4 text-[16px] leading-relaxed">{p.summary}</p>
              <p className="mt-4 border-t border-line pt-3 text-xs leading-relaxed text-muted">
                Built {dateTimeLabel(p.created_at)} from {Math.round(p.history_days / 30.4)} months of books by model{" "}
                <span className="font-mono">{p.model_version}</span> (monotone LightGBM, calibrated; trained on synthetic
                shops). Reasons come from SHAP values: how much each fact pushed your risk up or down.
              </p>
            </section>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Reasons title="Helping your score" items={p.helps} tone="ok" />
            <Reasons title="Holding it back" items={p.hurts} tone="bad" />
          </div>

          <Card eyebrow="Inputs" title="Your numbers next to the typical shop" flush>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-y border-line bg-raised/50">
                  <tr>
                    <th className={thClass}>From your books</th>
                    <th className={`${thClass} text-right`}>Yours</th>
                    <th className={`${thClass} text-right`}>Typical shop</th>
                    <th className={thClass}>Used in the score</th>
                  </tr>
                </thead>
                <tbody>
                  {p.features.map((f) => (
                    <tr key={f.name} className="border-t border-line first:border-0">
                      <td className={tdClass}>{f.label}</td>
                      <td className={`${tdClass} tabular text-right font-medium`}>{value(f.unit, f.value)}</td>
                      <td className={`${tdClass} tabular text-right text-muted`}>{value(f.unit, f.typical)}</td>
                      <td className={tdClass}>
                        {f.scored ? <Badge tone="ok">scored</Badge> : <Badge tone="neutral">shown, not scored</Badge>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="border-t border-line px-5 py-4 text-xs leading-relaxed text-muted">
              Sales level, margin and mobile-money share are shown to lenders but not scored: on our test shops they did not
              predict repayment and they differ by shop type and region, so scoring them only made the gaps between groups
              wider.
            </p>
          </Card>
        </>
      )}
    </Page>
  );
}
