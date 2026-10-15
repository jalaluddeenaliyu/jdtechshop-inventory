import { Plus } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { initialPeriod, PeriodPicker, type Period } from "../components/Period";
import { Badge, Button, Dialog, Notice, PageHeader, Pager, SelectInput, Spinner, TextInput } from "../components/ui";
import { api, ApiError, qs, type Expense, type Page, type PaymentMethod } from "../lib/api";
import { dateOnly, naira, todayISO } from "../lib/format";
import { ReverseForm } from "./Corrections";

type ExpensePage = Page<Expense> & { amount_total: string };

/** Running costs (rent, power, salaries…) — they turn gross profit into net profit. Owner only. */
export function ExpensesPage() {
  const [period, setPeriod] = useState<Period>(() => initialPeriod("month"));
  const [page, setPage] = useState(1);
  const [data, setData] = useState<ExpensePage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [reversing, setReversing] = useState<Expense | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api.get<ExpensePage>(`/expenses${qs({ date_from: period.from, date_to: period.to, page, page_size: 25 })}`)
      .then(setData).catch((e) => setError(e.message));
  }, [period.from, period.to, page]);
  useEffect(load, [load]);

  return (
    <>
      <PageHeader title="Expenses"
        description="Money spent running the shop. Recording it gives you an honest net profit in Reports and on the dashboard."
        actions={<Button onClick={() => { setFlash(null); setAdding(true); }}><Plus className="size-4" aria-hidden /> Add expense</Button>} />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <PeriodPicker value={period} onChange={(p) => { setPeriod(p); setPage(1); }} />
        {data && <p className="text-muted">Total for the period: <span className="num font-medium text-ink">{naira(data.amount_total)}</span></p>}
      </div>
      {flash && <div className="mb-4"><Notice tone="success">{flash}</Notice></div>}
      {error && <Notice tone="error">{error}</Notice>}
      {!data && !error && <Spinner />}
      {data && (
        <>
          <div className="relative overflow-x-auto rounded-lg border border-line bg-white">
            <table className="w-full text-left text-[15px]">
              <thead className="border-b border-line text-sm text-muted">
                <tr>
                  <th className="px-4 py-3 font-medium">Date</th>
                  <th className="px-4 py-3 font-medium">Category</th>
                  <th className="hidden md:table-cell px-4 py-3 font-medium">Note</th>
                  <th className="px-4 py-3 font-medium text-right">Amount</th>
                  <th className="px-4 py-3"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((e) => (
                  <tr key={e.id} className={`border-b border-line last:border-0 align-top ${e.reversed ? "text-muted" : ""}`}>
                    <td className="px-4 py-3 num whitespace-nowrap">{dateOnly(e.spent_on)}</td>
                    <td className="px-4 py-3">{e.category}{e.method_name && <div className="text-sm text-muted">{e.method_name}</div>}</td>
                    <td className="hidden md:table-cell px-4 py-3 text-muted">{e.note ?? ""}{e.reversed && <div><Badge tone="off">Reversed: {e.reversal_reason}</Badge></div>}</td>
                    <td className={`px-4 py-3 text-right num whitespace-nowrap ${e.reversed ? "line-through" : ""}`}>{naira(e.amount)}</td>
                    <td className="px-4 py-2 text-right">
                      {!e.reversed && <Button variant="ghost" className="h-8 text-alert" onClick={() => { setFlash(null); setReversing(e); }}>Reverse</Button>}
                    </td>
                  </tr>
                ))}
                {!data.items.length && <tr><td colSpan={5} className="px-4 py-10 text-center text-muted">No expenses recorded for this period.</td></tr>}
              </tbody>
            </table>
          </div>
          <Pager page={page} pageSize={25} total={data.total} noun={["expense", "expenses"]} onPage={setPage} />
        </>
      )}
      <Dialog open={adding} title="Add expense" onClose={() => setAdding(false)}>
        {adding && <ExpenseForm onDone={(e) => { setAdding(false); setFlash(`Recorded ${naira(e.amount)} for ${e.category}.`); load(); }} />}
      </Dialog>
      <Dialog open={!!reversing} title="Reverse expense" onClose={() => setReversing(null)}>
        {reversing && (
          <ReverseForm<Expense> endpoint={`/expenses/${reversing.id}/reverse`} confirmLabel="Reverse expense"
            explain={`${naira(reversing.amount)} for ${reversing.category} on ${dateOnly(reversing.spent_on)} will no longer count against profit. Use this for an expense recorded by mistake.`}
            onDone={() => { setReversing(null); setFlash("Expense reversed."); load(); }} />
        )}
      </Dialog>
    </>
  );
}

function ExpenseForm({ onDone }: { onDone: (e: Expense) => void }) {
  const [categories, setCategories] = useState<string[]>([]);
  const [methods, setMethods] = useState<PaymentMethod[]>([]);
  const [form, setForm] = useState({ category: "", amount: "", spent_on: todayISO(), method_id: "", note: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get<string[]>("/expenses/categories").then(setCategories).catch(() => {});
    api.get<PaymentMethod[]>("/payment-methods").then((m) => {
      setMethods(m);
      setForm((f) => ({ ...f, method_id: m[0] ? String(m[0].id) : "" }));
    }).catch(() => {});
  }, []);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    setErrors({});
    try {
      onDone(await api.post<Expense>("/expenses", {
        ...form, amount: form.amount.replaceAll(",", "").trim(), method_id: form.method_id ? Number(form.method_id) : null,
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
      {error && <Notice tone="error">{error}</Notice>}
      <TextInput label="Category" list="expense-categories" value={form.category} error={errors.category}
        onChange={set("category")} placeholder="e.g. Rent" hint="Pick one or type your own." />
      <datalist id="expense-categories">{categories.map((c) => <option key={c} value={c} />)}</datalist>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextInput label="Amount (₦)" inputMode="decimal" value={form.amount} error={errors.amount} onChange={set("amount")} placeholder="0.00" />
        <TextInput label="Date" type="date" max={todayISO()} value={form.spent_on} error={errors.spent_on} onChange={set("spent_on")} />
      </div>
      <SelectInput label="Paid by" value={form.method_id} onChange={set("method_id")}>
        <option value="">Not stated</option>
        {methods.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
      </SelectInput>
      <TextInput label="Note" value={form.note} error={errors.note} onChange={set("note")} placeholder="e.g. October rent" />
      <div className="flex justify-end pt-2"><Button type="submit" loading={busy}>Add expense</Button></div>
    </form>
  );
}
