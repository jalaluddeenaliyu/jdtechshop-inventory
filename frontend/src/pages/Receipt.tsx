import { ArrowLeft, Printer, RotateCcw, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Button, Dialog, Notice, Spinner } from "../components/ui";
import { Logo } from "../components/Logo";
import { api, type BusinessSettings, type Sale } from "../lib/api";
import { useAuth } from "../lib/auth";
import { dateOnly, dateTime, naira, phone } from "../lib/format";
import { ReturnItemsForm, ReverseForm } from "./Corrections";

/** /sales/:id — the receipt, ready to print (?print=1 opens the print dialog). */
export function ReceiptPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const { user } = useAuth();
  const [sale, setSale] = useState<Sale | null>(null);
  const [shop, setShop] = useState<BusinessSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<"return" | "reverse" | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const isOwner = user?.role === "owner";

  useEffect(() => {
    api.get<Sale>(`/sales/${id}`).then(setSale).catch((e) => setError(e.message));
    api.get<BusinessSettings>("/settings").then(setShop).catch(() => {});
  }, [id]);

  const autoPrint = params.get("print") === "1";
  useEffect(() => {
    if (autoPrint && sale && shop) setTimeout(() => window.print(), 300);
  }, [autoPrint, sale, shop]);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!sale || !shop) return <Spinner />;

  return (
    <>
      <div className="no-print mb-4 flex flex-wrap items-center justify-between gap-3">
        <Link to="/sales?tab=history" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Sales history
        </Link>
        <div className="flex flex-wrap gap-2">
          {isOwner && sale.status === "completed" && (sale.lines ?? []).some((l) => l.quantity > l.returned_quantity) && (
            <Button variant="secondary" onClick={() => { setFlash(null); setAction("return"); }}>
              <RotateCcw className="size-4" aria-hidden /> Return items
            </Button>
          )}
          {isOwner && sale.status === "completed" && !sale.returns?.length && (
            <Button variant="secondary" className="text-alert" onClick={() => { setFlash(null); setAction("reverse"); }}>
              <Undo2 className="size-4" aria-hidden /> Reverse sale
            </Button>
          )}
          <Link to="/sales" className="inline-flex items-center rounded-md border border-line bg-white px-3.5 h-10 text-sm font-medium hover:border-board-300">New sale</Link>
          <Button onClick={() => window.print()}><Printer className="size-4" aria-hidden /> Print receipt</Button>
        </div>
      </div>

      {flash && <div className="no-print mb-4"><Notice tone="success">{flash}</Notice></div>}
      {sale.status === "reversed" && (
        <div className="mb-4"><Notice tone="error">
          This sale was reversed on {dateTime(sale.reversed_at)} by {sale.reversed_by_name}: “{sale.reversal_reason}”.
          {Number(sale.reversal_refund) > 0 && <> {naira(sale.reversal_refund)} was given back.</>}
        </Notice></div>
      )}
      <article className="receipt mx-auto w-full max-w-sm rounded-lg border border-line bg-white p-5 text-[14px] leading-snug print:max-w-none print:border-0 print:p-0">
        <header className="text-center">
          <h1><Logo on="dark" alt={shop.business_name} className="mx-auto h-16 print:h-14" /></h1>
          {shop.slogan && <p className="italic">{shop.slogan}</p>}
          {shop.address && <p>{shop.address}</p>}
          {shop.phone && <p className="num">{shop.phone}</p>}
        </header>

        <dl className="mt-3 border-t border-dashed border-ink/40 pt-2 space-y-0.5">
          <Row label="Receipt" value={sale.receipt_no} />
          <Row label="Date" value={dateTime(sale.sold_at)} />
          <Row label="Served by" value={sale.sold_by_name} />
          {!sale.is_walk_in && <Row label="Customer" value={`${sale.customer_name}${sale.customer_phone ? ` · ${phone(sale.customer_phone)}` : ""}`} />}
        </dl>

        <table className="mt-3 w-full border-t border-dashed border-ink/40">
          <tbody>
            {sale.lines?.map((l) => (
              <tr key={l.id} className="align-top">
                <td className="pt-2 pr-2">
                  <div className="font-medium">{l.product_name}</div>
                  <div className="num">{l.quantity} × {naira(l.unit_price)}</div>
                  {(l.imei1 || l.serial_number) && (
                    <div className="num text-[12px]">{[l.imei1 && `IMEI ${l.imei1}`, l.imei2 && `IMEI ${l.imei2}`, l.serial_number && `S/N ${l.serial_number}`].filter(Boolean).join(" · ")}</div>
                  )}
                  {l.condition && l.condition !== "new" && <div className="text-[12px] capitalize">{l.condition}</div>}
                  {Number(l.discount) > 0 && <div className="num text-[12px]">Discount −{naira(l.discount)}</div>}
                </td>
                <td className="pt-2 text-right num whitespace-nowrap">{naira(l.line_total)}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <dl className="mt-3 border-t border-dashed border-ink/40 pt-2 space-y-0.5">
          {Number(sale.discount_total) > 0 && (
            <>
              <Row label="Subtotal" value={naira(sale.subtotal)} />
              <Row label="Discount" value={`−${naira(sale.discount_total)}`} />
            </>
          )}
          <Row label="TOTAL" value={naira(sale.total)} strong />
          {sale.payments?.map((p) => <Row key={p.id} label={`Paid · ${p.method_name}`} value={naira(p.amount)} />)}
          {Number(sale.reversal_refund) > 0 && <Row label="Refunded (sale reversed)" value={`−${naira(sale.reversal_refund)}`} />}
          {Number(sale.balance_due) > 0 && (
            <>
              <Row label="Balance owed" value={naira(sale.balance_due)} strong />
              {sale.due_date && <Row label="Due by" value={dateOnly(sale.due_date)} />}
            </>
          )}
        </dl>

        {Number(sale.returned_total) > 0 && (
          <dl className="mt-3 border-t border-dashed border-ink/40 pt-2 space-y-0.5">
            <Row label="Items returned" value={`−${naira(sale.returned_total)}`} />
            <Row label="Net paid" value={naira(sale.amount_paid)} />
            <Row label="Balance owed" value={naira(sale.balance_due)} strong />
          </dl>
        )}
        {shop.receipt_footer && <p className="mt-4 border-t border-dashed border-ink/40 pt-2 text-center">{shop.receipt_footer}</p>}
        {user?.role === "owner" && sale.profit !== null && (
          <p className="no-print mt-4 rounded bg-paper px-2 py-1 text-center text-sm text-muted">Profit on this sale (not printed): <span className="num">{naira(sale.profit)}</span></p>
        )}
      </article>

      {!!sale.returns?.length && (
        <section className="no-print mx-auto mt-5 w-full max-w-sm">
          <h2 className="mb-2 font-semibold">Returns</h2>
          <ul className="divide-y divide-line rounded-lg border border-line bg-white">
            {sale.returns.map((r) => (
              <li key={r.id} className="px-3 py-2 text-sm">
                <div className="flex justify-between gap-2">
                  <span className="font-medium">{dateTime(r.returned_at)}</span>
                  <span className="num">{naira(r.value_total)}</span>
                </div>
                <p className="text-muted">
                  {r.items.map(([lineId, qty, restocked]) => {
                    const line = sale.lines?.find((l) => l.id === lineId);
                    return `${line?.product_name ?? "Item"} × ${qty}${restocked ? "" : " (damaged)"}`;
                  }).join(", ")} — “{r.reason}” · {r.recorded_by_name}
                  {Number(r.refund_amount) > 0 && <> · refunded {naira(r.refund_amount)}</>}
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}

      <Dialog wide open={action === "return"} title={`Return items from ${sale.receipt_no}`} onClose={() => setAction(null)}>
        {action === "return" && <ReturnItemsForm sale={sale} onDone={(s) => {
          setSale(s);
          setAction(null);
          const r = s.returns?.at(-1);
          setFlash(`Return recorded${r && Number(r.refund_amount) > 0 ? `: give back ${naira(r.refund_amount)}` : ""}.`);
        }} />}
      </Dialog>
      <Dialog open={action === "reverse"} title={`Reverse sale ${sale.receipt_no}`} onClose={() => setAction(null)}>
        {action === "reverse" && (
          <ReverseForm<Sale> endpoint={`/sales/${sale.id}/reverse`} refund={Number(sale.amount_paid)}
            confirmLabel="Reverse sale"
            explain="Use this only for a sale that should never have been recorded (wrong items, wrong customer, entered twice). Every item goes back into stock and the sale is kept, marked reversed. For goods brought back, use “Return items” instead."
            onDone={(s) => { setSale(s); setAction(null); setFlash("Sale reversed."); }} />
        )}
      </Dialog>
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
