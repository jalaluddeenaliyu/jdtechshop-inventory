import { Download, FileSpreadsheet, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { Badge, Button, Notice } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { naira } from "../lib/format";

interface ImportedProduct {
  rows: number[];
  name: string;
  tracking_type: "quantity" | "serialized" | null;
  quantity: number;
  cost_each: string | null;
  selling_price: string | null;
  stock_value: string;
  category: string | null;
  brand: string | null;
  error: string | null;
}

interface ImportResult {
  saved: boolean;
  summary: {
    products: number;
    phones_and_laptops: number;
    items_in_stock: number;
    stock_value: string;
    new_categories: string[];
    new_brands: string[];
    rows_read: number;
  };
  problems: { rows: number[]; product: string | null; message: string }[];
  products: ImportedProduct[];
}

const rowLabel = (rows: number[]) =>
  rows.length === 1 ? `Row ${rows[0]}` : `Rows ${rows.slice(0, 4).join(", ")}${rows.length > 4 ? "…" : ""}`;

/** Download the template → fill it in → upload → check the preview → import. */
export function ImportProducts({ onDone }: { onDone: (message: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"check" | "save" | null>(null);
  const input = useRef<HTMLInputElement>(null);

  async function check(f: File) {
    setFile(f);
    setPreview(null);
    setError(null);
    setBusy("check");
    try {
      setPreview(await api.upload<ImportResult>("/product-import?commit=false", f));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't read that file. Try again.");
    } finally {
      setBusy(null);
      if (input.current) input.current.value = ""; // choosing the same file again re-checks it
    }
  }

  async function save() {
    if (!file || busy) return;
    setBusy("save");
    setError(null);
    try {
      const result = await api.upload<ImportResult>("/product-import?commit=true", file);
      if (result.saved) {
        const s = result.summary;
        onDone(`Imported ${s.products} ${s.products === 1 ? "product" : "products"} with ${s.items_in_stock} items in stock, worth ${naira(s.stock_value)} at cost.`);
      } else {
        setPreview(result); // something changed since the preview (e.g. a product was added meanwhile)
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Import failed. Nothing was saved.");
    } finally {
      setBusy(null);
    }
  }

  const s = preview?.summary;
  const problems = preview?.problems ?? [];

  return (
    <div className="space-y-5">
      <ol className="grid gap-3 sm:grid-cols-3 text-sm">
        <li className="rounded-md border border-line p-3">
          <p className="font-medium">1. Download the template</p>
          <p className="mt-0.5 text-muted">An Excel file with the right columns and an example sheet.</p>
          <a href="/api/product-import/template" download
            className="mt-2 inline-flex items-center gap-2 rounded-md border border-line bg-white px-3 h-9 font-medium text-board hover:border-board-300">
            <Download className="size-4" aria-hidden /> Download template
          </a>
        </li>
        <li className="rounded-md border border-line p-3">
          <p className="font-medium">2. Fill it in</p>
          <p className="mt-0.5 text-muted">
            Accessories: one row each, with quantity and cost. Phones and laptops: one row <em>per phone</em>, with its IMEI or serial and cost.
          </p>
        </li>
        <li className="rounded-md border border-line p-3">
          <p className="font-medium">3. Upload and check</p>
          <p className="mt-0.5 text-muted">You'll see a preview first. Nothing is saved until you confirm.</p>
          <input ref={input} type="file" accept=".xlsx,.xlsm,.csv" className="sr-only" id="import-file"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) check(f); }} />
          <Button className="mt-2 h-9" variant="secondary" loading={busy === "check"}
            onClick={() => input.current?.click()}>
            <Upload className="size-4" aria-hidden /> {file ? "Choose another file" : "Choose file"}
          </Button>
        </li>
      </ol>

      {error && <Notice tone="error">{error}</Notice>}

      {preview && s && (
        <>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <FileSpreadsheet className="size-4 text-muted" aria-hidden />
            <span className="font-medium">{file?.name}</span>
            <span className="text-muted">
              {s.rows_read} rows read · {s.products} ready
              {problems.length > 0 && <> · <span className="text-alert font-medium">{problems.length} to fix</span></>}
            </span>
          </div>

          {problems.length > 0 ? (
            <div className="rounded-md border border-alert/30 bg-alert-bg p-4 text-sm">
              <p className="font-medium text-alert">
                Fix {problems.length === 1 ? "this" : `these ${problems.length}`} in your file, save it, then choose it again. Nothing has been imported.
              </p>
              <ul className="mt-2 space-y-1 text-ink">
                {problems.slice(0, 50).map((p, i) => (
                  <li key={i}>
                    <span className="font-medium num">{rowLabel(p.rows)}</span>
                    {p.product && <span className="text-muted"> ({p.product})</span>}: {p.message}
                  </li>
                ))}
                {problems.length > 50 && <li className="text-muted">…and {problems.length - 50} more.</li>}
              </ul>
            </div>
          ) : (
            <div className="rounded-md bg-ok-bg p-4 text-sm text-ok">
              <p className="font-medium">Everything checks out.</p>
              <p className="text-ink">
                {s.products} {s.products === 1 ? "product" : "products"}, {s.items_in_stock} items in stock
                {s.phones_and_laptops > 0 && <> (including {s.phones_and_laptops} phones/laptops with IMEI or serial)</>},
                worth <span className="num font-medium">{naira(s.stock_value)}</span> at cost.
                {s.new_categories.length > 0 && <> New categories: {s.new_categories.join(", ")}.</>}
                {s.new_brands.length > 0 && <> New brands: {s.new_brands.join(", ")}.</>}
              </p>
            </div>
          )}

          <div className="max-h-80 overflow-auto rounded-lg border border-line">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-white border-b border-line text-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">Row</th>
                  <th className="px-3 py-2 font-medium">Product</th>
                  <th className="px-3 py-2 font-medium text-right">Selling price</th>
                  <th className="px-3 py-2 font-medium text-right">Cost each</th>
                  <th className="px-3 py-2 font-medium text-right">In stock</th>
                  <th className="hidden sm:table-cell px-3 py-2 font-medium text-right">Stock value</th>
                </tr>
              </thead>
              <tbody>
                {preview.products.map((p, i) => (
                  <tr key={i} className={`border-b border-line last:border-0 align-top ${p.error ? "bg-alert-bg/60" : ""}`}>
                    <td className="px-3 py-2 num text-muted whitespace-nowrap">{p.rows.length > 1 ? `${p.rows[0]}–${p.rows[p.rows.length - 1]}` : p.rows[0]}</td>
                    <td className="px-3 py-2">
                      <div className="font-medium">{p.name}</div>
                      <div className="text-muted">
                        {p.tracking_type === "serialized" && <Badge tone="pad">Serial/IMEI</Badge>}{" "}
                        {[p.category, p.brand].filter(Boolean).join(" · ")}
                      </div>
                      {p.error && <div className="text-alert">{p.error}</div>}
                    </td>
                    <td className="px-3 py-2 text-right num whitespace-nowrap">{p.selling_price ? naira(p.selling_price) : "—"}</td>
                    <td className="px-3 py-2 text-right num whitespace-nowrap">{p.error ? "—" : p.cost_each ? naira(p.cost_each) : "—"}</td>
                    <td className="px-3 py-2 text-right num">{p.error ? "—" : p.quantity}</td>
                    <td className="hidden sm:table-cell px-3 py-2 text-right num whitespace-nowrap">{p.error ? "—" : naira(p.stock_value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex justify-end">
            <Button onClick={save} loading={busy === "save"} disabled={problems.length > 0 || s.products === 0}>
              Import {s.products} {s.products === 1 ? "product" : "products"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
