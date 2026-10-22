import { CheckCircle2, Link2, PackagePlus, Wallet, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Dialog, Notice, Spinner, TextInput } from "../components/ui";
import { api, ApiError, type PosProduct, type PosResult, type PosUnit, type QuickLine } from "../lib/api";
import { dateOnly, dateTime, naira } from "../lib/format";
import { ScanBox, UnitPicker, unitLabel } from "./Pos";

/** Sales → To review: items sold with “Quick sale” before they were in the catalogue (owner only). */
export function QuickReview({ onCount }: { onCount?: (n: number) => void }) {
  const [view, setView] = useState<"waiting" | "done">("waiting");
  const [rows, setRows] = useState<QuickLine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState<QuickLine | null>(null);

  const load = useCallback(() => {
    setRows(null);
    api.get<QuickLine[]>(`/quick-sales?reviewed=${view === "done"}`)
      .then((r) => { setRows(r); if (view === "waiting") onCount?.(r.length); })
      .catch((e) => setError(e.message));
  }, [view, onCount]);
  useEffect(load, [load]);

  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-muted">
        Items sold with <span className="font-medium text-ink">Quick sale</span> before they were added to your products.
        Until you review them, their cost counts as ₦0, so profit looks higher than it really is and stock hasn't gone down.
        For each one, pick the product it really was (its stock then goes down and its cost is used),
        or just enter what it cost you if you'll never stock it.
      </p>

      <div className="flex gap-2">
        {(["waiting", "done"] as const).map((v) => (
          <button key={v} type="button" onClick={() => { setFlash(null); setView(v); }} aria-pressed={view === v}
            className={`rounded-full border px-3 h-8 text-sm ${view === v ? "border-board bg-board text-white" : "border-line bg-white hover:border-board-300"}`}>
            {v === "waiting" ? "Waiting" : "Reviewed"}
          </button>
        ))}
      </div>

      {flash && <Notice tone="success">{flash}</Notice>}
      {error && <Notice tone="error">{error}</Notice>}
      {!rows ? <Spinner /> : !rows.length ? (
        <p className="rounded-lg border border-line bg-white px-4 py-8 text-center text-muted">
          {view === "waiting" ? <><CheckCircle2 className="mx-auto mb-2 size-6 text-ok" aria-hidden />Nothing to review. Every quick sale has been sorted out.</>
            : "No quick sales reviewed yet."}
        </p>
      ) : (
        <ul className="divide-y divide-line rounded-lg border border-line bg-white">
          {rows.map((r) => (
            <li key={r.line_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
              <div className="min-w-56 flex-1">
                <p className="font-medium">{r.quick_name} <span className="num text-muted font-normal">× {r.quantity}</span></p>
                <p className="text-sm text-muted">
                  {dateOnly(r.business_date)} · <Link to={`/sales/${r.sale_id}`} className="underline num">{r.receipt_no}</Link> · {r.sold_by_name}
                  {r.customer_name !== "Walk-in customer" && <> · {r.customer_name}</>}
                </p>
                {r.reviewed && (
                  <p className="mt-0.5 text-sm">
                    {r.linked_product_name ? <><Link2 className="inline size-3.5" aria-hidden /> {r.linked_product_name}</> : "Cost entered"}
                    {" · "}cost <span className="num">{naira(r.cost_total)}</span>
                    {" · "}profit <span className="num">{naira(Number(r.line_total) - Number(r.cost_total))}</span>
                    <span className="text-muted"> · {r.reviewed_by_name}, {dateTime(r.reviewed_at)}</span>
                  </p>
                )}
              </div>
              <div className="text-right">
                <p className="num font-medium">{naira(r.line_total)}</p>
                <p className="num text-sm text-muted">{r.quantity} × {naira(r.unit_price)}</p>
              </div>
              {!r.reviewed && <Button variant="secondary" onClick={() => { setFlash(null); setReviewing(r); }}>Review</Button>}
            </li>
          ))}
        </ul>
      )}

      <Dialog wide open={!!reviewing} title={reviewing ? `Review “${reviewing.quick_name}”` : ""} onClose={() => setReviewing(null)}>
        {reviewing && (
          <ReviewForm line={reviewing} onDone={(r) => {
            setReviewing(null);
            setFlash(`${r.quick_name} on ${r.receipt_no}: ${r.linked_product_name ? `linked to ${r.linked_product_name}` : "cost saved"}. ` +
              `Profit on it is ${naira(Number(r.line_total) - Number(r.cost_total))}.`);
            load();
          }} />
        )}
      </Dialog>
    </div>
  );
}

function ReviewForm({ line, onDone }: { line: QuickLine; onDone: (r: QuickLine) => void }) {
  const [mode, setMode] = useState<"product" | "cost">("product");
  const [product, setProduct] = useState<PosProduct | null>(null);
  const [unit, setUnit] = useState<PosUnit | null>(null);
  const [cost, setCost] = useState("");
  const [costError, setCostError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const serialized = product?.tracking_type === "serialized";
  const ready = mode === "cost" ? cost.trim() !== "" : !!product && (!serialized || !!unit);

  function pick(r: PosResult) {
    setError(null);
    setProduct(r.product);
    setUnit(r.unit ?? null);
  }

  async function save() {
    if (busy) return;
    setError(null);
    let body: Record<string, unknown>;
    if (mode === "cost") {
      const n = Number(cost.replaceAll(",", ""));
      if (!Number.isFinite(n) || n < 0) return setCostError("Enter a cost of ₦0 or more.");
      setCostError(null);
      body = { cost_each: n.toFixed(2) };
    } else {
      if (!product) return setError("Search for the product first.");
      if (serialized && !unit) return setError(`Pick which ${product.name} was sold.`);
      body = { product_id: product.id, ...(unit ? { unit_id: unit.id } : {}) };
    }
    setBusy(true);
    try {
      onDone(await api.post<QuickLine>(`/quick-sales/${line.line_id}/review`, body));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const costEach = Number(cost.replaceAll(",", "")) || 0;
  return (
    <div className="space-y-4">
      <p className="text-sm">
        Sold on <span className="font-medium">{dateOnly(line.business_date)}</span> ({line.receipt_no}):{" "}
        <span className="num">{line.quantity} × {naira(line.unit_price)} = {naira(line.line_total)}</span>
      </p>

      <div role="radiogroup" aria-label="How to review it" className="grid gap-2 sm:grid-cols-2">
        {([["product", "It's one of my products", "Stock goes down and its cost is used", Link2],
           ["cost", "Just enter what it cost me", "For a one-off item you won't stock", Wallet]] as const).map(([id, title, note, Icon]) => (
          <button key={id} type="button" role="radio" aria-checked={mode === id} onClick={() => { setError(null); setMode(id); }}
            className={`flex items-start gap-2.5 rounded-md border p-3 text-left ${mode === id ? "border-board ring-2 ring-pad/40" : "border-line hover:border-board-300"}`}>
            <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span><span className="block font-medium">{title}</span><span className="block text-sm text-muted">{note}</span></span>
          </button>
        ))}
      </div>

      {mode === "product" ? (
        <div className="space-y-3">
          {!product ? (
            <>
              <ScanBox onPick={pick} placeholder="Search your products, or scan the phone's IMEI" />
              <p className="flex items-start gap-1.5 text-sm text-muted">
                <PackagePlus className="mt-0.5 size-4 shrink-0" aria-hidden />
                <span>
                  Not in your products yet? <Link to="/products" target="_blank" className="text-board underline">Add it on the Products page</Link>{" "}
                  first, and count the {line.quantity} sold here in its stock. Then come back and search for it.
                </span>
              </p>
            </>
          ) : (
            <div className="flex items-center justify-between gap-3 rounded-md border border-line bg-paper px-3 py-2">
              <div>
                <p className="font-medium">{product.name}</p>
                <p className="text-sm text-muted num">
                  {unit ? unitLabel(unit) : product.tracking_type === "service" ? "Service"
                    : product.tracking_type === "serialized" ? "Phone / laptop" : `${product.qty_on_hand} in stock`}
                </p>
              </div>
              <button type="button" aria-label="Choose another product" onClick={() => { setProduct(null); setUnit(null); setError(null); }}
                className="rounded p-1.5 text-muted hover:text-ink"><X className="size-4" aria-hidden /></button>
            </div>
          )}
          {product && serialized && !unit && (
            line.quantity > 1 ? (
              <Notice tone="error">This quick sale was for {line.quantity} items, but each {product.name} is linked by its own IMEI/serial. Use “Just enter what it cost me” instead.</Notice>
            ) : (
              <div>
                <p className="mb-2 text-sm font-medium">Which {product.name} was sold?</p>
                <UnitPicker product={product} exclude={[]} onPick={setUnit} />
              </div>
            )
          )}
          {product && product.tracking_type === "quantity" && product.qty_on_hand < line.quantity && (
            <p className="text-sm text-alert">
              Only {product.qty_on_hand} in stock, but this sale took {line.quantity}. Add its stock first (counting the {line.quantity} sold), then link it.
            </p>
          )}
        </div>
      ) : (
        <div className="max-w-xs space-y-2">
          <TextInput label="What did one cost you? (₦)" inputMode="decimal" autoFocus value={cost}
            onChange={(e) => setCost(e.target.value)} error={costError ?? undefined} placeholder="0.00" />
          {cost.trim() !== "" && (
            <p className="text-sm text-muted">
              Profit on this sale: <span className="num font-medium text-ink">{naira(Number(line.line_total) - costEach * line.quantity)}</span>
            </p>
          )}
        </div>
      )}

      {error && <Notice tone="error">{error}</Notice>}
      <div className="flex items-center justify-end gap-3">
        <Badge tone="neutral">Can be done once</Badge>
        <Button onClick={save} loading={busy} disabled={!ready}>
          {mode === "cost" ? "Save cost" : "Link to this product"}
        </Button>
      </div>
    </div>
  );
}
