import { Download, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { api, errorText } from "../api";
import { useApp } from "../state";
import type { ControlResult, ErasePreview } from "../types";
import { Button, Loading, Modal, Note, PinField } from "../ui";

/** Everything KhataPilot holds about the shop, as one JSON file on the owner's device (numbers masked). */
export async function downloadMyData(): Promise<void> {
  const data = await api.get<unknown>("/data/export");
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "khatapilot-my-data.json";
  a.click();
  URL.revokeObjectURL(url);
}

export function ExportDataButton({ size = "md", variant = "secondary" }: { size?: "sm" | "md"; variant?: "secondary" | "ghost" }) {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      variant={variant}
      size={size}
      busy={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await downloadMyData();
        } catch (e) {
          toast(errorText(e), "bad");
        } finally {
          setBusy(false);
        }
      }}
    >
      <Download className={size === "sm" ? "size-3.5" : "size-4"} /> Export my data
    </Button>
  );
}

/** "Delete all my data": shows exactly what goes and what stays, then needs the PIN. */
export function EraseDataDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { refresh, toast } = useApp();
  const [preview, setPreview] = useState<ErasePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setPin("");
    setError(null);
    setPreview(null);
    api
      .get<ErasePreview>("/data/erase-preview")
      .then(setPreview)
      .catch((e) => setError(errorText(e)));
  }, [open]);

  async function erase() {
    setBusy(true);
    setError(null);
    try {
      const out = await api.post<ControlResult>("/owner/erase-data", { pin });
      if (out.ok) {
        toast("All your shop's data was deleted.", "ok");
        refresh();
        onClose();
      } else {
        setError(out.attempts_left != null ? `${out.message} (${out.attempts_left} tries left)` : out.message);
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setPin("");
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} eyebrow="Your data" title="Delete all your data?" wide>
      <div className="space-y-4">
        {!preview && !error && <Loading label="Counting what would be deleted" />}
        {preview && (
          <>
            <div className="rounded-2xl border border-bad/35 bg-bad-soft/50 p-4">
              <div className="label text-[10.5px] text-bad">This will be deleted</div>
              {preview.items.length ? (
                <ul className="mt-2 grid gap-1 text-sm sm:grid-cols-2">
                  {preview.items.map((i) => (
                    <li key={i.what} className="flex justify-between gap-3">
                      <span>{i.what}</span>
                      <span className="tabular font-medium">{i.count.toLocaleString("en-IN")}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-sm">Nothing: the books are already empty.</p>
              )}
              {preview.scheduled_payments > 0 && (
                <p className="mt-2 text-sm font-medium text-bad">
                  {preview.scheduled_payments} scheduled payment{preview.scheduled_payments !== 1 ? "s" : ""} will be cancelled.
                </p>
              )}
            </div>
            <Note tone="neutral" title="What stays">
              {preview.kept}
            </Note>
            <Note tone="warn">It cannot be undone. Export your data first if you want a copy.</Note>
            <div className="flex justify-start">
              <ExportDataButton size="sm" variant="ghost" />
            </div>
            <PinField value={pin} onChange={setPin} />
          </>
        )}
        {error && <Note tone="bad">{error}</Note>}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="danger" busy={busy} disabled={!preview || pin.length < 4} onClick={erase}>
            <Trash2 className="size-4" /> Delete everything
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export function DeleteDataButton({ size = "md" }: { size?: "sm" | "md" }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="dangerGhost" size={size} onClick={() => setOpen(true)}>
        <Trash2 className={size === "sm" ? "size-3.5" : "size-4"} /> Delete all my data
      </Button>
      <EraseDataDialog open={open} onClose={() => setOpen(false)} />
    </>
  );
}
