import { Plus, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Badge, Button, Checkbox, Notice, PageHeader, Pager, Spinner } from "../components/ui";
import { api, qs, type Page, type Purchase, type Supplier } from "../lib/api";
import { dateOnly, naira, PURCHASE_STATUS_LABELS } from "../lib/format";

const PAGE_SIZE = 25;

export function statusBadge(p: Pick<Purchase, "status" | "balance_due">) {
  if (p.status === "draft") return <Badge tone="pad">Draft</Badge>;
  if (p.status === "reversed") return <Badge tone="off">Reversed</Badge>;
  return Number(p.balance_due) > 0 ? <Badge tone="alert">Unpaid</Badge> : <Badge tone="ok">Paid</Badge>;
}

export function PurchasesPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const supplierId = params.get("supplier") ?? "";
  const unpaid = params.get("unpaid") === "1";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const [search, setSearch] = useState(params.get("q") ?? "");
  const q = params.get("q") ?? "";
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [data, setData] = useState<Page<Purchase> | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    api.get<Supplier[]>("/suppliers?include_inactive=true").then(setSuppliers).catch(() => {});
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      if (q !== search.trim()) update({ q: search.trim() || null });
    }, 250);
    return () => clearTimeout(t);
  }, [search]); // only re-run when typing changes, not when the URL updates

  useEffect(() => {
    setError(null);
    api.get<Page<Purchase>>(`/purchases${qs({
      status, supplier_id: supplierId, q, unpaid_only: unpaid, page, page_size: PAGE_SIZE,
    })}`).then(setData).catch((e) => setError(e.message));
  }, [status, supplierId, q, unpaid, page]);

  const selectCls = "h-10 rounded-md border border-line bg-white px-3 text-[15px] text-ink focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40";

  return (
    <>
      <PageHeader
        title="Purchases"
        description="Stock bought from suppliers. A draft changes nothing; posting it adds the stock and what you owe the supplier."
        actions={<Button onClick={() => navigate("/purchases/new")}><Plus className="size-4" aria-hidden /> New purchase</Button>}
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-56 max-w-sm">
          <label htmlFor="purchase-search" className="sr-only">Search purchases</label>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" aria-hidden />
          <input id="purchase-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="PO number or supplier invoice"
            className="block w-full h-10 rounded-md border border-line bg-white pl-9 pr-3 text-[15px] placeholder:text-muted/70
              focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40" />
        </div>
        <label className="sr-only" htmlFor="purchase-supplier">Supplier</label>
        <select id="purchase-supplier" className={selectCls} value={supplierId} onChange={(e) => update({ supplier: e.target.value })}>
          <option value="">All suppliers</option>
          {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <label className="sr-only" htmlFor="purchase-status">Status</label>
        <select id="purchase-status" className={selectCls} value={status} onChange={(e) => update({ status: e.target.value })}>
          <option value="">Drafts and posted</option>
          {Object.entries(PURCHASE_STATUS_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <Checkbox label="Only unpaid" checked={unpaid} onChange={(e) => update({ unpaid: e.target.checked ? "1" : null })} />
      </div>

      {error && <Notice tone="error">{error}</Notice>}
      {!data && !error && <Spinner />}
      {data && (
        <>
          <div className="relative overflow-x-auto rounded-lg border border-line bg-white">
            <table className="w-full text-left text-[15px]">
              <thead className="border-b border-line text-sm text-muted">
                <tr>
                  <th className="px-4 py-3 font-medium">Purchase</th>
                  <th className="px-4 py-3 font-medium">Supplier</th>
                  <th className="hidden md:table-cell px-4 py-3 font-medium">Date</th>
                  <th className="px-4 py-3 font-medium text-right">Total</th>
                  <th className="hidden md:table-cell px-4 py-3 font-medium text-right">Still owed</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((p) => (
                  <tr key={p.id} className="border-b border-line last:border-0 align-top">
                    <td className="px-4 py-3 whitespace-nowrap">
                      <Link to={`/purchases/${p.id}`} className="font-medium text-board hover:underline num">{p.reference}</Link>
                      <div className="text-sm text-muted">
                        {p.supplier_invoice_no ? `Invoice ${p.supplier_invoice_no}` : `${p.line_count} line${p.line_count === 1 ? "" : "s"}`}
                      </div>
                    </td>
                    <td className="px-4 py-3 min-w-36">{p.supplier_name}</td>
                    <td className="hidden md:table-cell px-4 py-3 num whitespace-nowrap">{dateOnly(p.purchase_date)}</td>
                    <td className="px-4 py-3 text-right num whitespace-nowrap">{naira(p.total)}</td>
                    <td className="hidden md:table-cell px-4 py-3 text-right num whitespace-nowrap">
                      {p.status === "posted" ? naira(p.balance_due) : "—"}
                    </td>
                    <td className="px-4 py-3">{statusBadge(p)}</td>
                  </tr>
                ))}
                {!data.items.length && (
                  <tr><td colSpan={6} className="px-4 py-10 text-center text-muted">
                    {q || status || supplierId || unpaid
                      ? "No purchases match."
                      : "No purchases yet. Use “New purchase” when stock arrives from a supplier."}
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
          <Pager page={page} pageSize={PAGE_SIZE} total={data.total} noun={["purchase", "purchases"]}
            onPage={(n) => update({ page: String(n) })} />
        </>
      )}
    </>
  );
}
