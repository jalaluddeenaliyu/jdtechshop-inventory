import { ArrowLeft, Banknote, Plus, RotateCcw, Search, Send, Trash2, Undo2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { flushSync } from "react-dom";
import { Badge, Button, Dialog, Notice, PageHeader, Panel, SelectInput, Spinner, TextArea, TextInput } from "../components/ui";
import {
  api,
  ApiError,
  newKey,
  qs,
  type Condition,
  type Page,
  type Product,
  type Purchase,
  type Supplier,
  type SupplierPayment,
  type TrackingType,
} from "../lib/api";
import { dateOnly, dateTime, naira, todayISO } from "../lib/format";
import { ReverseForm, SupplierReturnForm } from "./Corrections";
import { statusBadge } from "./Purchases";
import { PaymentForm, SupplierForm } from "./Suppliers";

/** /purchases/new and /purchases/:id — edit a draft, or view a posted purchase. */
export function PurchaseEditorPage() {
  const { id } = useParams();
  const isNew = id === "new";
  const [purchase, setPurchase] = useState<Purchase | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (isNew) return;
    setError(null);
    api.get<Purchase>(`/purchases/${id}`).then(setPurchase).catch((e) => setError(e.message));
  }, [id, isNew]);
  useEffect(load, [load]);

  if (error) {
    return (
      <>
        <BackLink />
        <Notice tone="error">{error}</Notice>
      </>
    );
  }
  if (!isNew && !purchase) return <Spinner />;
  if (purchase && purchase.status !== "draft") return <PostedPurchase purchase={purchase} onChanged={load} />;
  return <DraftEditor key={purchase?.id ?? "new"} purchase={purchase} onPosted={setPurchase} />;
}

function BackLink() {
  return (
    <Link to="/purchases" className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink">
      <ArrowLeft className="size-4" aria-hidden /> All purchases
    </Link>
  );
}

// ---------------- Draft editor ----------------

type UnitRow = { key: number; serial_number: string; imei1: string; imei2: string; condition: Condition };
type LineRow = {
  key: number;
  product: { id: number; name: string; sku: string; tracking_type: TrackingType };
  quantity: string;
  unit_cost: string;
  units: UnitRow[];
};

let nextKey = 1;
const blankUnit = (condition: Condition = "new"): UnitRow =>
  ({ key: nextKey++, serial_number: "", imei1: "", imei2: "", condition });
const unitIsBlank = (u: UnitRow) => !u.serial_number.trim() && !u.imei1.trim() && !u.imei2.trim();
const money = (s: string) => Number(s.replaceAll(",", "").trim());

function lineQuantity(line: LineRow): number {
  return line.product.tracking_type === "serialized"
    ? line.units.filter((u) => !unitIsBlank(u)).length
    : Number(line.quantity) || 0;
}

/** Same rule as the server: additional costs spread by line value, in kobo, last line takes the remainder. */
function previewLanded(lines: LineRow[], additional: number): number[] {
  const values = lines.map((l) => Math.round(lineQuantity(l) * (money(l.unit_cost) || 0) * 100));
  const total = values.reduce((a, b) => a + b, 0);
  const weights = total > 0 ? values : lines.map((l) => lineQuantity(l));
  const sum = weights.reduce((a, b) => a + b, 0);
  const extra = Math.round(additional * 100);
  let given = 0;
  return lines.map((l, i) => {
    const share = i === lines.length - 1 ? extra - given : sum ? Math.round((extra * weights[i]) / sum) : 0;
    given += share;
    const qty = lineQuantity(l);
    return qty ? (money(l.unit_cost) || 0) + share / 100 / qty : 0;
  });
}

function fromPurchase(p: Purchase | null): LineRow[] {
  return (p?.lines ?? []).map((l) => ({
    key: nextKey++,
    product: { id: l.product_id, name: l.product_name, sku: l.product_sku, tracking_type: l.tracking_type },
    quantity: String(l.quantity),
    unit_cost: String(Number(l.unit_cost)),
    units: l.tracking_type === "serialized"
      ? [...l.units.map((u) => ({
          key: nextKey++, serial_number: u.serial_number ?? "", imei1: u.imei1 ?? "", imei2: u.imei2 ?? "",
          condition: u.condition,
        })), blankUnit()]
      : [],
  }));
}

function DraftEditor({ purchase, onPosted }: { purchase: Purchase | null; onPosted: (p: Purchase) => void }) {
  const navigate = useNavigate();
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [header, setHeader] = useState({
    supplier_id: purchase ? String(purchase.supplier_id) : "",
    supplier_invoice_no: purchase?.supplier_invoice_no ?? "",
    purchase_date: purchase?.purchase_date ?? todayISO(),
    additional_costs: purchase ? String(Number(purchase.additional_costs) || "") : "",
    notes: purchase?.notes ?? "",
  });
  const [lines, setLines] = useState<LineRow[]>(() => fromPurchase(purchase));
  const [createKey] = useState(newKey); // a double-clicked first save creates one draft
  const [error, setError] = useState<string | null>(null);
  const [lineErrors, setLineErrors] = useState<Record<number, string>>({});
  const [flash, setFlash] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "post" | "discard" | null>(null);
  const [addingSupplier, setAddingSupplier] = useState(false);
  const linesRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.get<Supplier[]>("/suppliers").then(setSuppliers).catch(() => {});
  }, []);

  // "Restock" on the Products page opens a new purchase with that product already added.
  const [params] = useSearchParams();
  const restockId = !purchase ? params.get("product") : null;
  const restockDone = useRef(false);
  useEffect(() => {
    if (!restockId || !/^\d+$/.test(restockId) || restockDone.current) return;
    restockDone.current = true; // add it once, even if this effect runs twice
    api.get<Product>(`/products/${restockId}`).then(addProduct).catch(() => {});
  }, [restockId]); // eslint-disable-line react-hooks/exhaustive-deps

  const additional = money(header.additional_costs) || 0;
  const landed = previewLanded(lines, additional);
  const subtotal = lines.reduce((sum, l) => sum + Math.round(lineQuantity(l) * (money(l.unit_cost) || 0) * 100), 0) / 100;

  function addProduct(p: Product) {
    setLines((ls) => [...ls, {
      key: nextKey++,
      product: { id: p.id, name: p.name, sku: p.sku, tracking_type: p.tracking_type },
      quantity: p.tracking_type === "quantity" ? "1" : "",
      unit_cost: "",
      units: p.tracking_type === "serialized" ? [blankUnit()] : [],
    }]);
    setTimeout(() => {
      const rows = linesRef.current?.querySelectorAll<HTMLElement>("[data-line]");
      rows?.[rows.length - 1]?.querySelector<HTMLInputElement>("input")?.focus();
    }, 0);
  }

  function updateLine(key: number, patch: Partial<LineRow>) {
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  }

  function body() {
    return {
      ...header,
      additional_costs: header.additional_costs.replaceAll(",", "").trim() || "0",
      lines: lines.map((l) => ({
        product_id: l.product.id,
        quantity: lineQuantity(l),
        unit_cost: l.unit_cost.replaceAll(",", "").trim(),
        units: l.units.filter((u) => !unitIsBlank(u)).map(({ serial_number, imei1, imei2, condition }) =>
          ({ serial_number, imei1, imei2, condition })),
      })),
      ...(purchase ? {} : { idempotency_key: createKey }),
    };
  }

  function check(): boolean {
    const problems: Record<number, string> = {};
    lines.forEach((l) => {
      if (!l.unit_cost.trim() || !(money(l.unit_cost) >= 0)) problems[l.key] = "Enter what each one cost.";
      if (l.product.tracking_type === "serialized" && lineQuantity(l) === 0) {
        problems[l.key] = "Scan or type the serial/IMEI of each unit.";
      }
      if (l.product.tracking_type === "quantity" && !(Number.isInteger(Number(l.quantity)) && Number(l.quantity) >= 1)) {
        problems[l.key] = "Enter how many: a whole number, 1 or more.";
      }
    });
    setLineErrors(problems);
    if (!header.supplier_id) {
      setError("Choose the supplier.");
      return false;
    }
    return !Object.keys(problems).length;
  }

  function showError(err: unknown) {
    if (!(err instanceof ApiError)) {
      setError("Could not save. Try again.");
      return;
    }
    // "Line 2, unit 1: …" messages and "lines.1.…" field errors go next to the line they belong to.
    const byLine: Record<number, string> = {};
    const m = err.message.match(/^Line (\d+)(?:, unit (\d+))?: (.*)$/);
    if (m && lines[Number(m[1]) - 1]) {
      byLine[lines[Number(m[1]) - 1].key] = (m[2] ? `Unit ${m[2]}: ` : "") + m[3];
    }
    for (const [field, message] of Object.entries(err.fieldErrors)) {
      const f = field.match(/^lines\.(\d+)(?:\.units\.(\d+))?/);
      if (f && lines[Number(f[1])]) byLine[lines[Number(f[1])].key] ??= (f[2] ? `Unit ${Number(f[2]) + 1}: ` : "") + message;
    }
    setLineErrors(byLine);
    setError(Object.keys(byLine).length ? "Please fix the highlighted lines." : err.message);
  }

  async function save(): Promise<Purchase | null> {
    const saved = purchase
      ? await api.put<Purchase>(`/purchases/${purchase.id}`, body())
      : await api.post<Purchase>("/purchases", body());
    if (!purchase) navigate(`/purchases/${saved.id}`, { replace: true });
    return saved;
  }

  async function onSave() {
    if (busy || !check()) return;
    setBusy("save");
    setError(null);
    setFlash(null);
    try {
      const saved = await save();
      if (purchase && saved) setFlash(`Draft ${saved.reference} saved.`);
    } catch (err) {
      showError(err);
    } finally {
      setBusy(null);
    }
  }

  async function onPost() {
    if (busy || !check()) return;
    if (!lines.length) {
      setError("Add at least one product before posting.");
      return;
    }
    const items = lines.reduce((n, l) => n + lineQuantity(l), 0);
    if (!window.confirm(
      `Post this purchase? ${items} item(s) will be added to stock and ${naira(subtotal + additional)} will be owed to the supplier. ` +
      "A posted purchase cannot be edited.",
    )) return;
    setBusy("post");
    setError(null);
    try {
      const saved = await save();
      if (saved) onPosted(await api.post<Purchase>(`/purchases/${saved.id}/post`));
    } catch (err) {
      showError(err);
    } finally {
      setBusy(null);
    }
  }

  async function onDiscard() {
    if (!purchase || busy) return;
    if (!window.confirm(`Discard draft ${purchase.reference}? It has not changed any stock.`)) return;
    setBusy("discard");
    try {
      await api.del(`/purchases/${purchase.id}`);
      navigate("/purchases");
    } catch (err) {
      showError(err);
      setBusy(null);
    }
  }

  return (
    <>
      <BackLink />
      <PageHeader
        title={purchase ? `Purchase ${purchase.reference}` : "New purchase"}
        description="Nothing changes in stock until you post. You can save a draft and finish it later."
      />
      {flash && <div className="mb-4"><Notice tone="success">{flash}</Notice></div>}
      {error && <div className="mb-4"><Notice tone="error">{error}</Notice></div>}

      <div className="space-y-5">
        <Panel>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <SelectInput label="Supplier" value={header.supplier_id}
              onChange={(e) => setHeader({ ...header, supplier_id: e.target.value })}>
              <option value="">Choose…</option>
              {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              {purchase && !suppliers.some((s) => s.id === purchase.supplier_id) && (
                <option value={purchase.supplier_id}>{purchase.supplier_name} (turned off)</option>
              )}
            </SelectInput>
            <TextInput label="Supplier's invoice no." value={header.supplier_invoice_no}
              onChange={(e) => setHeader({ ...header, supplier_invoice_no: e.target.value })} />
            <TextInput label="Date received" type="date" max={todayISO()} value={header.purchase_date}
              onChange={(e) => setHeader({ ...header, purchase_date: e.target.value })} />
            <TextInput label="Transport & other costs (₦)" inputMode="decimal" placeholder="0.00"
              value={header.additional_costs} onChange={(e) => setHeader({ ...header, additional_costs: e.target.value })}
              hint="Shared across the items by value." />
          </div>
          <p className="mt-3 text-sm text-muted">
            {!suppliers.length && "No suppliers yet. "}
            <button type="button" className="text-board underline underline-offset-2 hover:text-board-600"
              onClick={() => setAddingSupplier(true)}>
              + Add a new supplier
            </button>
            {" "}without leaving this page.
          </p>
          <Dialog open={addingSupplier} title="New supplier" onClose={() => setAddingSupplier(false)}>
            {addingSupplier && (
              <SupplierForm onDone={(s) => {
                setSuppliers((list) => [...list, s].sort((a, b) => a.name.localeCompare(b.name)));
                setHeader((h) => ({ ...h, supplier_id: String(s.id) }));
                setAddingSupplier(false);
                setFlash(`Added supplier ${s.name}.`);
              }} />
            )}
          </Dialog>
        </Panel>

        <Panel title="Items">
          <div ref={linesRef} className="space-y-4">
            {lines.map((l, i) => (
              <LineEditor key={l.key} n={i + 1} line={l} landed={landed[i]} error={lineErrors[l.key]}
                onChange={(patch) => updateLine(l.key, patch)}
                onRemove={() => setLines((ls) => ls.filter((x) => x.key !== l.key))} />
            ))}
            <ProductPicker onPick={addProduct} />
          </div>
        </Panel>

        <Panel>
          <div className="grid gap-5 md:grid-cols-[1fr_18rem]">
            <TextArea label="Notes" rows={3} value={header.notes} onChange={(e) => setHeader({ ...header, notes: e.target.value })} />
            <dl className="space-y-1.5 text-[15px]">
              <div className="flex justify-between"><dt className="text-muted">Items</dt><dd className="num">{naira(subtotal)}</dd></div>
              <div className="flex justify-between"><dt className="text-muted">Transport & other</dt><dd className="num">{naira(additional)}</dd></div>
              <div className="flex justify-between border-t border-line pt-1.5 font-semibold"><dt>Total owed</dt><dd className="num">{naira(subtotal + additional)}</dd></div>
            </dl>
          </div>
        </Panel>

        <div className="flex flex-wrap justify-between gap-3">
          <div>
            {purchase && (
              <Button variant="ghost" className="text-alert" onClick={onDiscard} loading={busy === "discard"}>
                <Trash2 className="size-4" aria-hidden /> Discard draft
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onSave} loading={busy === "save"} disabled={!!busy}>Save draft</Button>
            <Button onClick={onPost} loading={busy === "post"} disabled={!!busy}>
              <Send className="size-4" aria-hidden /> Post purchase
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}

const cell = "block w-full h-9 rounded-md border border-line bg-white px-2.5 text-[15px] focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40";

function LineEditor({
  n, line, landed, error, onChange, onRemove,
}: {
  n: number;
  line: LineRow;
  landed: number;
  error?: string;
  onChange: (patch: Partial<LineRow>) => void;
  onRemove: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const serialized = line.product.tracking_type === "serialized";
  const qty = lineQuantity(line);

  // Enter moves to the next text box (scanner-friendly); from IMEI 2 it starts a new unit row.
  // Dropdowns are skipped: Enter on a dropdown is unreliable across browsers.
  function onEnter(e: KeyboardEvent<HTMLElement>) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const fields = Array.from(box.current?.querySelectorAll<HTMLElement>("[data-unit] input") ?? []);
    const i = fields.indexOf(e.currentTarget);
    if (i >= 0 && i < fields.length - 1) fields[i + 1].focus();
    else {
      // Render the new row now and move into it before the next keystroke can arrive.
      flushSync(() => onChange({ units: [...line.units, blankUnit(line.units.at(-1)?.condition)] }));
      const rows = box.current?.querySelectorAll<HTMLElement>("[data-unit]");
      rows?.[rows.length - 1]?.querySelector<HTMLInputElement>("input")?.focus();
    }
  }

  function setUnit(key: number, patch: Partial<UnitRow>) {
    onChange({ units: line.units.map((u) => (u.key === key ? { ...u, ...patch } : u)) });
  }

  return (
    <div ref={box} data-line className={`rounded-md border p-3 ${error ? "border-alert" : "border-line"}`}>
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex-1 min-w-48">
          <p className="font-medium"><span className="text-muted num">{n}.</span> {line.product.name}</p>
          <p className="text-sm text-muted num">{line.product.sku} · {serialized ? "Serial / IMEI" : "Quantity"}</p>
        </div>
        <label className="w-24">
          <span className="block text-sm text-muted">Quantity</span>
          {serialized ? (
            <span className="block h-9 leading-9 num font-medium" aria-live="polite">{qty}</span>
          ) : (
            <input className={`${cell} num`} type="number" min={1} step={1} inputMode="numeric" value={line.quantity}
              onChange={(e) => onChange({ quantity: e.target.value })} />
          )}
        </label>
        <label className="w-36">
          <span className="block text-sm text-muted">Cost each (₦)</span>
          <input className={`${cell} num`} inputMode="decimal" placeholder="0.00" value={line.unit_cost}
            onChange={(e) => onChange({ unit_cost: e.target.value })} />
        </label>
        <div className="w-36 text-right">
          <span className="block text-sm text-muted">Landed each</span>
          <span className="block h-9 leading-9 num">{landed ? naira(landed) : "—"}</span>
        </div>
        <button type="button" onClick={onRemove} aria-label={`Remove ${line.product.name}`}
          className="mt-5 rounded p-1.5 text-muted hover:text-alert">
          <Trash2 className="size-4" aria-hidden />
        </button>
      </div>

      {serialized && (
        <div className="mt-3 space-y-2">
          <div className="hidden md:grid grid-cols-[1.5rem_1fr_minmax(10rem,1fr)_minmax(10rem,1fr)_8rem_2rem] gap-2 text-sm text-muted">
            <span>#</span><span>Serial number</span><span>IMEI 1</span><span>IMEI 2</span><span>Condition</span><span />
          </div>
          {line.units.map((u, i) => (
            <div key={u.key} data-unit
              className="grid grid-cols-2 md:grid-cols-[1.5rem_1fr_minmax(10rem,1fr)_minmax(10rem,1fr)_8rem_2rem] gap-2 items-center">
              <span className="num text-sm text-muted col-span-2 md:col-span-1">{i + 1}</span>
              <input aria-label={`Line ${n} unit ${i + 1} serial number`} className={`${cell} col-span-2 md:col-span-1`}
                placeholder="Serial" spellCheck={false} autoCapitalize="characters" value={u.serial_number}
                onKeyDown={onEnter} onChange={(e) => setUnit(u.key, { serial_number: e.target.value })} />
              <input aria-label={`Line ${n} unit ${i + 1} IMEI 1`} className={`${cell} num`} inputMode="numeric" placeholder="IMEI 1"
                value={u.imei1} onKeyDown={onEnter} onChange={(e) => setUnit(u.key, { imei1: e.target.value })} />
              <input aria-label={`Line ${n} unit ${i + 1} IMEI 2`} className={`${cell} num`} inputMode="numeric" placeholder="IMEI 2"
                value={u.imei2} onKeyDown={onEnter} onChange={(e) => setUnit(u.key, { imei2: e.target.value })} />
              <select aria-label={`Line ${n} unit ${i + 1} condition`} className={cell} value={u.condition}
                onChange={(e) => setUnit(u.key, { condition: e.target.value as Condition })}>
                <option value="new">New</option>
                <option value="used">Used</option>
                <option value="refurbished">Refurbished</option>
              </select>
              <button type="button" aria-label={`Remove unit ${i + 1}`} disabled={line.units.length === 1}
                onClick={() => onChange({ units: line.units.filter((x) => x.key !== u.key) })}
                className="justify-self-end md:justify-self-center rounded p-1.5 text-muted hover:text-alert disabled:opacity-30">
                <Trash2 className="size-4" aria-hidden />
              </button>
            </div>
          ))}
          <Button type="button" variant="ghost" className="h-8"
            onClick={() => onChange({ units: [...line.units, blankUnit(line.units.at(-1)?.condition)] })}>
            <Plus className="size-4" aria-hidden /> Add unit
          </Button>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-alert">{error}</p>}
    </div>
  );
}

function ProductPicker({ onPick }: { onPick: (p: Product) => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Product[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (!q.trim()) {
      setResults([]);
      return;
    }
    const t = setTimeout(() => {
      api.get<Page<Product>>(`/products${qs({ q: q.trim(), page_size: 8 })}`)
        .then((r) => { setResults(r.items); setActive(0); setOpen(true); }).catch(() => {});
    }, 200);
    return () => clearTimeout(t);
  }, [q]);

  function pick(p: Product) {
    onPick(p);
    setQ("");
    setResults([]);
    setOpen(false);
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    if (e.key === "Enter") { e.preventDefault(); if (results[active]) pick(results[active]); }
    if (e.key === "Escape") setOpen(false);
  }

  return (
    <div className="relative max-w-xl">
      <label htmlFor="product-picker" className="block text-sm font-medium mb-1.5">Add a product</label>
      <Search className="pointer-events-none absolute left-3 top-[2.45rem] size-4 text-muted" aria-hidden />
      <input id="product-picker" role="combobox" aria-expanded={open && results.length > 0} aria-controls="product-picker-list"
        aria-autocomplete="list" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey}
        onBlur={() => setTimeout(() => setOpen(false), 150)} onFocus={() => results.length && setOpen(true)}
        placeholder="Type a product name, SKU or barcode"
        className="block w-full h-10 rounded-md border border-line bg-white pl-9 pr-3 text-[15px] placeholder:text-muted/70
          focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40" />
      {open && q.trim() && (
        <ul id="product-picker-list" role="listbox"
          className="absolute z-10 mt-1 w-full overflow-hidden rounded-md border border-line bg-white shadow-lg">
          {results.map((p, i) => (
            <li key={p.id} role="option" aria-selected={i === active}
              onMouseDown={(e) => { e.preventDefault(); pick(p); }} onMouseEnter={() => setActive(i)}
              className={`cursor-pointer px-3 py-2 ${i === active ? "bg-board/5" : ""}`}>
              <span className="font-medium">{p.name}</span>
              <span className="ml-2 text-sm text-muted num">{p.sku} · {p.tracking_type === "serialized" ? "Serial / IMEI" : "Quantity"} · {p.qty_on_hand} in stock</span>
            </li>
          ))}
          {!results.length && (
            <li className="px-3 py-2 text-sm text-muted">
              No product found. <Link to="/products" className="text-board underline">Add it on the Products page</Link> first.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

// ---------------- Posted purchase (read-only) ----------------

function PostedPurchase({ purchase, onChanged }: { purchase: Purchase; onChanged: () => void }) {
  const [payments, setPayments] = useState<Page<SupplierPayment> | null>(null);
  const [supplier, setSupplier] = useState<Supplier | null>(null);
  const [paying, setPaying] = useState(false);
  const [action, setAction] = useState<"return" | "reverse" | { reversePayment: SupplierPayment } | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const posted = purchase.status === "posted";

  useEffect(() => {
    api.get<Page<SupplierPayment>>(`/supplier-payments${qs({ purchase_id: purchase.id, page_size: 100 })}`)
      .then(setPayments).catch(() => {});
    api.get<Supplier>(`/suppliers/${purchase.supplier_id}`).then(setSupplier).catch(() => {});
  }, [purchase.id, purchase.supplier_id, purchase.amount_paid, reload]);

  function changed(message: string) {
    setAction(null);
    setFlash(message);
    setReload((n) => n + 1);
    onChanged();
  }

  return (
    <>
      <BackLink />
      <PageHeader
        title={`Purchase ${purchase.reference}`}
        description={`From ${purchase.supplier_name}${purchase.supplier_invoice_no ? ` · invoice ${purchase.supplier_invoice_no}` : ""} · received ${dateOnly(purchase.purchase_date)}`}
        actions={posted && (
          <>
            <Button variant="secondary" onClick={() => { setFlash(null); setAction("return"); }}>
              <RotateCcw className="size-4" aria-hidden /> Return to supplier
            </Button>
            <Button variant="secondary" className="text-alert" onClick={() => { setFlash(null); setAction("reverse"); }}>
              <Undo2 className="size-4" aria-hidden /> Reverse
            </Button>
            {Number(purchase.balance_due) > 0 && supplier && (
              <Button onClick={() => { setFlash(null); setPaying(true); }}>
                <Banknote className="size-4" aria-hidden /> Record payment
              </Button>
            )}
          </>
        )}
      />
      {flash && <div className="mb-4"><Notice tone="success">{flash}</Notice></div>}
      <div className="mb-4 flex flex-wrap items-center gap-3 text-sm text-muted">
        {statusBadge(purchase)}
        <span>Posted {dateTime(purchase.posted_at)} by {purchase.posted_by_name ?? "—"}</span>
      </div>
      {purchase.status === "reversed" && (
        <div className="mb-4"><Notice tone="error">
          Reversed on {dateTime(purchase.reversed_at)}: “{purchase.reversal_reason}”. Its stock was taken back out and nothing is owed for it.
        </Notice></div>
      )}

      <div className={`grid gap-3 mb-5 ${Number(purchase.returned_credit) > 0 ? "sm:grid-cols-4" : "sm:grid-cols-3"}`}>
        {([["Total", purchase.total],
           ...(Number(purchase.returned_credit) > 0 ? [["Returned (credit)", purchase.returned_credit] as const] : []),
           ["Paid", purchase.amount_paid], ["Still owed", purchase.balance_due]] as const).map(([label, v]) => (
          <div key={label} className="rounded-lg border border-line bg-white px-4 py-3">
            <p className="text-sm text-muted">{label}</p>
            <p className={`num text-xl font-semibold ${label === "Still owed" && Number(v) > 0 ? "text-alert" : ""}`}>{naira(v)}</p>
          </div>
        ))}
      </div>

      <div className="relative overflow-x-auto rounded-lg border border-line bg-white mb-5">
        <table className="w-full text-left text-[15px]">
          <thead className="border-b border-line text-sm text-muted">
            <tr>
              <th className="px-4 py-3 font-medium">Item</th>
              <th className="px-4 py-3 font-medium text-right">Qty</th>
              <th className="px-4 py-3 font-medium text-right">Cost each</th>
              <th className="px-4 py-3 font-medium text-right">Landed each</th>
              <th className="px-4 py-3 font-medium text-right">Line total</th>
            </tr>
          </thead>
          <tbody>
            {purchase.lines?.map((l) => (
              <tr key={l.id} className="border-b border-line last:border-0 align-top">
                <td className="px-4 py-3 min-w-48">
                  <div className="font-medium">{l.product_name}</div>
                  <div className="text-sm text-muted num">{l.product_sku}</div>
                  {l.units.length > 0 && (
                    <ul className="mt-1 text-sm text-muted num">
                      {l.units.map((u, i) => (
                        <li key={i}>{[u.imei1, u.imei2, u.serial_number].filter(Boolean).join(" · ")}{u.condition !== "new" ? ` (${u.condition})` : ""}</li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className="px-4 py-3 text-right num">{l.quantity}</td>
                <td className="px-4 py-3 text-right num whitespace-nowrap">{naira(l.unit_cost)}</td>
                <td className="px-4 py-3 text-right num whitespace-nowrap">{naira(l.landed_unit_cost)}</td>
                <td className="px-4 py-3 text-right num whitespace-nowrap">{naira(l.line_total)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot className="border-t border-line text-[15px]">
            <tr><td colSpan={4} className="px-4 py-2 text-right text-muted">Transport & other costs</td><td className="px-4 py-2 text-right num">{naira(purchase.additional_costs)}</td></tr>
            <tr><td colSpan={4} className="px-4 py-2 text-right font-semibold">Total</td><td className="px-4 py-2 text-right num font-semibold">{naira(purchase.total)}</td></tr>
          </tfoot>
        </table>
      </div>
      {purchase.notes && <p className="mb-5 text-muted whitespace-pre-line">{purchase.notes}</p>}

      <Panel title="Payments for this purchase">
        {!payments ? <Spinner /> : payments.items.length ? (
          <ul className="divide-y divide-line">
            {payments.items.map((p) => (
              <li key={p.id} className="flex flex-wrap justify-between gap-2 py-2">
                <span><span className="num">{dateOnly(p.paid_on)}</span> <span className="text-sm text-muted">· {p.method_name}{p.reference ? ` · ${p.reference}` : ""} · by {p.recorded_by_name}</span></span>
                <span className="flex items-center gap-2">
                  <span className={`num font-medium ${p.reversed ? "line-through text-muted" : ""}`}>{naira(p.amount)}</span>
                  {p.reversed ? <Badge tone="off">Reversed: {p.reversal_reason}</Badge> : (
                    <Button variant="ghost" className="h-8 text-alert" onClick={() => { setFlash(null); setAction({ reversePayment: p }); }}>Reverse</Button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        ) : <p className="text-muted">No payments recorded against this purchase yet.</p>}
      </Panel>

      {!!purchase.returns?.length && (
        <div className="mt-5"><Panel title="Returned to the supplier">
          <ul className="divide-y divide-line">
            {purchase.returns.map((r) => (
              <li key={r.id} className="flex flex-wrap justify-between gap-2 py-2 text-sm">
                <span><span className="font-medium">{dateTime(r.returned_at)}</span> <span className="text-muted">· {r.items.join(", ")} — “{r.reason}”</span></span>
                <span className="num font-medium">Credit {naira(r.credit_total)}</span>
              </li>
            ))}
          </ul>
        </Panel></div>
      )}

      <Dialog wide open={action === "return"} title={`Return goods from ${purchase.reference}`} onClose={() => setAction(null)}>
        {action === "return" && <SupplierReturnForm purchase={purchase} onDone={(p) => changed(`Returned to ${p.supplier_name}. They credit you ${naira(p.returns?.at(-1)?.credit_total ?? 0)}.`)} />}
      </Dialog>
      <Dialog open={action === "reverse"} title={`Reverse ${purchase.reference}`} onClose={() => setAction(null)}>
        {action === "reverse" && (
          <ReverseForm<Purchase> endpoint={`/purchases/${purchase.id}/reverse`} confirmLabel="Reverse purchase"
            explain="Only for a purchase entered by mistake. All its stock must still be in the shop and nothing paid or returned on it. For goods going back to the supplier, use “Return to supplier”."
            onDone={() => changed("Purchase reversed.")} />
        )}
      </Dialog>
      <Dialog open={typeof action === "object" && action !== null} title="Reverse payment" onClose={() => setAction(null)}>
        {typeof action === "object" && action !== null && (
          <ReverseForm<SupplierPayment> endpoint={`/supplier-payments/${action.reversePayment.id}/reverse`} confirmLabel="Reverse payment"
            explain={`The ${naira(action.reversePayment.amount)} payment of ${dateOnly(action.reversePayment.paid_on)} will be marked reversed and owed again. Use this only for a payment recorded by mistake.`}
            onDone={() => changed("Payment reversed.")} />
        )}
      </Dialog>

      <Dialog open={paying} title="Record payment to supplier" onClose={() => setPaying(false)}>
        {paying && supplier && (
          <PaymentForm supplier={supplier} purchase={purchase} onDone={(p) => {
            setPaying(false);
            setFlash(`Recorded ${naira(p.amount)} paid to ${p.supplier_name}.`);
            onChanged();
          }} />
        )}
      </Dialog>
    </>
  );
}
