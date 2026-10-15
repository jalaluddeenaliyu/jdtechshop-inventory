import { Download, Printer } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { initialPeriod, PeriodPicker, type Period } from "../components/Period";
import { Button, Notice, PageHeader, Spinner } from "../components/ui";
import { api, qs, type BusinessSettings, type ReportColumn, type ReportData } from "../lib/api";
import { dateOnly, dateTime, MOVEMENT_LABELS, naira, todayLong } from "../lib/format";

type ReportDef = { key: string; label: string; about: string; dated: boolean };

const REPORTS: { group: string; items: ReportDef[] }[] = [
  { group: "Sales & profit", items: [
    { key: "sales", label: "Sales by day", about: "Sales, discounts, returns, cost and gross profit, day by day.", dated: true },
    { key: "profit", label: "Profit and loss", about: "Net sales down to net profit, with expenses and stock losses.", dated: true },
    { key: "best-sellers", label: "Best sellers", about: "What sold most, with profit per product.", dated: true },
    { key: "slow-sellers", label: "Slow sellers", about: "Stock that sold least — money tied up on the shelf.", dated: true },
    { key: "employees", label: "Sales by employee", about: "Each person's sales, discounts given and profit.", dated: true },
  ] },
  { group: "Stock", items: [
    { key: "stock", label: "Stock and valuation", about: "Every product in stock now, valued at cost and at price.", dated: false },
    { key: "movements", label: "Stock movements", about: "Every change in stock, with who and why.", dated: true },
  ] },
  { group: "Money", items: [
    { key: "cash", label: "Money in and out", about: "Cash-up by payment method: received, refunded, paid out.", dated: true },
    { key: "debts", label: "Customer debts", about: "Who owes the shop, and what is overdue.", dated: false },
    { key: "suppliers", label: "Supplier balances", about: "What the shop owes each supplier.", dated: false },
    { key: "purchases", label: "Purchases", about: "Stock bought, returned and paid for.", dated: true },
    { key: "corrections", label: "Discounts, returns and reversals", about: "Everything that reduced a figure, with reasons.", dated: true },
  ] },
];
const ALL = REPORTS.flatMap((g) => g.items);

function cell(value: string | number | null | undefined, col: ReportColumn): string {
  if (value === null || value === undefined || value === "") return col.kind === "text" ? "" : "—";
  switch (col.kind) {
    case "money": return naira(value);
    case "int": return Number(value).toLocaleString("en-NG");
    case "percent": return `${value}%`;
    case "date": return dateOnly(String(value));
    case "datetime": return dateTime(String(value));
    default: return String(value);
  }
}

const numeric = (col: ReportColumn) => ["money", "int", "percent"].includes(col.kind);
// Columns with long text may wrap; everything else (SKUs, dates, statuses) stays on one line.
const WRAPS = new Set(["product", "customer", "supplier", "employee", "detail", "reason", "category"]);

export function ReportsPage() {
  const [params, setParams] = useSearchParams();
  const current = ALL.find((r) => r.key === params.get("report")) ?? ALL[0];
  const [period, setPeriod] = useState<Period>(() => initialPeriod("month"));
  const [stock, setStock] = useState("all");
  const [movementType, setMovementType] = useState("");
  const [data, setData] = useState<ReportData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shop, setShop] = useState<BusinessSettings | null>(null);

  useEffect(() => { api.get<BusinessSettings>("/settings").then(setShop).catch(() => {}); }, []);

  const query = {
    ...(current.dated ? { date_from: period.from, date_to: period.to } : {}),
    ...(current.key === "stock" ? { stock } : {}),
    ...(current.key === "movements" ? { movement_type: movementType } : {}),
  };
  const queryString = qs(query);

  useEffect(() => {
    setData(null);
    setError(null);
    api.get<ReportData>(`/reports/${current.key}${queryString}`).then(setData).catch((e) => setError(e.message));
  }, [current.key, queryString]);

  const csvHref = `/api/reports/${current.key}${qs({ ...query, format: "csv" })}`;
  const selectCls = "h-10 rounded-md border border-line bg-white px-3 text-[15px] focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40";

  return (
    <>
      <div className="no-print">
        <PageHeader title="Reports" description="Worked out from your records each time you open them. Download any report as a spreadsheet (CSV) or print it." />
      </div>
      <div className="grid gap-6 xl:grid-cols-[15rem_1fr] items-start">
        {/* Narrower screens: pick the report from a list, so the table gets the full width. */}
        <div className="no-print xl:hidden">
          <label htmlFor="report-pick" className="sr-only">Report</label>
          <select id="report-pick" value={current.key} onChange={(e) => setParams({ report: e.target.value })}
            className="h-10 w-full max-w-sm rounded-md border border-line bg-white px-3 text-[15px] focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40">
            {REPORTS.map((g) => (
              <optgroup key={g.group} label={g.group}>
                {g.items.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
              </optgroup>
            ))}
          </select>
        </div>
        <nav aria-label="Reports" className="no-print hidden xl:block space-y-4">
          {REPORTS.map((g) => (
            <div key={g.group}>
              <p className="mb-1 text-sm font-medium text-muted">{g.group}</p>
              <ul className="space-y-0.5">
                {g.items.map((r) => (
                  <li key={r.key}>
                    <button onClick={() => setParams({ report: r.key })} aria-current={r.key === current.key ? "page" : undefined}
                      className={`w-full rounded-md px-3 py-1.5 text-left text-[15px] ${r.key === current.key ? "bg-board text-white" : "hover:bg-board/5"}`}>
                      {r.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <section className="min-w-0">
          <div className="no-print mb-4 flex flex-wrap items-center justify-between gap-3">
            <p className="text-muted">{current.about}</p>
            <div className="flex flex-wrap items-center gap-2">
              {current.dated && <PeriodPicker value={period} onChange={setPeriod} />}
              {current.key === "stock" && (
                <select aria-label="Which products" className={selectCls} value={stock} onChange={(e) => setStock(e.target.value)}>
                  <option value="all">All products</option>
                  <option value="low">Low and out of stock</option>
                  <option value="out">Out of stock only</option>
                </select>
              )}
              {current.key === "movements" && (
                <select aria-label="Movement" className={selectCls} value={movementType} onChange={(e) => setMovementType(e.target.value)}>
                  <option value="">All movements</option>
                  {Object.entries(MOVEMENT_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              )}
              <a href={csvHref} className="inline-flex items-center gap-2 rounded-md border border-line bg-white px-3.5 h-10 text-sm font-medium hover:border-board-300">
                <Download className="size-4" aria-hidden /> CSV
              </a>
              <Button variant="secondary" onClick={() => window.print()} disabled={!data}>
                <Printer className="size-4" aria-hidden /> Print
              </Button>
            </div>
          </div>

          {error && <Notice tone="error">{error}</Notice>}
          {!data && !error && <Spinner />}
          {data && (
            <article>
              <header className="mb-3">
                <p className="hidden print:block text-sm">{shop?.business_name} · printed {todayLong()}</p>
                <h2 className="text-xl font-semibold">{data.title}</h2>
                <p className="text-muted">{data.subtitle}</p>
              </header>
              <div className="relative overflow-x-auto rounded-lg border border-line bg-white print:overflow-visible print:border-0">
                <table className="w-full text-left text-[14px] print:text-[11px]">
                  <thead className="border-b border-line text-sm text-muted print:text-[11px]">
                    <tr>
                      {data.columns.map((c) => (
                        <th key={c.key} className={`px-3 py-2.5 font-medium whitespace-nowrap ${numeric(c) ? "text-right" : ""}`}>{c.label}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((row, i) => {
                      const isHeading = data.key === "profit" && !String(row.line).startsWith("  ");
                      return (
                        <tr key={i} className={`border-b border-line last:border-0 ${isHeading ? "font-semibold" : ""}`}>
                          {data.columns.map((c) => {
                            const v = row[c.key];
                            const negative = numeric(c) && Number(v) < 0;
                            return (
                              <td key={c.key} className={`px-3 py-2 ${numeric(c) ? "text-right num whitespace-nowrap" : WRAPS.has(c.key) ? "min-w-40" : "whitespace-nowrap"} ${negative ? "text-alert" : ""} ${c.key === "line" ? "whitespace-pre" : ""}`}>
                                {cell(v, c)}
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })}
                    {!data.rows.length && (
                      <tr><td colSpan={data.columns.length} className="px-3 py-10 text-center text-muted">Nothing to show for this period.</td></tr>
                    )}
                  </tbody>
                  {data.totals && data.rows.length > 0 && (
                    <tfoot className="border-t-2 border-line font-semibold">
                      <tr>
                        {data.columns.map((c, i) => (
                          <td key={c.key} className={`px-3 py-2 ${numeric(c) ? "text-right num whitespace-nowrap" : ""}`}>
                            {i === 0 ? "Total" : data.totals![c.key] !== undefined ? cell(data.totals![c.key], c) : ""}
                          </td>
                        ))}
                      </tr>
                    </tfoot>
                  )}
                </table>
              </div>
              {data.notes.length > 0 && (
                <ul className="mt-3 space-y-1 text-sm text-muted">
                  {data.notes.map((n, i) => <li key={i}>{n}</li>)}
                </ul>
              )}
            </article>
          )}
        </section>
      </div>
    </>
  );
}
