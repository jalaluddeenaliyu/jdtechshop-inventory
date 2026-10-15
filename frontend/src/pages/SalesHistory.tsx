import { Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Checkbox, Notice, Pager, Spinner } from "../components/ui";
import { api, qs, type Page, type Sale } from "../lib/api";
import { useAuth } from "../lib/auth";
import { dateTime, naira, todayISO } from "../lib/format";

const PAGE_SIZE = 25;

export function SalesHistory() {
  const { user } = useAuth();
  const isOwner = user?.role === "owner";
  const [from, setFrom] = useState(todayISO());
  const [to, setTo] = useState(todayISO());
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [unpaid, setUnpaid] = useState(false);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<Sale> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => { setQ(search.trim()); setPage(1); }, 250);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    setError(null);
    api.get<Page<Sale>>(`/sales${qs({ date_from: from, date_to: to, q, unpaid_only: unpaid, page, page_size: PAGE_SIZE })}`)
      .then(setData).catch((e) => setError(e.message));
  }, [from, to, q, unpaid, page]);

  const shown = data?.items ?? [];
  const sum = (k: "total" | "balance_due" | "profit") => shown.reduce((s, x) => s + Number(x[k] ?? 0), 0);
  const inputCls = "h-10 rounded-md border border-line bg-white px-3 text-[15px] focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40";

  return (
    <>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="text-sm text-muted">From<input type="date" className={`${inputCls} block mt-1`} value={from} max={to}
          onChange={(e) => { setFrom(e.target.value); setPage(1); }} /></label>
        <label className="text-sm text-muted">To<input type="date" className={`${inputCls} block mt-1`} value={to} min={from}
          onChange={(e) => { setTo(e.target.value); setPage(1); }} /></label>
        <div className="relative flex-1 min-w-56 max-w-sm">
          <label htmlFor="sale-search" className="sr-only">Search sales</label>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" aria-hidden />
          <input id="sale-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Receipt number or customer" className={`${inputCls} w-full pl-9`} />
        </div>
        <Checkbox label="Only unpaid" checked={unpaid} onChange={(e) => { setUnpaid(e.target.checked); setPage(1); }} />
      </div>

      {error && <Notice tone="error">{error}</Notice>}
      {!data && !error && <Spinner />}
      {data && (
        <>
          <div className="relative overflow-x-auto rounded-lg border border-line bg-white">
            <table className="w-full text-left text-[15px]">
              <thead className="border-b border-line text-sm text-muted">
                <tr>
                  <th className="px-4 py-3 font-medium">Receipt</th>
                  <th className="px-4 py-3 font-medium">Customer</th>
                  <th className="hidden md:table-cell px-4 py-3 font-medium">Sold by</th>
                  <th className="px-4 py-3 font-medium text-right">Total</th>
                  <th className="hidden md:table-cell px-4 py-3 font-medium text-right">Owed</th>
                  {isOwner && <th className="hidden lg:table-cell px-4 py-3 font-medium text-right">Profit</th>}
                </tr>
              </thead>
              <tbody>
                {data.items.map((s) => (
                  <tr key={s.id} className="border-b border-line last:border-0 align-top">
                    <td className="px-4 py-3 whitespace-nowrap">
                      <Link to={`/sales/${s.id}`} className="font-medium text-board hover:underline num">{s.receipt_no}</Link>
                      <div className="text-sm text-muted num">{dateTime(s.sold_at)} · {s.item_count} item{s.item_count === 1 ? "" : "s"}</div>
                    </td>
                    <td className="px-4 py-3 min-w-36">{s.customer_name}</td>
                    <td className="hidden md:table-cell px-4 py-3 text-muted">{s.sold_by_name}</td>
                    <td className="px-4 py-3 text-right num whitespace-nowrap">{naira(s.total)}</td>
                    <td className="hidden md:table-cell px-4 py-3 text-right num whitespace-nowrap">
                      {Number(s.balance_due) > 0 ? <Badge tone="alert">{naira(s.balance_due)}</Badge> : <span className="text-muted">—</span>}
                    </td>
                    {isOwner && <td className="hidden lg:table-cell px-4 py-3 text-right num whitespace-nowrap text-muted">{naira(s.profit)}</td>}
                  </tr>
                ))}
                {!data.items.length && (
                  <tr><td colSpan={6} className="px-4 py-10 text-center text-muted">No sales match.</td></tr>
                )}
              </tbody>
              {data.items.length > 0 && (
                <tfoot className="border-t border-line text-sm">
                  <tr>
                    <td colSpan={3} className="hidden md:table-cell px-4 py-2 text-muted">Shown on this page</td>
                    <td colSpan={1} className="md:hidden px-4 py-2 text-muted">This page</td>
                    <td className="md:hidden" />
                    <td className="px-4 py-2 text-right num font-medium">{naira(sum("total"))}</td>
                    <td className="hidden md:table-cell px-4 py-2 text-right num">{naira(sum("balance_due"))}</td>
                    {isOwner && <td className="hidden lg:table-cell px-4 py-2 text-right num">{naira(sum("profit"))}</td>}
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
          <Pager page={page} pageSize={PAGE_SIZE} total={data.total} noun={["sale", "sales"]} onPage={setPage} />
        </>
      )}
    </>
  );
}
