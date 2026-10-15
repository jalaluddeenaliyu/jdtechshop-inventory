import { FileText, Pencil, Plus, Search } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Badge, Button, Checkbox, Dialog, Notice, PageHeader, Pager, Spinner, TextArea, TextInput } from "../components/ui";
import { api, ApiError, qs, type Customer, type Page } from "../lib/api";
import { useAuth } from "../lib/auth";
import { dateTime, naira, phone } from "../lib/format";
import { CustomerStatement } from "./Debts";

const PAGE_SIZE = 25;

type Mode = { kind: "add" } | { kind: "edit"; customer: Customer } | { kind: "statement"; customer: Customer } | null;

export function CustomersPage() {
  const { user } = useAuth();
  const isOwner = user?.role === "owner";
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [showOff, setShowOff] = useState(false);
  const [owingOnly, setOwingOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<Customer> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>(null);
  const [flash, setFlash] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => {
      setQ(search.trim());
      setPage(1);
    }, 250);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback(() => {
    setError(null);
    api.get<Page<Customer>>(`/customers${qs({ q, include_inactive: showOff, owing_only: owingOnly, page, page_size: PAGE_SIZE })}`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [q, showOff, owingOnly, page]);
  useEffect(load, [load]);

  function done(message: string) {
    setMode(null);
    setFlash(message);
    load();
  }

  return (
    <>
      <PageHeader
        title="Customers"
        description="People who buy from the shop. Sales to people you don't need to record go to the built-in walk-in customer."
        actions={
          <Button onClick={() => { setFlash(null); setMode({ kind: "add" }); }}>
            <Plus className="size-4" aria-hidden /> Add customer
          </Button>
        }
      />
      {flash && <div className="mb-4"><Notice tone="success">{flash}</Notice></div>}

      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="relative flex-1 min-w-60 max-w-md">
          <label htmlFor="customer-search" className="sr-only">Search customers</label>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" aria-hidden />
          <input id="customer-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name, phone or email"
            className="block w-full h-10 rounded-md border border-line bg-white pl-9 pr-3 text-[15px] placeholder:text-muted/70
              focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40" />
        </div>
        <Checkbox label="Only customers who owe" checked={owingOnly}
          onChange={(e) => { setOwingOnly(e.target.checked); setPage(1); }} />
        {isOwner && (
          <Checkbox label="Show turned-off customers" checked={showOff}
            onChange={(e) => { setShowOff(e.target.checked); setPage(1); }} />
        )}
      </div>

      {error && <Notice tone="error">{error}</Notice>}
      {!data && !error && <Spinner />}
      {data && (
        <>
          <div className="relative overflow-x-auto rounded-lg border border-line bg-white">
            <table className="w-full text-left text-[15px]">
              <thead className="border-b border-line text-sm text-muted">
                <tr>
                  <th className="px-4 py-3 font-medium">Customer</th>
                  <th className="px-4 py-3 font-medium">Phone</th>
                  <th className="hidden xl:table-cell px-4 py-3 font-medium">Email / address</th>
                  <th className="px-4 py-3 font-medium text-right">Owes</th>
                  <th className="hidden 2xl:table-cell px-4 py-3 font-medium">Added</th>
                  <th className="px-4 py-3"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((c) => (
                  <tr key={c.id} className={`border-b border-line last:border-0 align-top ${c.is_active ? "" : "text-muted"}`}>
                    <td className="px-4 py-3 min-w-40">
                      <div className="font-medium">
                        {c.name}
                        {!c.is_active && <span className="ml-2"><Badge tone="off">Turned off</Badge></span>}
                      </div>
                      {c.notes && <div className="text-sm text-muted line-clamp-1">{c.notes}</div>}
                    </td>
                    <td className="px-4 py-3 num whitespace-nowrap">
                      {c.phone ? <a href={`tel:${c.phone}`} className="hover:underline">{phone(c.phone)}</a> : "—"}
                    </td>
                    <td className="hidden xl:table-cell px-4 py-3 text-muted">
                      {[c.email, c.address].filter(Boolean).join(" · ") || "—"}
                    </td>
                    <td className="px-4 py-3 text-right num whitespace-nowrap">
                      {Number(c.owed) > 0 ? (
                        <>
                          <span className="font-medium text-alert">{naira(c.owed)}</span>
                          {Number(c.overdue) > 0 && <div className="text-sm text-alert">{naira(c.overdue)} overdue</div>}
                        </>
                      ) : <span className="text-muted">—</span>}
                    </td>
                    <td className="hidden 2xl:table-cell px-4 py-3 num whitespace-nowrap text-muted">{dateTime(c.created_at)}</td>
                    <td className="px-4 py-2">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" className="h-8" onClick={() => { setFlash(null); setMode({ kind: "statement", customer: c }); }}>
                          <FileText className="size-4" aria-hidden /> Statement
                        </Button>
                        <Button variant="ghost" className="h-8" onClick={() => { setFlash(null); setMode({ kind: "edit", customer: c }); }}>
                          <Pencil className="size-4" aria-hidden /> Edit
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!data.items.length && (
                  <tr><td colSpan={6} className="px-4 py-10 text-center text-muted">
                    {q || owingOnly ? "No customers match." : "No customers yet. Use “Add customer” to record one."}
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
          <Pager page={page} pageSize={PAGE_SIZE} total={data.total} noun={["customer", "customers"]} onPage={setPage} />
        </>
      )}

      <Dialog open={mode?.kind === "add"} title="Add customer" onClose={() => setMode(null)}>
        {mode?.kind === "add" && <CustomerForm canTurnOff={false} onDone={(c) => done(`Added ${c.name}.`)} />}
      </Dialog>
      <Dialog wide open={mode?.kind === "statement"} title={mode?.kind === "statement" ? mode.customer.name : ""}
        onClose={() => { setMode(null); load(); }}>
        {mode?.kind === "statement" && <CustomerStatement customer={mode.customer} onChanged={load} />}
      </Dialog>
      <Dialog open={mode?.kind === "edit"} title="Edit customer" onClose={() => setMode(null)}>
        {mode?.kind === "edit" && (
          <CustomerForm customer={mode.customer} canTurnOff={isOwner} onDone={(c) => done(`Saved ${c.name}.`)} />
        )}
      </Dialog>
    </>
  );
}

/** Add or edit a customer. Exported so the sales screen can add a customer on the spot. */
export function CustomerForm({
  customer,
  canTurnOff,
  onDone,
}: {
  customer?: Customer;
  canTurnOff: boolean;
  onDone: (c: Customer) => void;
}) {
  const [form, setForm] = useState({
    name: customer?.name ?? "",
    phone: customer?.phone ? phone(customer.phone) : "",
    email: customer?.email ?? "",
    address: customer?.address ?? "",
    notes: customer?.notes ?? "",
    is_active: customer?.is_active ?? true,
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
      onDone(customer
        ? await api.patch<Customer>(`/customers/${customer.id}`, canTurnOff ? { ...fields, is_active } : fields)
        : await api.post<Customer>("/customers", fields));
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
      <TextInput label="Name" value={form.name} error={errors.name} onChange={set("name")} autoFocus={!customer} />
      <div className="grid gap-4 sm:grid-cols-2">
        <TextInput label="Phone" type="tel" inputMode="tel" value={form.phone} error={errors.phone}
          onChange={set("phone")} placeholder="0803 123 4567" hint="One phone number per customer." />
        <TextInput label="Email" type="email" value={form.email} error={errors.email} onChange={set("email")} />
      </div>
      <TextInput label="Address" value={form.address} error={errors.address} onChange={set("address")} />
      <TextArea label="Notes" rows={2} value={form.notes} error={errors.notes} onChange={set("notes")} />
      {customer && canTurnOff && (
        <Checkbox label="Customer is active" checked={form.is_active}
          hint="Turned-off customers are hidden from searches. Their history is kept."
          onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
      )}
      <div className="flex justify-end pt-2">
        <Button type="submit" loading={busy}>{customer ? "Save changes" : "Add customer"}</Button>
      </div>
    </form>
  );
}
