import { Check, Plus, Search, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Badge, Button, Checkbox, Dialog, Notice, Pager, SelectInput, Spinner, TextInput } from "../components/ui";
import {
  api,
  ApiError,
  newKey,
  qs,
  type Adjustment,
  type Page,
  type PaymentMethod,
  type Product,
  type Purchase,
  type Sale,
  type Unit,
} from "../lib/api";
import { useAuth } from "../lib/auth";
import { dateTime, naira, UNIT_STATUS_LABELS } from "../lib/format";

function useMethods() {
  const [methods, setMethods] = useState<PaymentMethod[]>([]);
  useEffect(() => { api.get<PaymentMethod[]>("/payment-methods").then(setMethods).catch(() => {}); }, []);
  return methods;
}

function errorText(err: unknown) {
  return err instanceof ApiError ? err.message : "Could not save. Try again.";
}

// ---------------- Customer return ----------------

/** Bring items back from a sale. Value first reduces what is owed; only over-payment is refunded. */
export function ReturnItemsForm({ sale, onDone }: { sale: Sale; onDone: (s: Sale) => void }) {
  const methods = useMethods();
  const lines = (sale.lines ?? []).filter((l) => l.quantity > l.returned_quantity);
  const [picked, setPicked] = useState<Record<number, { qty: number; damaged: boolean }>>({});
  const [reason, setReason] = useState("");
  const [methodId, setMethodId] = useState("");
  const [key] = useState(newKey);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (!methodId && methods[0]) setMethodId(String(methods[0].id)); }, [methods, methodId]);

  // Preview, same rule as the server (the server's figures are final).
  const value = lines.reduce((sum, l) => {
    const p = picked[l.id];
    if (!p) return sum;
    const last = l.returned_quantity + p.qty === l.quantity;
    const already = (Number(l.line_total) * l.returned_quantity) / l.quantity;
    return sum + (last ? Number(l.line_total) - already : (Number(l.line_total) * p.qty) / l.quantity);
  }, 0);
  const refund = Math.max(0, Number(sale.amount_paid) - (Number(sale.total) - Number(sale.returned_total) - value));

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const chosen = Object.entries(picked);
    if (!chosen.length) return setError("Tick the items that came back.");
    setBusy(true);
    setError(null);
    try {
      onDone(await api.post<Sale>(`/sales/${sale.id}/returns`, {
        lines: chosen.map(([id, p]) => ({ sale_line_id: Number(id), quantity: p.qty, restock: !p.damaged })),
        reason,
        refund_method_id: refund > 0 ? Number(methodId) : null,
        idempotency_key: key,
      }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {error && <Notice tone="error">{error}</Notice>}
      {!lines.length && <p className="text-muted">Everything on this receipt has already been returned.</p>}
      <ul className="divide-y divide-line rounded-md border border-line">
        {lines.map((l) => {
          const left = l.quantity - l.returned_quantity;
          const p = picked[l.id];
          return (
            <li key={l.id} className="flex flex-wrap items-center gap-3 px-3 py-2">
              <label className="flex flex-1 min-w-48 items-start gap-2">
                <input type="checkbox" className="mt-1 size-4 accent-board" checked={!!p}
                  onChange={(e) => setPicked((x) => {
                    const next = { ...x };
                    if (e.target.checked) next[l.id] = { qty: left, damaged: false };
                    else delete next[l.id];
                    return next;
                  })} />
                <span>
                  <span className="font-medium">{l.product_name}</span>
                  <span className="block text-sm text-muted num">
                    {l.imei1 ? `IMEI ${l.imei1}` : l.serial_number ? `S/N ${l.serial_number}`
                      : l.tracking_type === "service" ? `Service · ${left} of ${l.quantity} can be refunded` : `${left} of ${l.quantity} can come back`}
                  </span>
                </span>
              </label>
              {p && !l.unit_id && left > 1 && (
                <label className="flex items-center gap-1 text-sm">
                  Qty
                  <input type="number" min={1} max={left} value={p.qty} aria-label={`How many ${l.product_name}`}
                    onChange={(e) => setPicked((x) => ({ ...x, [l.id]: { ...p, qty: Math.min(left, Math.max(1, Number(e.target.value) || 1)) } }))}
                    className="w-16 h-8 rounded-md border border-line text-center num" />
                </label>
              )}
              {p && l.tracking_type !== "service" && (
                <Checkbox label="Came back damaged" checked={p.damaged}
                  onChange={(e) => setPicked((x) => ({ ...x, [l.id]: { ...p, damaged: e.target.checked } }))} />
              )}
            </li>
          );
        })}
      </ul>
      <TextInput label="Reason" value={reason} onChange={(e) => setReason(e.target.value)}
        placeholder="e.g. Faulty on arrival" hint="Kept in the history." data-autofocus />
      <div className="rounded-md bg-paper px-3 py-2 text-sm space-y-1">
        <p>Value returned: <span className="num font-medium">{naira(value)}</span></p>
        {value > 0 && (refund > 0
          ? <p>Give back to the customer: <span className="num font-medium">{naira(refund)}</span></p>
          : <p>This reduces what {sale.customer_name} owes; nothing is given back.</p>)}
      </div>
      {refund > 0 && (
        <SelectInput label="Refund paid by" value={methodId} onChange={(e) => setMethodId(e.target.value)}>
          {methods.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </SelectInput>
      )}
      <div className="flex justify-end pt-2">
        <Button type="submit" loading={busy} disabled={!lines.length}>Record return</Button>
      </div>
    </form>
  );
}

// ---------------- Reversal (sale, purchase, payments) ----------------

/** Undo a record with a reason. If money must go back to a customer, ask how. */
export function ReverseForm<T>({
  endpoint, explain, refund, confirmLabel, onDone,
}: {
  endpoint: string;
  explain: string;
  refund?: number;
  confirmLabel: string;
  onDone: (result: T) => void;
}) {
  const methods = useMethods();
  const [reason, setReason] = useState("");
  const [methodId, setMethodId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!methodId && methods[0]) setMethodId(String(methods[0].id)); }, [methods, methodId]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await api.post<T>(endpoint, { reason, refund_method_id: refund ? Number(methodId) : null }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <p className="text-muted">{explain}</p>
      {error && <Notice tone="error">{error}</Notice>}
      <TextInput label="Reason" value={reason} onChange={(e) => setReason(e.target.value)}
        placeholder="e.g. Entered on the wrong customer" hint="Required. Kept in the history." />
      {!!refund && refund > 0 && (
        <SelectInput label={`Give back ${naira(refund)} by`} value={methodId} onChange={(e) => setMethodId(e.target.value)}>
          {methods.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </SelectInput>
      )}
      <div className="flex justify-end pt-2">
        <Button type="submit" variant="danger" loading={busy}>{confirmLabel}</Button>
      </div>
    </form>
  );
}

// ---------------- Supplier return ----------------

export function SupplierReturnForm({ purchase, onDone }: { purchase: Purchase; onDone: (p: Purchase) => void }) {
  const lines = (purchase.lines ?? []).filter((l) =>
    l.tracking_type === "serialized" ? l.units_in_stock.length > 0 : l.quantity > l.returned_quantity);
  const [qty, setQty] = useState<Record<number, number>>({});
  const [units, setUnits] = useState<Record<number, number[]>>({});
  const [reason, setReason] = useState("");
  const [key] = useState(newKey);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const chosen = lines.flatMap((l) => {
    if (l.tracking_type === "serialized") {
      const ids = units[l.id] ?? [];
      return ids.length ? [{ purchase_line_id: l.id, quantity: ids.length, unit_ids: ids, credit: ids.length * Number(l.unit_cost) }] : [];
    }
    const q = qty[l.id] ?? 0;
    return q > 0 ? [{ purchase_line_id: l.id, quantity: q, unit_ids: [] as number[], credit: q * Number(l.unit_cost) }] : [];
  });
  const credit = chosen.reduce((s, c) => s + c.credit, 0);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!chosen.length) return setError("Choose what goes back to the supplier.");
    setBusy(true);
    setError(null);
    try {
      onDone(await api.post<Purchase>(`/purchases/${purchase.id}/returns`, {
        lines: chosen.map(({ credit: _c, ...rest }) => rest), reason, idempotency_key: key,
      }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {error && <Notice tone="error">{error}</Notice>}
      {!lines.length && <p className="text-muted">Nothing from this purchase is left to return.</p>}
      <ul className="divide-y divide-line rounded-md border border-line">
        {lines.map((l) => (
          <li key={l.id} className="px-3 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>
                <span className="font-medium">{l.product_name}</span>
                <span className="ml-2 text-sm text-muted num">{naira(l.unit_cost)} each</span>
              </span>
              {l.tracking_type === "quantity" && (
                <label className="flex items-center gap-1 text-sm">
                  Return
                  <input type="number" min={0} max={l.quantity - l.returned_quantity} value={qty[l.id] ?? 0}
                    aria-label={`How many ${l.product_name} go back`}
                    onChange={(e) => setQty((x) => ({ ...x, [l.id]: Math.min(l.quantity - l.returned_quantity, Math.max(0, Number(e.target.value) || 0)) }))}
                    className="w-16 h-8 rounded-md border border-line text-center num" />
                  of {l.quantity - l.returned_quantity}
                </label>
              )}
            </div>
            {l.tracking_type === "serialized" && (
              <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
                {l.units_in_stock.map((u) => (
                  <Checkbox key={u.id} label={u.label} checked={(units[l.id] ?? []).includes(u.id)}
                    onChange={(e) => setUnits((x) => ({ ...x, [l.id]: e.target.checked
                      ? [...(x[l.id] ?? []), u.id] : (x[l.id] ?? []).filter((i) => i !== u.id) }))} />
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
      <TextInput label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Faulty batch" />
      <p className="text-sm text-muted">Supplier credit (invoice price): <span className="num font-medium text-ink">{naira(credit)}</span></p>
      <div className="flex justify-end pt-2">
        <Button type="submit" loading={busy} disabled={!lines.length}>Return to supplier</Button>
      </div>
    </form>
  );
}

// ---------------- Stock adjustments ----------------

const KIND_LABEL: Record<string, string> = { damage: "Damaged", missing: "Missing", adjustment: "Count correction" };

export function AdjustmentsTab() {
  const { user } = useAuth();
  const isOwner = user?.role === "owner";
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<Adjustment> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [deciding, setDeciding] = useState<{ adj: Adjustment; approve: boolean } | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api.get<Page<Adjustment>>(`/stock-adjustments${qs({ status, page, page_size: 25 })}`).then(setData)
      .catch((e) => setError(e.message));
  }, [status, page]);
  useEffect(load, [load]);

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-muted max-w-2xl">
          {isOwner
            ? "Damaged, missing or found stock and count corrections. Staff requests wait for your approval; only approved adjustments change stock."
            : "Report damaged, missing or found stock. The owner approves it before stock changes."}
        </p>
        <div className="flex items-center gap-2">
          <label className="sr-only" htmlFor="adj-status">Status</label>
          <select id="adj-status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}
            className="h-10 rounded-md border border-line bg-white px-3 text-[15px] focus:outline-none focus:border-board">
            <option value="">All</option>
            <option value="pending">Waiting for approval</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
          </select>
          <Button onClick={() => { setFlash(null); setAdding(true); }}><Plus className="size-4" aria-hidden /> New adjustment</Button>
        </div>
      </div>
      {flash && <div className="mb-4"><Notice tone="success">{flash}</Notice></div>}
      {error && <Notice tone="error">{error}</Notice>}
      {!data && !error && <Spinner />}
      {data && (
        <>
          <ul className="space-y-3">
            {data.items.map((a) => (
              <li key={a.id} className="rounded-lg border border-line bg-white p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="font-medium">{a.reason}</p>
                    <p className="text-sm text-muted">Asked by {a.requested_by_name} · {dateTime(a.requested_at)}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {a.status === "pending" && <Badge tone="pad">Waiting for approval</Badge>}
                    {a.status === "approved" && <Badge tone="ok">Approved</Badge>}
                    {a.status === "rejected" && <Badge tone="off">Rejected</Badge>}
                    {isOwner && a.status === "pending" && (
                      <>
                        <Button variant="secondary" className="h-8" onClick={() => setDeciding({ adj: a, approve: false })}>
                          <X className="size-4" aria-hidden /> Reject
                        </Button>
                        <Button className="h-8" onClick={() => setDeciding({ adj: a, approve: true })}>
                          <Check className="size-4" aria-hidden /> Approve
                        </Button>
                      </>
                    )}
                  </div>
                </div>
                <ul className="mt-2 text-sm">
                  {a.lines.map((l, i) => (
                    <li key={i} className="flex flex-wrap justify-between gap-2 border-t border-line py-1.5">
                      <span>{l.product_name}{l.unit_label && <span className="ml-1 text-muted num">({l.unit_label})</span>}
                        <span className="ml-2 text-muted">{l.unit_new_status ? `→ ${UNIT_STATUS_LABELS[l.unit_new_status]}` : KIND_LABEL[l.kind]}</span></span>
                      <span className={`num font-medium ${l.quantity_change > 0 ? "text-ok" : "text-alert"}`}>{l.quantity_change > 0 ? "+" : "−"}{Math.abs(l.quantity_change)}</span>
                    </li>
                  ))}
                </ul>
                {a.decided_by_name && (
                  <p className="mt-2 text-sm text-muted">{a.status === "approved" ? "Approved" : "Rejected"} by {a.decided_by_name} · {dateTime(a.decided_at)}{a.decision_note ? ` — ${a.decision_note}` : ""}</p>
                )}
              </li>
            ))}
            {!data.items.length && <li className="rounded-lg border border-line bg-white px-4 py-10 text-center text-muted">No adjustments.</li>}
          </ul>
          <Pager page={page} pageSize={25} total={data.total} noun={["adjustment", "adjustments"]} onPage={setPage} />
        </>
      )}
      <Dialog wide open={adding} title="New stock adjustment" onClose={() => setAdding(false)}>
        {adding && <AdjustmentForm isOwner={isOwner} onDone={(a) => {
          setAdding(false);
          setFlash(a.status === "approved" ? "Adjustment applied." : "Sent to the owner for approval.");
          load();
        }} />}
      </Dialog>
      <Dialog open={!!deciding} title={deciding?.approve ? "Approve adjustment" : "Reject adjustment"} onClose={() => setDeciding(null)}>
        {deciding && <DecisionForm adj={deciding.adj} approve={deciding.approve} onDone={(a) => {
          setDeciding(null);
          setFlash(a.status === "approved" ? "Approved: stock updated." : "Rejected: stock unchanged.");
          load();
        }} />}
      </Dialog>
    </>
  );
}

function DecisionForm({ adj, approve, onDone }: { adj: Adjustment; approve: boolean; onDone: (a: Adjustment) => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await api.post<Adjustment>(`/stock-adjustments/${adj.id}/${approve ? "approve" : "reject"}`, { note }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <p className="text-muted">“{adj.reason}” from {adj.requested_by_name}. {approve ? "Stock changes as listed." : "Nothing changes."}</p>
      {error && <Notice tone="error">{error}</Notice>}
      <TextInput label="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
      <div className="flex justify-end pt-2">
        <Button type="submit" variant={approve ? "primary" : "danger"} loading={busy}>{approve ? "Approve" : "Reject"}</Button>
      </div>
    </form>
  );
}

type AdjRow = { key: number; product: Product; change: string; kind: "damage" | "missing" | "adjustment"; unit: Unit | null; newStatus: "in_stock" | "damaged" | "missing" };
let rowKey = 1;

function AdjustmentForm({ isOwner, onDone }: { isOwner: boolean; onDone: (a: Adjustment) => void }) {
  const [rows, setRows] = useState<AdjRow[]>([]);
  const [reason, setReason] = useState("");
  const [approveNow, setApproveNow] = useState(isOwner);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const update = (key: number, patch: Partial<AdjRow>) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!rows.length) return setError("Add at least one product.");
    setBusy(true);
    setError(null);
    try {
      onDone(await api.post<Adjustment>("/stock-adjustments", {
        reason,
        approve_now: isOwner && approveNow,
        lines: rows.map((r) => r.product.tracking_type === "serialized"
          ? { product_id: r.product.id, unit_id: r.unit?.id ?? null, unit_new_status: r.newStatus }
          : { product_id: r.product.id, kind: r.kind,
              // Damaged/missing always remove stock; a count correction keeps the sign typed.
              quantity_change: r.kind === "adjustment" ? Number(r.change) || 0 : -Math.abs(Number(r.change) || 0) }),
      }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {error && <Notice tone="error">{error}</Notice>}
      <ul className="space-y-3">
        {rows.map((r) => (
          <li key={r.key} className="rounded-md border border-line p-3">
            <div className="flex items-start justify-between gap-2">
              <p className="font-medium">{r.product.name} <span className="text-sm text-muted num">· {r.product.qty_on_hand} in stock</span></p>
              <button type="button" aria-label={`Remove ${r.product.name}`} onClick={() => setRows((rs) => rs.filter((x) => x.key !== r.key))}
                className="rounded p-1 text-muted hover:text-alert"><Trash2 className="size-4" aria-hidden /></button>
            </div>
            {r.product.tracking_type === "serialized" ? (
              <UnitChoice product={r.product} unit={r.unit} newStatus={r.newStatus}
                onChange={(unit, newStatus) => update(r.key, { unit, newStatus })} />
            ) : (
              <div className="mt-2 grid gap-3 sm:grid-cols-2">
                <SelectInput label="What happened" value={r.kind} onChange={(e) => update(r.key, { kind: e.target.value as AdjRow["kind"] })}>
                  <option value="damage">Damaged (remove)</option>
                  <option value="missing">Missing (remove)</option>
                  <option value="adjustment">Count correction (+ or −)</option>
                </SelectInput>
                <TextInput label={r.kind === "adjustment" ? "Change (e.g. 2 or -2)" : "How many"} inputMode="numeric"
                  value={r.change} onChange={(e) => update(r.key, { change: e.target.value })} />
              </div>
            )}
          </li>
        ))}
      </ul>
      <ProductFinder onPick={(p) => setRows((rs) => [...rs, { key: rowKey++, product: p, change: "", kind: "damage", unit: null, newStatus: "damaged" }])} />
      <TextInput label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Dropped during delivery" />
      {isOwner && <Checkbox label="Apply now (approve my own adjustment)" checked={approveNow} onChange={(e) => setApproveNow(e.target.checked)} />}
      <div className="flex justify-end pt-2">
        <Button type="submit" loading={busy}>{isOwner && approveNow ? "Apply adjustment" : "Send for approval"}</Button>
      </div>
    </form>
  );
}

function UnitChoice({ product, unit, newStatus, onChange }: {
  product: Product; unit: Unit | null; newStatus: AdjRow["newStatus"]; onChange: (u: Unit | null, s: AdjRow["newStatus"]) => void;
}) {
  const [units, setUnits] = useState<Unit[]>([]);
  useEffect(() => {
    Promise.all(["in_stock", "damaged", "missing"].map((st) =>
      api.get<Page<Unit>>(`/units${qs({ product_id: product.id, status: st, page_size: 200 })}`).then((p) => p.items)))
      .then((lists) => setUnits(lists.flat())).catch(() => {});
  }, [product.id]);
  return (
    <div className="mt-2 grid gap-3 sm:grid-cols-2">
      <SelectInput label="Unit" value={unit ? String(unit.id) : ""} onChange={(e) => {
        const u = units.find((x) => String(x.id) === e.target.value) ?? null;
        onChange(u, u?.status === "in_stock" ? "damaged" : "in_stock");
      }}>
        <option value="">Choose…</option>
        {units.map((u) => <option key={u.id} value={u.id}>{u.imei1 ?? u.serial_number} — {UNIT_STATUS_LABELS[u.status]}</option>)}
      </SelectInput>
      <SelectInput label="New status" value={newStatus} disabled={!unit} onChange={(e) => onChange(unit, e.target.value as AdjRow["newStatus"])}>
        {unit?.status === "in_stock" ? (
          <>
            <option value="damaged">Damaged</option>
            <option value="missing">Missing</option>
          </>
        ) : <option value="in_stock">Found — back in stock</option>}
      </SelectInput>
    </div>
  );
}

function ProductFinder({ onPick }: { onPick: (p: Product) => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Product[]>([]);
  useEffect(() => {
    if (!q.trim()) { setResults([]); return; }
    const t = setTimeout(() => {
      api.get<Page<Product>>(`/products${qs({ q: q.trim(), page_size: 8 })}`)
        .then((r) => setResults(r.items.filter((p) => p.tracking_type !== "service").slice(0, 6))).catch(() => {});
    }, 200);
    return () => clearTimeout(t);
  }, [q]);
  return (
    <div className="relative">
      <label htmlFor="adj-product" className="block text-sm font-medium mb-1.5">Add a product</label>
      <Search className="pointer-events-none absolute left-3 top-[2.45rem] size-4 text-muted" aria-hidden />
      <input id="adj-product" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, SKU, barcode or IMEI"
        className="block w-full h-10 rounded-md border border-line bg-white pl-9 pr-3 text-[15px] focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40" />
      {results.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full overflow-hidden rounded-md border border-line bg-white shadow-lg">
          {results.map((p) => (
            <li key={p.id}>
              <button type="button" onClick={() => { onPick(p); setQ(""); setResults([]); }}
                className="flex w-full justify-between gap-2 px-3 py-2 text-left hover:bg-board/5">
                <span className="font-medium">{p.name}</span>
                <span className="text-sm text-muted num">{p.sku} · {p.qty_on_hand} in stock</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
