import { ArrowLeft, Banknote, Printer } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Badge, Button, Dialog, Notice, SelectInput, Spinner, TextInput } from "../components/ui";
import { Logo } from "../components/Logo";
import {
  api,
  ApiError,
  newKey,
  qs,
  type BusinessSettings,
  type Customer,
  type CustomerDebt,
  type CustomerPayment,
  type Page,
  type PaymentMethod,
} from "../lib/api";
import { useAuth } from "../lib/auth";
import { dateOnly, dateTime, naira, phone, todayISO } from "../lib/format";
import { ReverseForm } from "./Corrections";

/** What a customer owes, sale by sale, with their payments and a "Receive payment" button. */
export function CustomerStatement({ customer, onChanged }: { customer: Customer; onChanged: () => void }) {
  const [debt, setDebt] = useState<CustomerDebt | null>(null);
  const [payments, setPayments] = useState<Page<CustomerPayment> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paying, setPaying] = useState<{ saleId: number | null } | null>(null);
  const [done, setDone] = useState<CustomerPayment | null>(null);
  const [reversing, setReversing] = useState<CustomerPayment | null>(null);
  const isOwner = useAuth().user?.role === "owner";

  const load = useCallback(() => {
    api.get<CustomerDebt>(`/customers/${customer.id}/debt`).then(setDebt).catch((e) => setError(e.message));
    api.get<Page<CustomerPayment>>(`/customer-payments${qs({ customer_id: customer.id, page_size: 20 })}`)
      .then(setPayments).catch((e) => setError(e.message));
  }, [customer.id]);
  useEffect(load, [load]);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!debt || !payments) return <Spinner />;
  const owes = Number(debt.owed) > 0;

  return (
    <div className="space-y-5">
      {done && (
        <Notice tone="success">
          Received {naira(done.amount)} from {done.customer_name}. {Number(done.customer_owes_now) > 0 ? `Still owes ${naira(done.customer_owes_now)}.` : "Nothing owed now."}{" "}
          <Link to={`/customer-payments/${done.id}?print=1`} className="inline-flex items-center gap-1 font-medium underline">
            <Printer className="size-4" aria-hidden /> Print payment receipt
          </Link>
        </Notice>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        <Figure label="Bought" value={naira(debt.sales_total)} />
        <Figure label="Paid" value={naira(debt.paid_total)} />
        <Figure label="Owes" value={naira(debt.owed)} tone={owes ? "alert" : undefined}
          note={Number(debt.overdue) > 0 ? `${naira(debt.overdue)} overdue` : undefined} />
      </div>

      <section>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold">Unpaid sales</h3>
          {owes && (
            <Button className="h-9" onClick={() => { setDone(null); setPaying({ saleId: null }); }}>
              <Banknote className="size-4" aria-hidden /> Receive payment
            </Button>
          )}
        </div>
        {debt.unpaid_sales.length ? (
          <ul className="divide-y divide-line rounded-md border border-line">
            {debt.unpaid_sales.map((s) => (
              <li key={s.sale_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                <span>
                  <Link to={`/sales/${s.sale_id}`} className="font-medium num hover:underline">{s.receipt_no}</Link>
                  <span className="ml-2 text-sm text-muted">{dateOnly(s.business_date)}</span>
                  {s.due_date && (
                    <span className="ml-2">
                      {s.overdue ? <Badge tone="alert">Overdue since {dateOnly(s.due_date)}</Badge>
                        : <Badge tone="neutral">Due {dateOnly(s.due_date)}</Badge>}
                    </span>
                  )}
                </span>
                <span className="flex items-center gap-3">
                  <span className="text-right num text-sm">
                    <span className="block font-medium text-[15px]">{naira(s.balance)}</span>
                    <span className="text-muted">of {naira(s.total)}</span>
                  </span>
                  <Button variant="ghost" className="h-8" onClick={() => { setDone(null); setPaying({ saleId: s.sale_id }); }}>Pay this</Button>
                </span>
              </li>
            ))}
          </ul>
        ) : <p className="text-muted">Nothing owed.</p>}
      </section>

      <section>
        <h3 className="mb-2 font-semibold">Recent payments</h3>
        {payments.items.length ? (
          <ul className="divide-y divide-line rounded-md border border-line">
            {payments.items.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                <span>
                  <span className="num">{dateOnly(p.received_on)}</span>
                  <span className="ml-2 text-sm text-muted">
                    {p.method_name} · {p.at_till ? `at the till, ${p.allocations[0]?.receipt_no}` : p.allocations.map((a) => a.receipt_no).join(", ")}
                  </span>
                </span>
                <span className="flex items-center gap-2">
                  <span className={`num font-medium ${p.reversed ? "line-through text-muted" : ""}`}>{naira(p.amount)}</span>
                  {p.reversed && <Badge tone="off">Reversed: {p.reversal_reason}</Badge>}
                  {!p.at_till && <Link to={`/customer-payments/${p.id}`} className="text-sm text-board underline">Receipt</Link>}
                  {isOwner && !p.reversed && (
                    <Button variant="ghost" className="h-8 text-alert" onClick={() => { setDone(null); setReversing(p); }}>Reverse</Button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        ) : <p className="text-muted">No payments yet.</p>}
      </section>

      <Dialog open={!!reversing} title="Reverse payment" onClose={() => setReversing(null)}>
        {reversing && (
          <ReverseForm<CustomerPayment> endpoint={`/customer-payments/${reversing.id}/reverse`} confirmLabel="Reverse payment"
            explain={`The ${naira(reversing.amount)} payment of ${dateOnly(reversing.received_on)} will be marked reversed, and ${customer.name} will owe it again. Use this only for a payment recorded by mistake.`}
            onDone={() => { setReversing(null); load(); onChanged(); }} />
        )}
      </Dialog>
      <Dialog open={!!paying} title={`Receive payment from ${customer.name}`} onClose={() => setPaying(null)}>
        {paying && (
          <ReceivePaymentForm debt={debt} saleId={paying.saleId} onDone={(p) => {
            setPaying(null);
            setDone(p);
            load();
            onChanged();
          }} />
        )}
      </Dialog>
    </div>
  );
}

function Figure({ label, value, tone, note }: { label: string; value: string; tone?: "alert"; note?: string }) {
  return (
    <div className="rounded-md border border-line px-3 py-2">
      <p className="text-sm text-muted">{label}</p>
      <p className={`num font-semibold ${tone === "alert" ? "text-alert" : ""}`}>{value}</p>
      {note && <p className="text-sm text-alert">{note}</p>}
    </div>
  );
}

function ReceivePaymentForm({ debt, saleId, onDone }: { debt: CustomerDebt; saleId: number | null; onDone: (p: CustomerPayment) => void }) {
  const [methods, setMethods] = useState<PaymentMethod[]>([]);
  const [form, setForm] = useState({
    sale_id: saleId ? String(saleId) : "",
    amount: "",
    method_id: "",
    received_on: todayISO(),
    reference: "",
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
  }, []);

  const chosen = debt.unpaid_sales.find((s) => String(s.sale_id) === form.sale_id);
  const limit = chosen ? chosen.balance : debt.owed;
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError(null);
    const amount = form.amount.replaceAll(",", "").trim();
    if (!amount || !(Number(amount) > 0)) {
      setErrors({ amount: "Enter the amount received." });
      return;
    }
    setErrors({});
    setBusy(true);
    try {
      onDone(await api.post<CustomerPayment>("/customer-payments", {
        customer_id: debt.customer_id,
        sale_id: form.sale_id ? Number(form.sale_id) : null,
        amount,
        method_id: Number(form.method_id),
        received_on: form.received_on,
        reference: form.reference,
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
        {debt.customer_name} owes <span className="num font-medium text-ink">{naira(debt.owed)}</span> in all.
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      <SelectInput label="For" value={form.sale_id} onChange={set("sale_id")}
        hint={form.sale_id ? undefined : "Pays off the oldest sales first."}>
        <option value="">The balance (oldest sales first)</option>
        {debt.unpaid_sales.map((s) => (
          <option key={s.sale_id} value={s.sale_id}>{s.receipt_no} — {naira(s.balance)} left{s.overdue ? " (overdue)" : ""}</option>
        ))}
      </SelectInput>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextInput label="Amount received (₦)" inputMode="decimal" placeholder="0.00" data-autofocus value={form.amount}
          error={errors.amount} onChange={set("amount")} hint={`Up to ${naira(limit)}.`} />
        <SelectInput label="Paid by" value={form.method_id} onChange={set("method_id")}>
          {methods.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </SelectInput>
        <TextInput label="Date received" type="date" max={todayISO()} value={form.received_on}
          error={errors.received_on} onChange={set("received_on")} />
        <TextInput label="Reference" placeholder="e.g. transfer ID" value={form.reference}
          error={errors.reference} onChange={set("reference")} />
      </div>
      <div className="flex flex-wrap justify-between gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={() => setForm({ ...form, amount: Number(limit).toFixed(2) })}>
          Pays it all ({naira(limit)})
        </Button>
        <Button type="submit" loading={busy}>Record payment</Button>
      </div>
    </form>
  );
}

/** /customer-payments/:id — proof of a repayment, ready to print (?print=1 opens the print dialog). */
export function PaymentReceiptPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const [payment, setPayment] = useState<CustomerPayment | null>(null);
  const [shop, setShop] = useState<BusinessSettings | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<CustomerPayment>(`/customer-payments/${id}`).then(setPayment).catch((e) => setError(e.message));
    api.get<BusinessSettings>("/settings").then(setShop).catch(() => {});
  }, [id]);
  const autoPrint = params.get("print") === "1";
  useEffect(() => {
    if (autoPrint && payment && shop) setTimeout(() => window.print(), 300);
  }, [autoPrint, payment, shop]);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!payment || !shop) return <Spinner />;

  return (
    <>
      <div className="no-print mb-4 flex flex-wrap items-center justify-between gap-3">
        <Link to="/customers" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Customers
        </Link>
        <Button onClick={() => window.print()}><Printer className="size-4" aria-hidden /> Print receipt</Button>
      </div>
      <article className="mx-auto w-full max-w-sm rounded-lg border border-line bg-white p-5 text-[14px] leading-snug print:max-w-none print:border-0 print:p-0">
        <header className="text-center">
          <h1><Logo on="dark" alt={shop.business_name} className="mx-auto h-16 print:h-14" /></h1>
          {shop.address && <p>{shop.address}</p>}
          {shop.phone && <p className="num">{shop.phone}</p>}
          <p className="mt-2 font-semibold uppercase tracking-wide">Payment received</p>
        </header>
        <dl className="mt-3 border-t border-dashed border-ink/40 pt-2 space-y-0.5">
          <Row label="Receipt" value={payment.reference_no} />
          <Row label="Date" value={dateTime(payment.received_at)} />
          <Row label="From" value={`${payment.customer_name}${payment.customer_phone ? ` · ${phone(payment.customer_phone)}` : ""}`} />
          <Row label="Received by" value={payment.recorded_by_name ?? "—"} />
          <Row label="Paid by" value={`${payment.method_name}${payment.reference ? ` · ${payment.reference}` : ""}`} />
        </dl>
        <dl className="mt-3 border-t border-dashed border-ink/40 pt-2 space-y-0.5">
          {payment.allocations.map((a) => <Row key={a.sale_id} label={`Towards ${a.receipt_no}`} value={naira(a.amount)} />)}
          <Row label="AMOUNT PAID" value={naira(payment.amount)} strong />
          <Row label={`Owes now (${dateOnly(todayISO())})`} value={naira(payment.customer_owes_now)} />
        </dl>
        {shop.receipt_footer && <p className="mt-4 border-t border-dashed border-ink/40 pt-2 text-center">{shop.receipt_footer}</p>}
      </article>
    </>
  );
}

function Row({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={`flex justify-between gap-3 ${strong ? "font-bold text-[15px]" : ""}`}>
      <dt>{label}</dt>
      <dd className="num text-right">{value}</dd>
    </div>
  );
}
