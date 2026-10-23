import { FileSpreadsheet, History, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { Button, Notice } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { dateOnly, naira } from "../lib/format";

export interface SalesImportResult {
  saved: boolean;
  summary: {
    sales: number;
    items: number;
    date_from: string | null;
    date_to: string | null;
    total: string;
    paid: string;
    owed: string;
    products_used: number;
    new_products: string[];
    products_without_cost: number;
    new_customers: string[];
    customers_owing: { name: string; owed: string }[];
  };
  problems: { where: string; message: string }[];
}

/** Upload the old-sales workbook → check the preview → import (history only, stock untouched). */
export function ImportSales({ onDone }: { onDone: (r: SalesImportResult) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<SalesImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"check" | "save" | null>(null);
  const input = useRef<HTMLInputElement>(null);

  async function check(f: File) {
    setFile(f);
    setPreview(null);
    setError(null);
    setBusy("check");
    try {
      setPreview(await api.upload<SalesImportResult>("/sales-import?commit=false", f));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't read that file. Try again.");
    } finally {
      setBusy(null);
      if (input.current) input.current.value = "";
    }
  }

  async function save() {
    if (!file || busy) return;
    setBusy("save");
    setError(null);
    try {
      const r = await api.upload<SalesImportResult>("/sales-import?commit=true", file);
      if (r.saved) onDone(r);
      else setPreview(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Import failed. Nothing was saved.");
    } finally {
      setBusy(null);
    }
  }

  const s = preview?.summary;
  const problems = preview?.problems ?? [];
  return (
    <div className="space-y-4">
      <p className="flex items-start gap-2 text-sm text-muted">
        <History className="mt-0.5 size-4 shrink-0" aria-hidden />
        <span>
          Bring in sales from your old app (the workbook Claude prepared from Vyapar). Each sale goes in on its own date,
          with its customer, payments and anything still owed. <span className="font-medium text-ink">Your stock is not changed</span> —
          add today's stock separately. Missing products and customers are created. You'll see a check first; nothing is saved until you confirm.
        </span>
      </p>

      <input ref={input} type="file" accept=".xlsx,.xlsm" className="sr-only" id="sales-import-file"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) check(f); }} />
      <Button variant="secondary" loading={busy === "check"} onClick={() => input.current?.click()}>
        <Upload className="size-4" aria-hidden /> {file ? "Choose another file" : "Choose the Excel file"}
      </Button>

      {error && <Notice tone="error">{error}</Notice>}

      {preview && s && (
        <>
          <p className="flex items-center gap-2 text-sm">
            <FileSpreadsheet className="size-4 text-muted" aria-hidden /><span className="font-medium">{file?.name}</span>
          </p>
          {problems.length > 0 ? (
            <div className="rounded-md border border-alert/30 bg-alert-bg p-4 text-sm">
              <p className="font-medium text-alert">
                Fix {problems.length === 1 ? "this" : `these ${problems.length}`} in the file, save it, then choose it again. Nothing has been imported.
              </p>
              <ul className="mt-2 space-y-1">
                {problems.slice(0, 50).map((p, i) => <li key={i}><span className="font-medium">{p.where}</span>: {p.message}</li>)}
                {problems.length > 50 && <li className="text-muted">…and {problems.length - 50} more.</li>}
              </ul>
            </div>
          ) : (
            <div className="rounded-md bg-ok-bg p-4 text-sm">
              <p className="font-medium text-ok">Everything checks out.</p>
              <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-4">
                <Fig label="Sales" value={String(s.sales)} />
                <Fig label="Dates" value={s.date_from ? `${dateOnly(s.date_from)} – ${dateOnly(s.date_to)}` : "—"} />
                <Fig label="Total" value={naira(s.total)} />
                <Fig label="Paid" value={naira(s.paid)} />
              </dl>
              <ul className="mt-3 space-y-1 text-ink">
                <li>
                  {s.products_used} products sold{s.new_products.length > 0 && <>, {s.new_products.length} new — they'll be added to your products with no stock</>}.
                  {s.products_without_cost > 0 && <> {s.products_without_cost} have no cost price yet, so profit on those counts their cost as ₦0.</>}
                </li>
                {s.new_customers.length > 0 && (
                  <li>{s.new_customers.length} new {s.new_customers.length === 1 ? "customer" : "customers"}: {s.new_customers.slice(0, 12).join(", ")}
                    {s.new_customers.length > 12 && <> and {s.new_customers.length - 12} more</>}.</li>
                )}
                {s.customers_owing.length > 0 && (
                  <li>Still owing {naira(s.owed)}: {s.customers_owing.map((c) => `${c.name} ${naira(c.owed)}`).join(" · ")}.</li>
                )}
              </ul>
            </div>
          )}
          <div className="flex justify-end">
            <Button onClick={save} loading={busy === "save"} disabled={problems.length > 0 || s.sales === 0}>
              Import {s.sales} {s.sales === 1 ? "sale" : "sales"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

function Fig({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-muted">{label}</dt>
      <dd className="num font-medium text-ink">{value}</dd>
    </div>
  );
}
