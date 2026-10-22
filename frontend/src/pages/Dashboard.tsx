import { AlertTriangle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BarChart } from "../components/BarChart";
import { initialPeriod, PeriodPicker, type Period } from "../components/Period";
import { Badge, Notice, PageHeader, Panel, Spinner } from "../components/ui";
import { api, qs, type DashboardData } from "../lib/api";
import { useAuth } from "../lib/auth";
import { dateOnly, dateTime, naira, todayLong } from "../lib/format";

/** Every number here is a query over what was recorded — nothing is estimated. */
export function DashboardPage() {
  const { user } = useAuth();
  const isOwner = user?.role === "owner";
  const firstName = user?.full_name.split(" ")[0] ?? "";
  const [period, setPeriod] = useState<Period>(() => initialPeriod("today"));
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    api.get<DashboardData>(`/dashboard${qs({ date_from: period.from, date_to: period.to })}`)
      .then(setData).catch((e) => setError(e.message));
  }, [period.from, period.to]);

  const f = data?.period;
  return (
    <>
      <PageHeader title={`Good day, ${firstName}`} description={todayLong()} actions={<PeriodPicker value={period} onChange={setPeriod} />} />
      {error && <Notice tone="error">{error}</Notice>}
      {isOwner && !!data?.quick_sales_to_review && (
        <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-pad/60 bg-pad/15 px-4 py-3 text-sm">
          <AlertTriangle className="size-4 shrink-0" aria-hidden />
          <span>
            <span className="font-medium">{data.quick_sales_to_review} quick {data.quick_sales_to_review === 1 ? "sale is" : "sales are"} waiting for review.</span>{" "}
            Their cost isn't counted yet, so profit shows higher than it really is.
          </span>
          <Link to="/sales?tab=review" className="font-medium text-board underline">Review now</Link>
        </div>
      )}
      {!data && !error && <Spinner />}
      {data && f && (
        <div className="space-y-5">
          <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
            <Tile label="Net sales" value={naira(f.net_sales)}
              note={`${f.sales_count} sale${f.sales_count === 1 ? "" : "s"} · ${f.items_sold} item${f.items_sold === 1 ? "" : "s"}`} />
            {isOwner ? (
              <Tile label="Gross profit" value={naira(f.gross_profit)}
                note={f.gross_margin !== null ? `${f.gross_margin}% margin` : "No sales yet"} />
            ) : (
              <Tile label="Average sale" value={naira(f.average_sale)} />
            )}
            {isOwner ? (
              <Tile label={f.expenses_recorded ? "Net profit" : "Profit before expenses"} value={naira(f.net_profit)}
                note={f.expenses_recorded ? `after ${naira(f.expenses)} expenses` : "No expenses recorded for this period"}
                tone={Number(f.net_profit) < 0 ? "alert" : undefined} />
            ) : (
              <Tile label="Sold on credit" value={naira(f.credit_given)} />
            )}
            {isOwner ? (
              <Tile label="Cash received" value={naira(f.cash_received)}
                note={Number(f.refunds) > 0 ? `${naira(f.refunds)} refunded` : "from customers"} />
            ) : (
              <Tile label="Returns" value={naira(f.returns)} />
            )}
          </div>

          {data.by_day.length > 1 && (
            <Panel title={isOwner ? "Net sales by day" : "Sales by day"}>
              <BarChart label="Net sales by day"
                points={data.by_day.map((d) => ({
                  label: d.day.slice(8), title: dateOnly(d.day), value: Number(d.net_sales),
                  extra: d.gross_profit !== null ? `Gross profit ${naira(d.gross_profit)}` : undefined,
                }))} />
              <p className="mt-2 text-sm text-muted">Day-by-day figures are in <Link to="/reports" className="text-board underline">Reports → Sales</Link>.</p>
            </Panel>
          )}

          <div className="grid gap-5 lg:grid-cols-2">
            <Panel title="Best sellers">
              {data.best_sellers.length ? (
                <ol className="space-y-2">
                  {data.best_sellers.map((b, i) => (
                    <li key={b.product_id} className="flex justify-between gap-3">
                      <span className="min-w-0"><span className="num text-muted">{i + 1}.</span> {b.name}</span>
                      <span className="num text-right whitespace-nowrap"><span className="font-medium">{b.quantity} sold</span>
                        <span className="ml-2 text-muted">{naira(b.net_sales)}</span></span>
                    </li>
                  ))}
                </ol>
              ) : <p className="text-muted">No sales in this period.</p>}
            </Panel>
            <Panel title="Stock alerts">
              <p className="mb-2 text-sm">
                <Badge tone="alert">{data.out_of_stock_count} out of stock</Badge>{" "}
                <Badge tone="pad">{data.low_stock_count} low</Badge>
              </p>
              {data.low_stock.length ? (
                <ul className="space-y-1.5">
                  {data.low_stock.map((s) => (
                    <li key={s.product_id} className="flex justify-between gap-3 text-[15px]">
                      <span className="flex items-center gap-1.5">
                        {s.qty_on_hand === 0 && <AlertTriangle className="size-4 text-alert" aria-label="Out of stock" />}
                        {s.name}
                      </span>
                      <span className="num">{s.qty_on_hand} left <span className="text-muted">(low at {s.reorder_level})</span></span>
                    </li>
                  ))}
                </ul>
              ) : <p className="text-muted">Everything is above its low-stock level.</p>}
              <p className="mt-3 text-sm"><Link to="/products" className="text-board underline">See products</Link></p>
            </Panel>
          </div>

          {isOwner && (
            <div className="grid gap-5 lg:grid-cols-2">
              <Panel title="Right now">
                <dl className="grid grid-cols-2 gap-3">
                  <Figure label="Stock at cost" value={naira(data.stock_value)} note={`${naira(data.retail_value)} at selling price`} />
                  <Figure label="Customers owe you" value={naira(data.customers_owe)}
                    note={Number(data.customers_overdue) > 0 ? `${naira(data.customers_overdue)} overdue` : undefined}
                    tone={Number(data.customers_overdue) > 0 ? "alert" : undefined} link="/customers" />
                  <Figure label="You owe suppliers" value={naira(data.owed_to_suppliers)} link="/suppliers" />
                  <Figure label="Adjustments waiting" value={String(data.pending_adjustments ?? 0)}
                    link="/inventory?tab=adjustments" tone={data.pending_adjustments ? "alert" : undefined} />
                </dl>
              </Panel>
              <Panel title="Money in, by method">
                {f.received_by_method?.length ? (
                  <ul className="space-y-1.5">
                    {f.received_by_method.map((m) => (
                      <li key={m.method} className="flex justify-between"><span>{m.method}</span><span className="num">{naira(m.amount)}</span></li>
                    ))}
                    {Number(f.refunds) > 0 && (
                      <li className="flex justify-between text-muted"><span>Refunded</span><span className="num">−{naira(f.refunds)}</span></li>
                    )}
                  </ul>
                ) : <p className="text-muted">No payments received in this period.</p>}
                <p className="mt-3 text-sm text-muted">Credit sales count as sales on the day, but as cash only when paid
                  ({naira(f.credit_given)} sold on credit in this period).</p>
              </Panel>
            </div>
          )}

          {isOwner && data.recent_activity && (
            <Panel title="Recent activity">
              {data.recent_activity.length ? (
                <ul className="divide-y divide-line -my-2">
                  {data.recent_activity.map((a, i) => (
                    <li key={i} className="flex flex-wrap justify-between gap-2 py-2 text-[15px]">
                      <span><span className="font-medium">{a.action}</span> <span className="text-muted">· {a.detail}</span></span>
                      <span className="text-sm text-muted num">{a.who} · {dateTime(a.when)}</span>
                    </li>
                  ))}
                </ul>
              ) : <p className="text-muted">Nothing recorded yet.</p>}
            </Panel>
          )}
        </div>
      )}
    </>
  );
}

function Tile({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: "alert" }) {
  return (
    <div className="rounded-lg border border-line bg-white px-4 py-3">
      <p className="text-sm text-muted">{label}</p>
      <p className={`num text-2xl font-semibold tracking-tight ${tone === "alert" ? "text-alert" : ""}`}>{value}</p>
      {note && <p className="mt-0.5 text-sm text-muted">{note}</p>}
    </div>
  );
}

function Figure({ label, value, note, tone, link }: { label: string; value: string; note?: string; tone?: "alert"; link?: string }) {
  const body = (
    <>
      <dt className="text-sm text-muted">{label}</dt>
      <dd className={`num text-lg font-semibold ${tone === "alert" ? "text-alert" : ""}`}>{value}</dd>
      {note && <dd className={`text-sm ${tone === "alert" ? "text-alert" : "text-muted"}`}>{note}</dd>}
    </>
  );
  return link ? <Link to={link} className="block rounded-md p-1 -m-1 hover:bg-board/5">{body}</Link> : <div>{body}</div>;
}
