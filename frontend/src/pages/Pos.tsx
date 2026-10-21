import { CheckCircle2, Minus, Plus, Printer, ScanLine, Search, Trash2, UserPlus, UserRound, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Button, Dialog, Notice, PageHeader, Panel, Spinner } from "../components/ui";
import {
  api,
  ApiError,
  newKey,
  qs,
  type BusinessSettings,
  type Customer,
  type Page,
  type PaymentMethod,
  type PosProduct,
  type PosResult,
  type PosUnit,
  type Sale,
  type Unit,
} from "../lib/api";
import { useAuth } from "../lib/auth";
import { naira, phone, todayISO } from "../lib/format";
import { CustomerForm } from "./Customers";
import { SalesHistory } from "./SalesHistory";

export function PosPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "history" ? "history" : "new";
  return (
    <>
      <PageHeader title="Sales / POS" />
      <div role="tablist" aria-label="Sales sections" className="mb-5 flex gap-1 border-b border-line no-print">
        {([["new", "New sale"], ["history", "Sales history"]] as const).map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id}
            onClick={() => setParams(id === "history" ? { tab: "history" } : {})}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-[15px] ${
              tab === id ? "border-pad text-ink font-medium" : "border-transparent text-muted hover:text-ink"}`}>
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel">{tab === "history" ? <SalesHistory /> : <Till />}</div>
    </>
  );
}

// ---------------- The till ----------------

type CartLine = { key: number; product: PosProduct; unit: PosUnit | null; quantity: number; discount: string; price: string };

const isService = (p: Pick<PosProduct, "tracking_type">) => p.tracking_type === "service";
/** A service priced at ₦0 means "type the price at the till" (e.g. custom printing jobs). */
const isOpenPrice = (p: Pick<PosProduct, "tracking_type" | "selling_price">) =>
  isService(p) && Number(p.selling_price) === 0;
type PayRow = { key: number; method_id: string; amount: string };

let nextKey = 1;
const money = (s: string) => Number(s.replaceAll(",", "").trim()) || 0;
const kobo = (n: number) => Math.round(n * 100);
const unitLabel = (u: PosUnit) => [u.imei1 && `IMEI ${u.imei1}`, u.serial_number && `S/N ${u.serial_number}`].filter(Boolean).join(" · ");

function Till() {
  const { user } = useAuth();
  const isOwner = user?.role === "owner";
  const [settings, setSettings] = useState<BusinessSettings | null>(null);
  const [methods, setMethods] = useState<PaymentMethod[]>([]);
  const [walkIn, setWalkIn] = useState<Customer | null>(null);
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const [payments, setPayments] = useState<PayRow[]>([]);
  const [dueDate, setDueDate] = useState("");
  const [tendered, setTendered] = useState("");
  const [saleKey, setSaleKey] = useState(newKey); // one per cart: a double click makes one sale
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lineErrors, setLineErrors] = useState<Record<number, string>>({});
  const [picking, setPicking] = useState<PosProduct | null>(null);
  const [done, setDone] = useState<Sale | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.get<BusinessSettings>("/settings").then(setSettings).catch(() => {});
    api.get<PaymentMethod[]>("/payment-methods").then((m) => {
      setMethods(m);
      setPayments([{ key: nextKey++, method_id: m[0] ? String(m[0].id) : "", amount: "" }]);
    }).catch(() => {});
    api.get<Customer>("/customers/walk-in").then((c) => { setWalkIn(c); setCustomer((cur) => cur ?? c); }).catch(() => {});
  }, []);

  const totals = useMemo(() => {
    const subtotal = cart.reduce((s, l) => s + kobo(l.quantity * linePrice(l)), 0);
    const discount = cart.reduce((s, l) => s + kobo(money(l.discount)), 0);
    const paid = payments.reduce((s, p) => s + kobo(money(p.amount)), 0);
    return { subtotal: subtotal / 100, discount: discount / 100, total: (subtotal - discount) / 100, paid: paid / 100,
      owed: (subtotal - discount - paid) / 100 };
  }, [cart, payments]);

  const itemCount = cart.reduce((n, l) => n + l.quantity, 0);
  const limitPct = Number(settings?.staff_max_discount_percent ?? 0);
  const cashMethodIds = methods.filter((m) => m.name.toLowerCase().includes("cash")).map((m) => String(m.id));
  const cashPaid = payments.filter((p) => cashMethodIds.includes(p.method_id)).reduce((s, p) => s + money(p.amount), 0);

  function linePrice(l: CartLine): number {
    return isOpenPrice(l.product) ? money(l.price) : money(l.product.selling_price);
  }

  function flashError(message: string) {
    setError(message);
    setDone(null);
  }

  function addResult(r: PosResult) {
    setError(null);
    setDone(null);
    if (r.kind === "unit" && r.unit) {
      if (cart.some((l) => l.unit?.id === r.unit!.id)) return flashError("That phone is already in the cart.");
      setCart((c) => [...c, { key: nextKey++, product: r.product, unit: r.unit, quantity: 1, discount: "", price: "" }]);
    } else if (r.product.tracking_type === "serialized") {
      setPicking(r.product);
      return;
    } else if (isOpenPrice(r.product)) {
      // Each job has its own price, so each goes on its own line; the price box gets the cursor.
      const key = nextKey++;
      setCart((c) => [...c, { key, product: r.product, unit: null, quantity: 1, discount: "", price: "" }]);
      setTimeout(() => document.getElementById(`price-${key}`)?.focus(), 0);
      return;
    } else {
      const existing = cart.find((l) => l.product.id === r.product.id && !l.unit);
      const wanted = (existing?.quantity ?? 0) + 1;
      if (!isService(r.product) && wanted > r.product.qty_on_hand) {
        return flashError(r.product.qty_on_hand
          ? `Only ${r.product.qty_on_hand} ${r.product.name} in stock.`
          : `${r.product.name} is out of stock.`);
      }
      if (existing) setCart((c) => c.map((l) => (l.key === existing.key ? { ...l, quantity: wanted } : l)));
      else setCart((c) => [...c, { key: nextKey++, product: r.product, unit: null, quantity: 1, discount: "", price: "" }]);
    }
    searchRef.current?.focus();
  }

  function setQty(line: CartLine, quantity: number) {
    if (quantity < 1) return;
    if (!isService(line.product) && quantity > line.product.qty_on_hand) return flashError(`Only ${line.product.qty_on_hand} ${line.product.name} in stock.`);
    setCart((c) => c.map((l) => (l.key === line.key ? { ...l, quantity } : l)));
  }

  function payInFull() {
    const others = payments.slice(1).reduce((s, p) => s + kobo(money(p.amount)), 0);
    const rest = Math.max(0, kobo(totals.total) - others) / 100;
    setPayments((ps) => ps.map((p, i) => (i === 0 ? { ...p, amount: rest ? rest.toFixed(2) : "" } : p)));
  }

  function reset() {
    setCart([]);
    setPayments([{ key: nextKey++, method_id: methods[0] ? String(methods[0].id) : "", amount: "" }]);
    setDueDate("");
    setTendered("");
    setCustomer(walkIn);
    setSaleKey(newKey());
    setLineErrors({});
    setError(null);
    searchRef.current?.focus();
  }

  async function complete() {
    if (busy || !cart.length) return;
    setError(null);
    setLineErrors({});
    if (totals.owed > 0 && customer?.is_walk_in) {
      return setError(`${naira(totals.owed)} is unpaid. Walk-in customers must pay in full — choose or add the customer to sell on credit.`);
    }
    if (totals.owed < 0) return setError("Payments are more than the total. Enter only what goes towards this sale.");
    const unpriced = cart.find((l) => isOpenPrice(l.product) && !(money(l.price) > 0));
    if (unpriced) {
      setLineErrors({ [unpriced.key]: `Enter the price for ${unpriced.product.name}.` });
      document.getElementById(`price-${unpriced.key}`)?.focus();
      return setError("Please fix the highlighted item.");
    }
    setBusy(true);
    try {
      const sale = await api.post<Sale>("/sales", {
        customer_id: customer && !customer.is_walk_in ? customer.id : null,
        lines: cart.map((l) => ({
          product_id: l.product.id, unit_id: l.unit?.id ?? null, quantity: l.quantity,
          discount: money(l.discount).toFixed(2),
          ...(isOpenPrice(l.product) ? { unit_price: money(l.price).toFixed(2) } : {}),
        })),
        payments: payments.filter((p) => money(p.amount) > 0)
          .map((p) => ({ method_id: Number(p.method_id), amount: money(p.amount).toFixed(2) })),
        due_date: totals.owed > 0 && dueDate ? dueDate : null,
        idempotency_key: saleKey,
      });
      setDone(sale);
      setCart([]);
      setPayments([{ key: nextKey++, method_id: methods[0] ? String(methods[0].id) : "", amount: "" }]);
      setDueDate("");
      setTendered("");
      setCustomer(walkIn);
      setSaleKey(newKey());
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Could not complete the sale. Try again.";
      const m = message.match(/^Line (\d+): (.*)$/);
      if (m && cart[Number(m[1]) - 1]) {
        setLineErrors({ [cart[Number(m[1]) - 1].key]: m[2] });
        setError("Please fix the highlighted item.");
      } else setError(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-5 xl:grid-cols-[1fr_24rem] items-start">
      <div className="space-y-4 min-w-0">
        <ScanBox inputRef={searchRef} onPick={addResult} />
        {done && (
          <Notice tone="success">
            <span className="font-medium">Sale {done.receipt_no} completed</span> — {naira(done.total)}
            {Number(done.balance_due) > 0 && <> · {naira(done.balance_due)} owed by {done.customer_name}</>}.{" "}
            <Link to={`/sales/${done.id}?print=1`} className="inline-flex items-center gap-1 font-medium underline">
              <Printer className="size-4" aria-hidden /> Print receipt
            </Link>
          </Notice>
        )}
        {error && <Notice tone="error">{error}</Notice>}

        <Panel title={`Cart${cart.length ? ` (${itemCount} item${itemCount === 1 ? "" : "s"})` : ""}`}>
          {!cart.length ? (
            <p className="py-6 text-center text-muted">
              <ScanLine className="mx-auto mb-2 size-6" aria-hidden />
              Scan a barcode or IMEI, or search for a product above.
            </p>
          ) : (
            <ul className="divide-y divide-line -my-2">
              {cart.map((l, i) => {
                const gross = l.quantity * linePrice(l);
                const allowed = Math.round(gross * limitPct) / 100;
                const overLimit = !isOwner && money(l.discount) > allowed;
                return (
                  <li key={l.key} className={`py-3 ${lineErrors[l.key] ? "bg-alert-bg/40 -mx-5 px-5" : ""}`}>
                    <div className="flex flex-wrap items-start gap-3">
                      <div className="flex-1 min-w-48">
                        <p className="font-medium"><span className="text-muted num">{i + 1}.</span> {l.product.name}</p>
                        <p className="text-sm text-muted num">
                          {l.unit ? unitLabel(l.unit) : isService(l.product) ? "Service" : l.product.sku}
                          {(l.unit?.condition ?? l.product.condition) && (l.unit?.condition ?? l.product.condition) !== "new" &&
                            ` · ${l.unit?.condition ?? l.product.condition}`}
                        </p>
                      </div>
                      <div className="flex items-center gap-1">
                        {l.unit ? <span className="w-24 text-center num">1</span> : (
                          <>
                            <button type="button" aria-label={`One less ${l.product.name}`} onClick={() => setQty(l, l.quantity - 1)}
                              className="rounded-md border border-line p-1.5 hover:border-board-300 disabled:opacity-40" disabled={l.quantity <= 1}>
                              <Minus className="size-4" aria-hidden />
                            </button>
                            <input aria-label={`Quantity of ${l.product.name}`} type="number" min={1} value={l.quantity}
                              onChange={(e) => setQty(l, Number(e.target.value) || 1)}
                              className="w-14 h-9 rounded-md border border-line text-center num focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40" />
                            <button type="button" aria-label={`One more ${l.product.name}`} onClick={() => setQty(l, l.quantity + 1)}
                              className="rounded-md border border-line p-1.5 hover:border-board-300">
                              <Plus className="size-4" aria-hidden />
                            </button>
                          </>
                        )}
                      </div>
                      {isOpenPrice(l.product) && (
                        <label className="w-28">
                          <span className="sr-only">Price of {l.product.name}</span>
                          <input id={`price-${l.key}`} inputMode="decimal" placeholder="Price ₦" value={l.price}
                            onChange={(e) => setCart((c) => c.map((x) => (x.key === l.key ? { ...x, price: e.target.value } : x)))}
                            className={`w-full h-9 rounded-md border px-2.5 text-right num focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40 ${lineErrors[l.key] && !money(l.price) ? "border-alert" : "border-pad"}`} />
                        </label>
                      )}
                      <label className="w-28">
                        <span className="sr-only">Discount on {l.product.name}</span>
                        <input inputMode="decimal" placeholder="Discount" value={l.discount}
                          onChange={(e) => setCart((c) => c.map((x) => (x.key === l.key ? { ...x, discount: e.target.value } : x)))}
                          className={`w-full h-9 rounded-md border px-2.5 text-right num focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40 ${overLimit ? "border-alert" : "border-line"}`} />
                      </label>
                      <div className="w-32 text-right">
                        <p className="num font-medium">{naira(gross - money(l.discount))}</p>
                        <p className="text-sm text-muted num">{l.quantity} × {isOpenPrice(l.product) && !money(l.price) ? "price?" : naira(linePrice(l))}</p>
                      </div>
                      <button type="button" aria-label={`Remove ${l.product.name}`} onClick={() => setCart((c) => c.filter((x) => x.key !== l.key))}
                        className="rounded p-1.5 text-muted hover:text-alert">
                        <Trash2 className="size-4" aria-hidden />
                      </button>
                    </div>
                    {overLimit && <p className="mt-1 text-sm text-alert">Staff may give up to {limitPct}% ({naira(allowed)}) on this line. Larger discounts need the owner.</p>}
                    {lineErrors[l.key] && <p className="mt-1 text-sm text-alert">{lineErrors[l.key]}</p>}
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>
      </div>

      <div className="space-y-4 xl:sticky xl:top-6">
        <CustomerBox customer={customer} walkIn={walkIn} onChange={setCustomer} />
        <Panel title="Payment">
          <dl className="space-y-1 text-[15px]">
            <div className="flex justify-between"><dt className="text-muted">Subtotal</dt><dd className="num">{naira(totals.subtotal)}</dd></div>
            {totals.discount > 0 && (
              <div className="flex justify-between"><dt className="text-muted">Discount</dt><dd className="num">−{naira(totals.discount)}</dd></div>
            )}
            <div className="flex justify-between border-t border-line pt-1.5 text-lg font-semibold"><dt>Total</dt><dd className="num">{naira(totals.total)}</dd></div>
          </dl>

          <div className="mt-4 space-y-2">
            {payments.map((p, i) => (
              <div key={p.key} className="flex gap-2">
                <label className="flex-1">
                  <span className="sr-only">Payment method {i + 1}</span>
                  <select value={p.method_id} onChange={(e) => setPayments((ps) => ps.map((x) => (x.key === p.key ? { ...x, method_id: e.target.value } : x)))}
                    className="w-full h-10 rounded-md border border-line bg-white px-2.5 text-[15px] focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40">
                    {methods.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                  </select>
                </label>
                <label className="w-36">
                  <span className="sr-only">Amount paid {i + 1}</span>
                  <input inputMode="decimal" placeholder="0.00" value={p.amount}
                    onChange={(e) => setPayments((ps) => ps.map((x) => (x.key === p.key ? { ...x, amount: e.target.value } : x)))}
                    className="w-full h-10 rounded-md border border-line px-2.5 text-right num focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40" />
                </label>
                {payments.length > 1 && (
                  <button type="button" aria-label="Remove this payment" onClick={() => setPayments((ps) => ps.filter((x) => x.key !== p.key))}
                    className="rounded p-1.5 text-muted hover:text-alert"><X className="size-4" aria-hidden /></button>
                )}
              </div>
            ))}
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" className="h-8" onClick={payInFull} disabled={!cart.length}>Paid in full</Button>
              {payments.length < 3 && (
                <Button variant="ghost" className="h-8" onClick={() => setPayments((ps) => [...ps, { key: nextKey++, method_id: methods[1] ? String(methods[1].id) : String(methods[0]?.id ?? ""), amount: "" }])}>
                  <Plus className="size-4" aria-hidden /> Split payment
                </Button>
              )}
            </div>
          </div>

          {cashPaid > 0 && (
            <div className="mt-3 flex items-center justify-between gap-2 rounded-md bg-paper px-3 py-2 text-sm">
              <label className="flex items-center gap-2">
                Customer gave
                <input inputMode="decimal" value={tendered} onChange={(e) => setTendered(e.target.value)} placeholder="Cash"
                  className="w-28 h-8 rounded-md border border-line bg-white px-2 text-right num focus:outline-none focus:border-board" />
              </label>
              <span className="num font-medium">{money(tendered) >= cashPaid ? `Change ${naira(money(tendered) - cashPaid)}` : ""}</span>
            </div>
          )}

          {cart.length > 0 && totals.owed > 0 && (
            <div className="mt-3 rounded-md border border-pad/50 bg-pad/10 px-3 py-2 text-sm">
              {customer?.is_walk_in ? (
                <p><span className="font-medium num">{naira(totals.owed)}</span> still to pay. Walk-in customers must pay in full.</p>
              ) : (
                <>
                  <p><span className="font-medium num">{naira(totals.owed)}</span> will be owed by {customer?.name} (credit sale).</p>
                  {!isOwner && settings && !settings.allow_staff_credit_sales && (
                    <p className="mt-1 text-alert">Sales staff cannot sell on credit (Settings). Ask the owner.</p>
                  )}
                  <label className="mt-2 flex items-center gap-2">
                    Pay by
                    <input type="date" min={todayISO()} value={dueDate} onChange={(e) => setDueDate(e.target.value)}
                      className="h-8 rounded-md border border-line bg-white px-2 focus:outline-none focus:border-board" />
                    <span className="text-muted">(optional)</span>
                  </label>
                </>
              )}
            </div>
          )}

          <div className="mt-4 flex gap-2">
            <Button className="flex-1 h-12 text-base" onClick={complete} loading={busy} disabled={!cart.length || busy}>
              <CheckCircle2 className="size-5" aria-hidden /> Complete sale
            </Button>
            {cart.length > 0 && <Button variant="ghost" className="h-12" onClick={reset} disabled={busy}>Clear</Button>}
          </div>
        </Panel>
      </div>

      <Dialog open={!!picking} title={picking ? `Which ${picking.name}?` : ""} onClose={() => setPicking(null)}>
        {picking && (
          <UnitPicker product={picking} exclude={cart.filter((l) => l.unit).map((l) => l.unit!.id)}
            onPick={(u) => {
              setCart((c) => [...c, { key: nextKey++, product: picking, unit: u, quantity: 1, discount: "", price: "" }]);
              setPicking(null);
              setTimeout(() => searchRef.current?.focus(), 0);
            }} />
        )}
      </Dialog>
    </div>
  );
}

/** Type or scan. Enter adds the match when there is exactly one obvious choice. */
function ScanBox({ inputRef, onPick }: { inputRef: React.RefObject<HTMLInputElement | null>; onPick: (r: PosResult) => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<PosResult[]>([]);
  const [active, setActive] = useState(0);
  const [searching, setSearching] = useState(false);
  const [notFound, setNotFound] = useState<string | null>(null);
  const seq = useRef(0);

  const search = useCallback(async (term: string) => {
    const n = ++seq.current;
    setSearching(true);
    try {
      const r = await api.get<PosResult[]>(`/pos/search${qs({ q: term })}`);
      if (n === seq.current) { setResults(r); setActive(0); }
      return r;
    } finally {
      if (n === seq.current) setSearching(false);
    }
  }, []);

  useEffect(() => {
    setNotFound(null);
    if (!q.trim()) { setResults([]); return; }
    const t = setTimeout(() => { search(q.trim()).catch(() => {}); }, 200);
    return () => clearTimeout(t);
  }, [q, search]);

  function pick(r: PosResult) {
    onPick(r);
    setQ("");
    setResults([]);
  }

  async function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); return; }
    if (e.key === "Escape") { setQ(""); return; }
    if (e.key !== "Enter") return;
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    // A scanner types the code and presses Enter at once, so search now rather than wait.
    const found = await search(term).catch(() => [] as PosResult[]);
    if (!found.length) { setNotFound(term); return; }
    const first = found[Math.min(active, found.length - 1)];
    const exact = first.kind === "unit" || [first.product.sku, first.product.barcode].includes(term.toUpperCase());
    if (found.length === 1 || exact || active > 0) pick(first);
  }

  return (
    <div className="relative">
      <label htmlFor="pos-search" className="sr-only">Scan or search</label>
      <Search className="pointer-events-none absolute left-3.5 top-1/2 size-5 -translate-y-1/2 text-muted" aria-hidden />
      <input id="pos-search" ref={inputRef} autoFocus value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey}
        role="combobox" aria-expanded={results.length > 0} aria-controls="pos-results" aria-autocomplete="list"
        placeholder="Scan barcode or IMEI, or type a product name"
        className="block w-full h-12 rounded-lg border border-line bg-white pl-11 pr-3 text-base placeholder:text-muted/70
          focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40" />
      {q.trim() && (results.length > 0 || notFound || searching) && (
        <ul id="pos-results" role="listbox" className="absolute z-20 mt-1 w-full overflow-hidden rounded-md border border-line bg-white shadow-lg">
          {results.map((r, i) => (
            <li key={`${r.kind}-${r.unit?.id ?? r.product.id}`} role="option" aria-selected={i === active}
              onMouseDown={(e) => { e.preventDefault(); pick(r); }} onMouseEnter={() => setActive(i)}
              className={`flex cursor-pointer items-center justify-between gap-3 px-3 py-2 ${i === active ? "bg-board/5" : ""}`}>
              <span className="min-w-0">
                <span className="font-medium">{r.product.name}</span>
                <span className="block text-sm text-muted num truncate">
                  {r.unit ? unitLabel(r.unit) : `${r.product.sku}${r.product.tracking_type === "serialized" ? " · choose a unit" : ""}`}
                </span>
              </span>
              <span className="shrink-0 text-right">
                <span className="block num">{isOpenPrice(r.product) ? "Price at till" : naira(r.product.selling_price)}</span>
                <span className={`block text-sm num ${r.product.qty_on_hand || isService(r.product) ? "text-muted" : "text-alert"}`}>
                  {r.unit ? <Badge tone="ok">This unit</Badge> : isService(r.product) ? "Service" : `${r.product.qty_on_hand} in stock`}
                </span>
              </span>
            </li>
          ))}
          {!results.length && notFound && (
            <li className="px-3 py-2 text-muted">Nothing found for “{notFound}”. Check the code, or search by name.</li>
          )}
          {!results.length && !notFound && searching && <li className="px-3 py-2 text-muted">Searching…</li>}
        </ul>
      )}
    </div>
  );
}

function UnitPicker({ product, exclude, onPick }: { product: PosProduct; exclude: number[]; onPick: (u: PosUnit) => void }) {
  const [units, setUnits] = useState<Unit[] | null>(null);
  const [filter, setFilter] = useState("");
  useEffect(() => {
    api.get<Page<Unit>>(`/units${qs({ product_id: product.id, status: "in_stock", page_size: 200 })}`)
      .then((p) => setUnits(p.items)).catch(() => setUnits([]));
  }, [product.id]);
  if (!units) return <Spinner />;
  const shown = units.filter((u) => !exclude.includes(u.id)
    && [u.imei1, u.imei2, u.serial_number].some((v) => (v ?? "").toLowerCase().includes(filter.trim().toLowerCase())));
  return (
    <div className="space-y-3">
      <input autoFocus value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Type part of the IMEI or serial"
        aria-label="Filter units"
        className="block w-full h-10 rounded-md border border-line bg-white px-3 text-[15px] focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40"
        onKeyDown={(e) => { if (e.key === "Enter" && shown.length === 1) { e.preventDefault(); onPick(shown[0]); } }} />
      <ul className="max-h-80 overflow-y-auto divide-y divide-line rounded-md border border-line">
        {shown.map((u) => (
          <li key={u.id}>
            <button type="button" onClick={() => onPick(u)} className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-board/5">
              <span className="num">{unitLabel(u)}</span>
              {u.condition !== "new" && <Badge tone="pad">{u.condition}</Badge>}
            </button>
          </li>
        ))}
        {!shown.length && <li className="px-3 py-4 text-center text-muted">{units.length ? "No unit matches." : `No ${product.name} in stock.`}</li>}
      </ul>
    </div>
  );
}

function CustomerBox({ customer, walkIn, onChange }: { customer: Customer | null; walkIn: Customer | null; onChange: (c: Customer | null) => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Customer[]>([]);
  const [adding, setAdding] = useState(false);
  useEffect(() => {
    if (!q.trim()) { setResults([]); return; }
    const t = setTimeout(() => {
      api.get<Page<Customer>>(`/customers${qs({ q: q.trim(), page_size: 6 })}`).then((p) => setResults(p.items)).catch(() => {});
    }, 200);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <Panel title="Customer">
      {customer && (
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <UserRound className="size-5 text-muted" aria-hidden />
            <div>
              <p className="font-medium">{customer.name}</p>
              {customer.phone && <p className="text-sm text-muted num">{phone(customer.phone)}</p>}
              {Number(customer.owed) > 0 && (
                <p className="text-sm text-alert num">
                  Already owes {naira(customer.owed)}{Number(customer.overdue) > 0 ? ` (${naira(customer.overdue)} overdue)` : ""}
                </p>
              )}
            </div>
          </div>
          {!customer.is_walk_in && (
            <Button variant="ghost" className="h-8" onClick={() => onChange(walkIn)}>Use walk-in</Button>
          )}
        </div>
      )}
      <div className="relative mt-3">
        <label htmlFor="pos-customer" className="sr-only">Find a customer</label>
        <input id="pos-customer" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find customer by name or phone"
          className="block w-full h-10 rounded-md border border-line bg-white px-3 text-[15px] focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40" />
        {results.length > 0 && (
          <ul className="absolute z-20 mt-1 w-full overflow-hidden rounded-md border border-line bg-white shadow-lg">
            {results.map((c) => (
              <li key={c.id}>
                <button type="button" onClick={() => { onChange(c); setQ(""); setResults([]); }}
                  className="flex w-full justify-between gap-2 px-3 py-2 text-left hover:bg-board/5">
                  <span className="font-medium">{c.name}</span>
                  <span className="text-sm text-muted num">{phone(c.phone)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <Button variant="ghost" className="mt-2 h-8" onClick={() => setAdding(true)}>
        <UserPlus className="size-4" aria-hidden /> New customer
      </Button>
      <Dialog open={adding} title="Add customer" onClose={() => setAdding(false)}>
        {adding && <CustomerForm canTurnOff={false} onDone={(c) => { onChange(c); setAdding(false); }} />}
      </Dialog>
    </Panel>
  );
}
