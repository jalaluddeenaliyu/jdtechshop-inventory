import { Banknote, History, Pencil, Plus, Search } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import {
  Badge,
  Button,
  Checkbox,
  Dialog,
  Notice,
  PageHeader,
  SelectInput,
  Spinner,
  TextArea,
  TextInput,
} from "../components/ui";
import {
  api,
  ApiError,
  newKey,
  qs,
  type Page,
  type PaymentMethod,
  type Purchase,
  type Supplier,
  type SupplierPayment,
} from "../lib/api";
import { dateOnly, naira, PURCHASE_STATUS_LABELS, todayISO } from "../lib/format";
import { ReverseForm } from "./Corrections";

type Mode =
  | { kind: "add" }
  | { kind: "edit"; supplier: Supplier }
  | { kind: "pay"; supplier: Supplier }
  | { kind: "history"; supplier: Supplier }
  | null;

export function SuppliersPage() {
  const [items, setItems] = useState<Supplier[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [owingOnly, setOwingOnly] = useState(false);
  const [showOff, setShowOff] = useState(false);
  const [mode, setMode] = useState<Mode>(null);
  const [flash, setFlash] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback(() => {
    setError(null);
    api.get<Supplier[]>(`/suppliers${qs({ q, owing_only: owingOnly, include_inactive: showOff })}`)
      .then(setItems)
      .catch((e) => setError(e.message));
  }, [q, owingOnly, showOff]);
  useEffect(load, [load]);

  function done(message: string) {
    setMode(null);
    setFlash(message);
    load();
  }

  const owedTotal = (items ?? []).reduce((sum, s) => sum + Number(s.balance), 0);

  return (
    <>
      <PageHeader
        title="Suppliers"
        description="Who you buy from and what you owe them. Balances are worked out from posted purchases and payments — they are never typed in."
        actions={
          <Button onClick={() => { setFlash(null); setMode({ kind: "add" }); }}>
            <Plus className="size-4" aria-hidden /> Add supplier
          </Button>
        }
      />
      {flash && <div className="mb-4"><Notice tone="success">{flash}</Notice></div>}

      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="relative flex-1 min-w-60 max-w-md">
          <label htmlFor="supplier-search" className="sr-only">Search suppliers</label>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" aria-hidden />
          <input id="supplier-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name or phone"
            className="block w-full h-10 rounded-md border border-line bg-white pl-9 pr-3 text-[15px] placeholder:text-muted/70
              focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40" />
        </div>
        <Checkbox label="Only suppliers I owe" checked={owingOnly} onChange={(e) => setOwingOnly(e.target.checked)} />
        <Checkbox label="Show turned-off suppliers" checked={showOff} onChange={(e) => setShowOff(e.target.checked)} />
      </div>

      {error && <Notice tone="error">{error}</Notice>}
      {!items && !error && <Spinner />}
      {items && (
        <>
          <div className="relative overflow-x-auto rounded-lg border border-line bg-white">
            <table className="w-full text-left text-[15px]">
              <thead className="border-b border-line text-sm text-muted">
                <tr>
                  <th className="px-4 py-3 font-medium">Supplier</th>
                  <th className="hidden md:table-cell px-4 py-3 font-medium text-right">Bought</th>
                  <th className="hidden md:table-cell px-4 py-3 font-medium text-right">Paid</th>
                  <th className="px-4 py-3 font-medium text-right">You owe</th>
                  <th className="px-4 py-3"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {items.map((s) => (
                  <tr key={s.id} className={`border-b border-line last:border-0 align-top ${s.is_active ? "" : "text-muted"}`}>
                    <td className="px-4 py-3 min-w-44">
                      <div className="font-medium">
                        {s.name}
                        {!s.is_active && <span className="ml-2"><Badge tone="off">Turned off</Badge></span>}
                      </div>
                      <div className="text-sm text-muted">{[s.phone, s.address].filter(Boolean).join(" · ") || "—"}</div>
                    </td>
                    <td className="hidden md:table-cell px-4 py-3 text-right num whitespace-nowrap text-muted">{naira(s.purchases_total)}</td>
                    <td className="hidden md:table-cell px-4 py-3 text-right num whitespace-nowrap text-muted">{naira(s.paid_total)}</td>
                    <td className={`px-4 py-3 text-right num whitespace-nowrap font-medium ${Number(s.balance) > 0 ? "text-alert" : ""}`}>
                      {naira(s.balance)}
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex justify-end gap-1">
                        {Number(s.balance) > 0 && (
                          <Button variant="ghost" className="h-8" onClick={() => { setFlash(null); setMode({ kind: "pay", supplier: s }); }}>
                            <Banknote className="size-4" aria-hidden /> Pay
                          </Button>
                        )}
                        <Button variant="ghost" className="h-8" onClick={() => setMode({ kind: "history", supplier: s })}>
                          <History className="size-4" aria-hidden /> History
                        </Button>
                        <Button variant="ghost" className="h-8" onClick={() => { setFlash(null); setMode({ kind: "edit", supplier: s }); }}>
                          <Pencil className="size-4" aria-hidden /> Edit
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!items.length && (
                  <tr><td colSpan={5} className="px-4 py-10 text-center text-muted">
                    {q || owingOnly ? "No suppliers match." : "No suppliers yet. Use “Add supplier” to create one."}
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
          {items.length > 0 && (
            <p className="mt-3 text-sm text-muted">
              Total owed to the suppliers shown: <span className="num font-medium text-ink">{naira(owedTotal)}</span>
            </p>
          )}
        </>
      )}

      <Dialog open={mode?.kind === "add"} title="Add supplier" onClose={() => setMode(null)}>
        {mode?.kind === "add" && <SupplierForm onDone={(s) => done(`Added ${s.name}.`)} />}
      </Dialog>
      <Dialog open={mode?.kind === "edit"} title="Edit supplier" onClose={() => setMode(null)}>
        {mode?.kind === "edit" && <SupplierForm supplier={mode.supplier} onDone={(s) => done(`Saved ${s.name}.`)} />}
      </Dialog>
      <Dialog open={mode?.kind === "pay"} title="Record payment to supplier" onClose={() => setMode(null)}>
        {mode?.kind === "pay" && (
          <PaymentForm supplier={mode.supplier}
            onDone={(p) => done(`Recorded ${naira(p.amount)} paid to ${p.supplier_name}.`)} />
        )}
      </Dialog>
      <Dialog wide open={mode?.kind === "history"} title={mode?.kind === "history" ? mode.supplier.name : ""}
        onClose={() => setMode(null)}>
        {mode?.kind === "history" && <SupplierHistory supplier={mode.supplier} />}
      </Dialog>
    </>
  );
}

function SupplierForm({ supplier, onDone }: { supplier?: Supplier; onDone: (s: Supplier) => void }) {
  const [form, setForm] = useState({
    name: supplier?.name ?? "",
    phone: supplier?.phone ?? "",
    email: supplier?.email ?? "",
    address: supplier?.address ?? "",
    notes: supplier?.notes ?? "",
    is_active: supplier?.is_active ?? true,
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    setErrors({});
    const { is_active, ...fields } = form;
    try {
      onDone(supplier
        ? await api.patch<Supplier>(`/suppliers/${supplier.id}`, { ...fields, is_active })
        : await api.post<Supplier>("/suppliers", fields));
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(err.fieldErrors);
        if (!Object.keys(err.fieldErrors).length) setError(err.message);
      } else setError("Could not save. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {error && <Notice tone="error">{error}</Notice>}
      <TextInput label="Name" value={form.name} error={errors.name} onChange={set("name")} autoFocus={!supplier} />
      <div className="grid gap-4 sm:grid-cols-2">
        <TextInput label="Phone" type="tel" value={form.phone} error={errors.phone} onChange={set("phone")} />
        <TextInput label="Email" type="email" value={form.email} error={errors.email} onChange={set("email")} />
      </div>
      <TextInput label="Address" value={form.address} error={errors.address} onChange={set("address")} />
      <TextArea label="Notes" value={form.notes} error={errors.notes} onChange={set("notes")} rows={2}
        placeholder="e.g. Bank details, delivery days" />
      {supplier && (
        <Checkbox label="Supplier is active" checked={form.is_active}
          hint="Turned-off suppliers cannot be chosen for new purchases. Their history and balance are kept."
          onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
      )}
      <div className="flex justify-end pt-2">
        <Button type="submit" loading={busy}>{supplier ? "Save changes" : "Add supplier"}</Button>
      </div>
    </form>
  );
}

/** Pay a supplier, either against one purchase or "on account". Never more than is owed. */
export function PaymentForm({
  supplier,
  purchase,
  onDone,
}: {
  supplier: Pick<Supplier, "id" | "name" | "balance">;
  purchase?: Pick<Purchase, "id" | "reference" | "balance_due">;
  onDone: (p: SupplierPayment) => void;
}) {
  const [methods, setMethods] = useState<PaymentMethod[]>([]);
  const [unpaid, setUnpaid] = useState<Purchase[]>([]);
  const [form, setForm] = useState({
    purchase_id: purchase ? String(purchase.id) : "",
    amount: "",
    method_id: "",
    paid_on: todayISO(),
    reference: "",
    notes: "",
  });
  const [key] = useState(newKey); // one key per form: a double click records one payment
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<PaymentMethod[]>("/payment-methods").then((m) => {
      setMethods(m);
      setForm((f) => (f.method_id ? f : { ...f, method_id: m[0] ? String(m[0].id) : "" }));
    }).catch(() => {});
    if (!purchase) {
      api.get<Page<Purchase>>(`/purchases${qs({ supplier_id: supplier.id, unpaid_only: true, page_size: 100 })}`)
        .then((p) => setUnpaid(p.items)).catch(() => {});
    }
  }, [supplier.id, purchase]);

  const chosen = purchase ?? unpaid.find((p) => String(p.id) === form.purchase_id);
  const limit = chosen ? Math.min(Number(chosen.balance_due), Number(supplier.balance)) : Number(supplier.balance);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError(null);
    const amount = form.amount.replaceAll(",", "").trim();
    if (!amount || !(Number(amount) > 0)) {
      setErrors({ amount: "Enter the amount paid." });
      return;
    }
    setErrors({});
    setBusy(true);
    try {
      onDone(await api.post<SupplierPayment>("/supplier-payments", {
        supplier_id: supplier.id,
        purchase_id: form.purchase_id ? Number(form.purchase_id) : null,
        amount,
        method_id: Number(form.method_id),
        paid_on: form.paid_on,
        reference: form.reference,
        notes: form.notes,
        idempotency_key: key,
      }));
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(err.fieldErrors);
        if (!Object.keys(err.fieldErrors).length) setError(err.message);
      } else setError("Could not save. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <p className="text-muted">
        You owe <span className="font-medium text-ink">{supplier.name}</span>{" "}
        <span className="num font-medium text-ink">{naira(supplier.balance)}</span> in all.
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      {purchase ? (
        <p className="text-sm">For purchase <span className="font-medium">{purchase.reference}</span>{" "}
          (<span className="num">{naira(purchase.balance_due)}</span> left to pay).</p>
      ) : (
        <SelectInput label="For" value={form.purchase_id} onChange={set("purchase_id")}
          hint="Choose a purchase, or pay towards the overall balance.">
          <option value="">The overall balance (on account)</option>
          {unpaid.map((p) => (
            <option key={p.id} value={p.id}>
              {p.reference}{p.supplier_invoice_no ? ` · invoice ${p.supplier_invoice_no}` : ""} — {naira(p.balance_due)} left
            </option>
          ))}
        </SelectInput>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <TextInput label="Amount (₦)" inputMode="decimal" value={form.amount} error={errors.amount}
          onChange={set("amount")} placeholder="0.00" data-autofocus
          hint={`Up to ${naira(limit)}.`} />
        <SelectInput label="Paid by" value={form.method_id} error={errors.method_id} onChange={set("method_id")}>
          {methods.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </SelectInput>
        <TextInput label="Date paid" type="date" value={form.paid_on} max={todayISO()} error={errors.paid_on}
          onChange={set("paid_on")} />
        <TextInput label="Reference" value={form.reference} error={errors.reference} onChange={set("reference")}
          placeholder="e.g. transfer ID" />
      </div>
      <TextInput label="Notes" value={form.notes} error={errors.notes} onChange={set("notes")} />
      <div className="flex justify-end pt-2">
        <Button type="submit" loading={busy}>Record payment</Button>
      </div>
    </form>
  );
}

function SupplierHistory({ supplier }: { supplier: Supplier }) {
  const [purchases, setPurchases] = useState<Page<Purchase> | null>(null);
  const [payments, setPayments] = useState<Page<SupplierPayment> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reversing, setReversing] = useState<SupplierPayment | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    api.get<Page<Purchase>>(`/purchases${qs({ supplier_id: supplier.id, page_size: 50 })}`).then(setPurchases)
      .catch((e) => setError(e.message));
    api.get<Page<SupplierPayment>>(`/supplier-payments${qs({ supplier_id: supplier.id, page_size: 50 })}`)
      .then(setPayments).catch((e) => setError(e.message));
  }, [supplier.id, reload]);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!purchases || !payments) return <Spinner />;

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        {([["Bought (posted)", supplier.purchases_total], ["Paid", supplier.paid_total], ["You owe", supplier.balance]] as const)
          .map(([label, value]) => (
            <div key={label} className="rounded-md border border-line px-3 py-2">
              <p className="text-sm text-muted">{label}</p>
              <p className="num font-semibold">{naira(value)}</p>
            </div>
          ))}
      </div>
      <section>
        <h3 className="mb-2 font-semibold">Purchases</h3>
        <ul className="divide-y divide-line rounded-md border border-line">
          {purchases.items.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <span>
                <Link to={`/purchases/${p.id}`} className="font-medium hover:underline">{p.reference}</Link>
                <span className="ml-2 text-sm text-muted">{dateOnly(p.purchase_date)}</span>
                {p.status !== "posted" && <span className="ml-2"><Badge tone="pad">{PURCHASE_STATUS_LABELS[p.status]}</Badge></span>}
              </span>
              <span className="num text-sm">
                {naira(p.total)}
                {p.status === "posted" && Number(p.balance_due) > 0 && (
                  <span className="ml-2 text-alert">{naira(p.balance_due)} due</span>
                )}
              </span>
            </li>
          ))}
          {!purchases.items.length && <li className="px-3 py-4 text-center text-muted">No purchases yet.</li>}
        </ul>
      </section>
      <section>
        <h3 className="mb-2 font-semibold">Payments</h3>
        <ul className="divide-y divide-line rounded-md border border-line">
          {payments.items.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <span>
                <span className="num">{dateOnly(p.paid_on)}</span>
                <span className="ml-2 text-sm text-muted">
                  {p.method_name}
                  {p.purchase_reference ? ` · ${p.purchase_reference}` : " · on account"}
                  {p.reference ? ` · ${p.reference}` : ""}
                </span>
              </span>
              <span className="flex items-center gap-2">
                <span className={`num font-medium ${p.reversed ? "line-through text-muted" : ""}`}>{naira(p.amount)}</span>
                {p.reversed ? <Badge tone="off">Reversed</Badge> : (
                  <Button variant="ghost" className="h-8 text-alert" onClick={() => setReversing(p)}>Reverse</Button>
                )}
              </span>
            </li>
          ))}
          {!payments.items.length && <li className="px-3 py-4 text-center text-muted">No payments yet.</li>}
        </ul>
      </section>
      <Dialog open={!!reversing} title="Reverse payment" onClose={() => setReversing(null)}>
        {reversing && (
          <ReverseForm<SupplierPayment> endpoint={`/supplier-payments/${reversing.id}/reverse`} confirmLabel="Reverse payment"
            explain={`The ${naira(reversing.amount)} payment of ${dateOnly(reversing.paid_on)} will be marked reversed and owed again. Use this only for a payment recorded by mistake.`}
            onDone={() => { setReversing(null); setReload((n) => n + 1); }} />
        )}
      </Dialog>
    </div>
  );
}
