import { Pencil, Plus, Search, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { flushSync } from "react-dom";
import { Badge, Button, Dialog, Notice, Pager, SelectInput, Spinner, TextInput } from "../components/ui";
import { api, ApiError, qs, type Condition, type Page, type Product, type Unit } from "../lib/api";
import { dateTime, naira, UNIT_STATUS_LABELS } from "../lib/format";

const cellInput =
  "block w-full h-9 rounded-md border bg-white px-2.5 text-[15px] focus:outline-none focus:border-board " +
  "focus:ring-2 focus:ring-pad/40";

const CONDITIONS: [Condition, string][] = [["new", "New"], ["used", "Used"], ["refurbished", "Refurbished"]];

// ---------------- Add units (opening stock for serial/IMEI products) ----------------

type Row = { key: number; serial_number: string; imei1: string; imei2: string; condition: Condition; unit_cost: string };

let nextKey = 1;
const emptyRow = (condition: Condition, unit_cost: string): Row =>
  ({ key: nextKey++, serial_number: "", imei1: "", imei2: "", condition, unit_cost });
const isBlank = (r: Row) => !r.serial_number.trim() && !r.imei1.trim() && !r.imei2.trim();

/**
 * One row per phone/laptop. Enter moves to the next box instead of submitting, so a barcode
 * scanner (which presses Enter after each scan) can fill IMEIs one after another.
 */
export function AddUnitsForm({ product, onDone }: { product: Product; onDone: (p: Product, count: number) => void }) {
  const [condition, setCondition] = useState<Condition>("new");
  const [cost, setCost] = useState("");
  const [rows, setRows] = useState<Row[]>(() => [emptyRow("new", "")]);
  const [note, setNote] = useState("");
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const grid = useRef<HTMLDivElement>(null);

  function update(key: number, patch: Partial<Row>) {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  function addRow() {
    const row = emptyRow(condition, cost);
    // Render the new row now and move into it before the next keystroke can arrive.
    flushSync(() => setRows((rs) => [...rs, row]));
    grid.current?.querySelector<HTMLInputElement>(`[data-row="${row.key}"] input`)?.focus();
  }

  // Dropdowns are skipped: Enter on a dropdown is unreliable across browsers.
  function onEnter(e: KeyboardEvent<HTMLElement>) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const fields = Array.from(grid.current?.querySelectorAll<HTMLElement>("[data-row] input") ?? []);
    const i = fields.indexOf(e.currentTarget);
    if (i >= 0 && i < fields.length - 1) fields[i + 1].focus();
    else addRow();
  }

  // Changing the shared condition/cost fills them in on rows that still have the old shared value.
  function setShared(next: { condition?: Condition; cost?: string }) {
    setRows((rs) => rs.map((r) => ({
      ...r,
      ...(next.condition !== undefined && r.condition === condition ? { condition: next.condition } : {}),
      ...(next.cost !== undefined && r.unit_cost === cost ? { unit_cost: next.cost } : {}),
    })));
    if (next.condition !== undefined) setCondition(next.condition);
    if (next.cost !== undefined) setCost(next.cost);
  }

  const filled = rows.filter((r) => !isBlank(r));
  const total = filled.reduce((sum, r) => sum + (Number(r.unit_cost.replaceAll(",", "")) || 0), 0);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError(null);
    setRowErrors({});
    if (!filled.length) {
      setError("Enter at least one unit: its serial number or IMEI.");
      return;
    }
    const missingCost = filled.filter((r) => !r.unit_cost.trim());
    if (missingCost.length) {
      setRowErrors(Object.fromEntries(missingCost.map((r) => [r.key, "Enter what this unit cost."])));
      return;
    }
    setBusy(true);
    try {
      const res = await api.post<{ product: Product; units: Unit[] }>(`/products/${product.id}/units/opening`, {
        note,
        units: filled.map((r) => ({
          serial_number: r.serial_number,
          imei1: r.imei1,
          imei2: r.imei2,
          condition: r.condition,
          unit_cost: r.unit_cost.replaceAll(",", "").trim(),
        })),
      });
      onDone(res.product, res.units.length);
    } catch (err) {
      if (err instanceof ApiError) {
        // Field errors arrive as "units.<index>.<field>"; show them on the matching row.
        const byRow: Record<number, string> = {};
        for (const [field, message] of Object.entries(err.fieldErrors)) {
          const m = field.match(/^units\.(\d+)/);
          if (m && filled[Number(m[1])]) byRow[filled[Number(m[1])].key] ??= message;
        }
        // Conflicts arrive as "Row N: …" counted over the rows that were sent.
        const row = err.message.match(/^Row (\d+): /);
        if (row && filled[Number(row[1]) - 1]) {
          byRow[filled[Number(row[1]) - 1].key] = err.message.slice(row[0].length).replace(/^./, (c) => c.toUpperCase());
        }
        setRowErrors(byRow);
        if (!Object.keys(byRow).length) setError(err.message);
      } else setError("Could not save. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <p className="text-muted">
        Enter each <span className="font-medium text-ink">{product.name}</span> you already have, one per row.
        Each needs a serial number or an IMEI. You can scan straight into the boxes.
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectInput label="Condition for new rows" value={condition}
          onChange={(e) => setShared({ condition: e.target.value as Condition })}>
          {CONDITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </SelectInput>
        <TextInput label="Cost each for new rows (₦)" inputMode="decimal" value={cost} placeholder="0.00"
          hint="Change it on a row if one unit cost more or less."
          onChange={(e) => setShared({ cost: e.target.value })} />
      </div>

      <div ref={grid} className="space-y-2">
        <div className="hidden md:grid grid-cols-[1.5rem_1fr_minmax(10.5rem,1fr)_minmax(10.5rem,1fr)_7.5rem_7.5rem_2rem] gap-2 text-sm text-muted">
          <span>#</span><span>Serial number</span><span>IMEI 1</span><span>IMEI 2</span><span>Condition</span><span>Cost (₦)</span><span />
        </div>
        {rows.map((r, i) => (
          <div key={r.key} data-row={r.key}
            className={`rounded-md md:rounded-none border md:border-0 p-2 md:p-0 ${rowErrors[r.key] ? "border-alert" : "border-line"}`}>
            <div className="grid grid-cols-2 md:grid-cols-[1.5rem_1fr_minmax(10.5rem,1fr)_minmax(10.5rem,1fr)_7.5rem_7.5rem_2rem] gap-2 items-center">
              <span className="num text-sm text-muted col-span-2 md:col-span-1">
                <span className="md:hidden">Unit </span>{i + 1}
              </span>
              <input aria-label={`Unit ${i + 1} serial number`} className={`${cellInput} border-line col-span-2 md:col-span-1`}
                value={r.serial_number} spellCheck={false} autoCapitalize="characters" placeholder="Serial"
                onKeyDown={onEnter} onChange={(e) => update(r.key, { serial_number: e.target.value })} />
              <input aria-label={`Unit ${i + 1} IMEI 1`} className={`${cellInput} border-line num`} inputMode="numeric"
                value={r.imei1} placeholder="IMEI 1" onKeyDown={onEnter}
                onChange={(e) => update(r.key, { imei1: e.target.value })} />
              <input aria-label={`Unit ${i + 1} IMEI 2`} className={`${cellInput} border-line num`} inputMode="numeric"
                value={r.imei2} placeholder="IMEI 2" onKeyDown={onEnter}
                onChange={(e) => update(r.key, { imei2: e.target.value })} />
              <select aria-label={`Unit ${i + 1} condition`} className={`${cellInput} border-line`} value={r.condition}
                onChange={(e) => update(r.key, { condition: e.target.value as Condition })}>
                {CONDITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <input aria-label={`Unit ${i + 1} cost`} className={`${cellInput} border-line num`} inputMode="decimal"
                value={r.unit_cost} placeholder="0.00" onKeyDown={onEnter}
                onChange={(e) => update(r.key, { unit_cost: e.target.value })} />
              <button type="button" aria-label={`Remove unit ${i + 1}`} disabled={rows.length === 1}
                onClick={() => setRows((rs) => rs.filter((x) => x.key !== r.key))}
                className="justify-self-end md:justify-self-center rounded p-1.5 text-muted hover:text-alert disabled:opacity-30">
                <Trash2 className="size-4" aria-hidden />
              </button>
            </div>
            {rowErrors[r.key] && <p className="mt-1 text-sm text-alert md:pl-8">{rowErrors[r.key]}</p>}
          </div>
        ))}
        <Button type="button" variant="ghost" className="h-8" onClick={addRow}>
          <Plus className="size-4" aria-hidden /> Add row
        </Button>
      </div>

      <TextInput label="Note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Shelf count on 15 October" />
      <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
        <p className="text-sm text-muted num">
          {filled.length} {filled.length === 1 ? "unit" : "units"} · total cost {naira(total)}
        </p>
        <Button type="submit" loading={busy}>Save {filled.length || ""} {filled.length === 1 ? "unit" : "units"}</Button>
      </div>
    </form>
  );
}

// ---------------- Units list (Inventory tab) ----------------

const PAGE_SIZE = 50;

export function UnitsTab() {
  const [params, setParams] = useSearchParams();
  const productId = params.get("product");
  const status = params.get("status") ?? "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [data, setData] = useState<Page<Unit> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Unit | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  function update(next: Record<string, string | null>) {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v) p.set(k, v);
      else p.delete(k);
    }
    if (!("page" in next)) p.delete("page");
    setParams(p, { replace: true });
  }

  useEffect(() => {
    const t = setTimeout(() => {
      if ((params.get("q") ?? "") !== search.trim()) update({ q: search.trim() || null });
    }, 250);
    return () => clearTimeout(t);
  }, [search]); // only re-run when typing changes, not when the URL updates

  const q = params.get("q") ?? "";
  useEffect(() => {
    setError(null);
    api
      .get<Page<Unit>>(`/units${qs({ q, product_id: productId, status, page, page_size: PAGE_SIZE })}`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [q, productId, status, page, reload]);

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-60 max-w-md">
          <label htmlFor="unit-search" className="sr-only">Search serial number or IMEI</label>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" aria-hidden />
          <input id="unit-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Search serial number or IMEI (any part)" inputMode="search"
            className="block w-full h-9 rounded-md border border-line bg-white pl-9 pr-3 text-[15px] placeholder:text-muted/70
              focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40" />
        </div>
        <label className="flex items-center gap-2 text-sm text-muted">
          Status
          <select value={status} onChange={(e) => update({ status: e.target.value })}
            className="h-9 rounded-md border border-line bg-white px-3 text-[15px] text-ink focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40">
            <option value="">Any status</option>
            {Object.entries(UNIT_STATUS_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        {productId && (
          <span className="inline-flex items-center gap-1 rounded-md border border-line bg-white pl-3 pr-1 h-9 text-sm">
            {data?.items[0] ? <>Product: <span className="font-medium">{data.items[0].product_name}</span></> : "One product"}
            <button onClick={() => update({ product: null })} className="rounded p-1 text-muted hover:text-ink" aria-label="Show all products">
              <X className="size-4" />
            </button>
          </span>
        )}
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
                  {!productId && <th className="px-4 py-3 font-medium">Product</th>}
                  <th className="px-4 py-3 font-medium">Serial number</th>
                  <th className="px-4 py-3 font-medium">IMEI 1</th>
                  <th className="px-4 py-3 font-medium">IMEI 2</th>
                  <th className="px-4 py-3 font-medium">Condition</th>
                  <th className="px-4 py-3 font-medium text-right">Cost</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Added</th>
                  <th className="px-4 py-3"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((u) => (
                  <tr key={u.id} className="border-b border-line last:border-0 align-top">
                    {!productId && (
                      <td className="px-4 py-2.5 min-w-44">
                        <button className="font-medium text-left hover:underline" onClick={() => update({ product: String(u.product_id) })}>
                          {u.product_name}
                        </button>
                        <div className="num text-sm text-muted">{u.product_sku}</div>
                      </td>
                    )}
                    <td className="px-4 py-2.5 num">{u.serial_number ?? "—"}</td>
                    <td className="px-4 py-2.5 num">{u.imei1 ?? "—"}</td>
                    <td className="px-4 py-2.5 num">{u.imei2 ?? "—"}</td>
                    <td className="px-4 py-2.5 capitalize">{u.condition}</td>
                    <td className="px-4 py-2.5 text-right num whitespace-nowrap">{naira(u.unit_cost)}</td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <Badge tone={u.status === "in_stock" ? "ok" : u.status === "sold" ? "neutral" : u.status === "reserved" ? "pad" : "alert"}>
                        {UNIT_STATUS_LABELS[u.status]}
                      </Badge>
                      {u.notes && <div className="mt-1 text-sm text-muted">{u.notes}</div>}
                    </td>
                    <td className="px-4 py-2.5 num whitespace-nowrap text-muted">{dateTime(u.created_at)}</td>
                    <td className="px-4 py-2">
                      <Button variant="ghost" className="h-8" onClick={() => { setFlash(null); setEditing(u); }}>
                        <Pencil className="size-4" aria-hidden /> Edit
                      </Button>
                    </td>
                  </tr>
                ))}
                {!data.items.length && (
                  <tr>
                    <td colSpan={9} className="px-4 py-10 text-center text-muted">
                      {q || status || productId
                        ? "No units match."
                        : "No serial/IMEI units yet. Use “Add units” on a phone or laptop on the Products page."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <Pager page={page} pageSize={PAGE_SIZE} total={data.total} noun={["unit", "units"]}
            onPage={(p) => update({ page: String(p) })} />
        </>
      )}
      <Dialog open={!!editing} title="Edit unit" onClose={() => setEditing(null)}>
        {editing && (
          <EditUnitForm unit={editing} onDone={(u) => {
            setEditing(null);
            setFlash(`Saved unit #${u.id} of ${u.product_name}.`);
            setReload((n) => n + 1);
          }} />
        )}
      </Dialog>
    </>
  );
}

function EditUnitForm({ unit, onDone }: { unit: Unit; onDone: (u: Unit) => void }) {
  const [form, setForm] = useState({
    serial_number: unit.serial_number ?? "",
    imei1: unit.imei1 ?? "",
    imei2: unit.imei2 ?? "",
    condition: unit.condition,
    notes: unit.notes ?? "",
  });
  // Owners (who can see costs) may correct the cost of a unit that is still in stock.
  const canFixCost = unit.unit_cost != null && unit.status === "in_stock";
  const [cost, setCost] = useState(unit.unit_cost != null ? String(Number(unit.unit_cost)) : "");
  const [costReason, setCostReason] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    setErrors({});
    try {
      let saved = await api.patch<Unit>(`/units/${unit.id}`, {
        ...form,
        serial_number: form.serial_number.trim() || null,
        imei1: form.imei1.trim() || null,
        imei2: form.imei2.trim() || null,
      });
      const newCost = Number(cost.replaceAll(",", "").replace("₦", "").trim());
      if (canFixCost && cost.trim() && newCost !== Number(unit.unit_cost)) {
        await api.post(`/products/${unit.product_id}/cost`, {
          cost_each: cost.replaceAll(",", "").replace("₦", "").trim(), unit_id: unit.id, reason: costReason,
        });
        saved = await api.get<Unit>(`/units/${unit.id}`);
      }
      onDone(saved);
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
        Correct a typo in this {unit.product_name}. Every change is recorded in the activity log.
        {canFixCost
          ? " A cost correction applies to this phone from now on; past sales keep the cost they were sold at."
          : " Its status changes only through purchases, sales and approved adjustments."}
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      <TextInput label="Serial number" value={form.serial_number} error={errors.serial_number}
        onChange={set("serial_number")} spellCheck={false} autoCapitalize="characters" />
      <div className="grid gap-4 sm:grid-cols-2">
        <TextInput label="IMEI 1" inputMode="numeric" value={form.imei1} error={errors.imei1} onChange={set("imei1")} />
        <TextInput label="IMEI 2" inputMode="numeric" value={form.imei2} error={errors.imei2} onChange={set("imei2")} />
      </div>
      <SelectInput label="Condition" value={form.condition} onChange={set("condition")}>
        {CONDITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </SelectInput>
      <TextInput label="Notes" value={form.notes} error={errors.notes} onChange={set("notes")} />
      {canFixCost && (
        <div className="grid gap-4 sm:grid-cols-2">
          <TextInput label="Cost price (₦)" inputMode="decimal" value={cost} error={errors.cost_each}
            onChange={(e) => setCost(e.target.value)} hint="What this phone really cost you." />
          <TextInput label="Why the cost changed (optional)" value={costReason} maxLength={200}
            onChange={(e) => setCostReason(e.target.value)} placeholder="e.g. Typed 700000, it was 650000" />
        </div>
      )}
      <div className="flex justify-end pt-2">
        <Button type="submit" loading={busy}>Save changes</Button>
      </div>
    </form>
  );
}
