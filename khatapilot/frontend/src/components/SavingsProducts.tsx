// Where to keep the savings: real Bangladeshi options (a monthly DPS in the bKash app, Sanchayapatra savings
// certificates), with their published rates, the date they were checked and the sources, and which fit this
// shop's savings pace. Read-only: KhataPilot opens or pays into none of them.

import { ExternalLink } from "lucide-react";

import { useData } from "../hooks";
import { useApp } from "../state";
import { Amount, Badge, Card, Loading, Note } from "../ui";

interface Product {
  id: string;
  name: string;
  provider: string;
  kind: "monthly" | "lump_sum";
  what: string;
  rate: string;
  tenure: string;
  how: string;
  notes: string[];
  fit: string;
  fits: boolean | null;
  instalment_paisa: number | null;
  sources: { title: string; url: string }[];
}

export function SavingsProducts() {
  const { version } = useApp();
  const products = useData<{ checked: string; lean_month_paisa: number; products: Product[]; note: string }>(
    "/savings/products",
    [version],
  );
  const data = products.data;
  return (
    <Card eyebrow="Where to keep it" title="Real savings products, and which fit your shop">
      {!data ? (
        products.error ? <Note tone="bad">{products.error}</Note> : <Loading />
      ) : (
        <div className="space-y-4">
          <ul className="divide-y divide-line">
            {data.products.map((p) => (
              <li key={p.id} className="space-y-1.5 py-3 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{p.name}</span>
                  {p.kind === "monthly" ? (
                    p.fits ? <Badge tone="ok">fits: <Amount paisa={p.instalment_paisa} /> a month</Badge> : <Badge tone="neutral">not yet</Badge>
                  ) : (
                    <Badge tone="info">lump sum</Badge>
                  )}
                </div>
                <p className="text-muted">{p.what}</p>
                <p className="text-xs">
                  <span className="text-faint">Rate:</span> {p.rate} · <span className="text-faint">Term:</span> {p.tenure}
                </p>
                <p className="text-xs">{p.fit}</p>
                <p className="text-xs text-muted">{p.how}</p>
                {p.notes.length > 0 && <p className="text-xs text-faint">{p.notes.join(" ")}</p>}
                <p className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-faint">
                  {p.sources.map((s) => (
                    <a key={s.url} href={s.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 underline-offset-2 hover:text-ink hover:underline">
                      <ExternalLink className="size-3" /> {s.title}
                    </a>
                  ))}
                </p>
              </li>
            ))}
          </ul>
          <Note tone="neutral" title={`Checked ${data.checked}`}>{data.note}</Note>
        </div>
      )}
    </Card>
  );
}
