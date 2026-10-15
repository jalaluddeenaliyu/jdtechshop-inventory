import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Notice, PageHeader, Pager, Spinner } from "../components/ui";
import { api, qs, type MovementType, type Page, type Product, type StockMovement } from "../lib/api";
import { dateTime, MOVEMENT_LABELS, naira, signed } from "../lib/format";
import { useAuth } from "../lib/auth";
import { AdjustmentsTab } from "./Corrections";
import { UnitsTab } from "./Units";

const PAGE_SIZE = 50;

export function InventoryPage() {
  const [params, setParams] = useSearchParams();
  const isOwner = useAuth().user?.role === "owner";
  const tabs = isOwner
    ? ([["movements", "Stock movements"], ["units", "Serial / IMEI units"], ["adjustments", "Adjustments"]] as const)
    : ([["adjustments", "Adjustments"]] as const);
  const asked = params.get("tab");
  const tab = tabs.find(([id]) => id === asked)?.[0] ?? tabs[0][0];
  return (
    <>
      <PageHeader
        title="Inventory"
        description={isOwner
          ? "Every change in stock, with who made it and why, and every phone and laptop by serial number and IMEI."
          : undefined}
      />
      <div role="tablist" aria-label="Inventory sections" className="mb-6 flex gap-1 overflow-x-auto border-b border-line">
        {tabs.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id}
            onClick={() => {
              const p = new URLSearchParams();
              if (id !== "movements") p.set("tab", id);
              const product = params.get("product");
              if (product) p.set("product", product);
              setParams(p);
            }}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-[15px] ${
              tab === id ? "border-pad text-ink font-medium" : "border-transparent text-muted hover:text-ink"
            }`}>
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === "units" ? <UnitsTab /> : tab === "adjustments" ? <AdjustmentsTab /> : <MovementsTab />}
      </div>
    </>
  );
}

/** The stock ledger: every change in stock, newest first. Entries are permanent. */
function MovementsTab() {
  const [params, setParams] = useSearchParams();
  const productId = params.get("product");
  const type = (params.get("type") ?? "") as MovementType | "";
  const page = Math.max(1, Number(params.get("page")) || 1);

  const [product, setProduct] = useState<Product | null>(null);
  const [data, setData] = useState<Page<StockMovement> | null>(null);
  const [error, setError] = useState<string | null>(null);

  function update(next: Record<string, string | null>) {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v) p.set(k, v);
      else p.delete(k);
    }
    if (!("page" in next)) p.delete("page");
    setParams(p);
  }

  useEffect(() => {
    setProduct(null);
    if (productId) api.get<Product>(`/products/${productId}`).then(setProduct).catch((e) => setError(e.message));
  }, [productId]);

  useEffect(() => {
    setData(null);
    setError(null);
    api
      .get<Page<StockMovement>>(`/stock-movements${qs({
        product_id: productId, movement_type: type, page, page_size: PAGE_SIZE,
      })}`)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [productId, type, page]);

  return (
    <>
      <p className="mb-3 text-muted">
        Entries can never be edited or deleted; mistakes are corrected with a new, opposite entry.
      </p>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-muted">
          Movement
          <select value={type} onChange={(e) => update({ type: e.target.value })}
            className="h-9 rounded-md border border-line bg-white px-3 text-[15px] text-ink focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40">
            <option value="">All movements</option>
            {Object.entries(MOVEMENT_LABELS).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </label>
        {productId && (
          <span className="inline-flex items-center gap-1 rounded-md border border-line bg-white pl-3 pr-1 h-9 text-sm">
            {product ? <>Product: <span className="font-medium">{product.name}</span> <span className="num text-muted">({product.sku})</span></> : "One product"}
            <button onClick={() => update({ product: null })} className="rounded p-1 text-muted hover:text-ink"
              aria-label="Show all products">
              <X className="size-4" />
            </button>
          </span>
        )}
        {!productId && (
          <span className="text-sm text-muted">
            For one product's history, use <Link to="/products" className="text-board underline">History</Link> on the Products page.
          </span>
        )}
      </div>

      {product && (
        <div className="mb-4 grid gap-3 sm:grid-cols-3">
          <Figure label="In stock now" value={String(product.qty_on_hand)} />
          <Figure label="Average cost" value={product.qty_on_hand > 0 ? naira(product.avg_cost) : "—"} />
          <Figure label="Stock value at cost"
            value={product.qty_on_hand > 0 && product.avg_cost ? naira(product.qty_on_hand * Number(product.avg_cost)) : "—"} />
        </div>
      )}

      {error && <Notice tone="error">{error}</Notice>}
      {!data && !error && <Spinner />}
      {data && (
        <>
          <div className="relative overflow-x-auto rounded-lg border border-line bg-white">
            <table className="w-full text-left text-[15px]">
              <thead className="border-b border-line text-sm text-muted">
                <tr>
                  <th className="px-4 py-3 font-medium">When</th>
                  {!productId && <th className="px-4 py-3 font-medium">Product</th>}
                  <th className="px-4 py-3 font-medium">Movement</th>
                  <th className="px-4 py-3 font-medium text-right">Change</th>
                  <th className="px-4 py-3 font-medium text-right">Stock after</th>
                  <th className="px-4 py-3 font-medium text-right">Cost each</th>
                  <th className="px-4 py-3 font-medium">Note</th>
                  <th className="px-4 py-3 font-medium">By</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((m) => (
                  <tr key={m.id} className="border-b border-line last:border-0 align-top">
                    <td className="px-4 py-2.5 num whitespace-nowrap">{dateTime(m.created_at)}</td>
                    {!productId && (
                      <td className="px-4 py-2.5 min-w-44">
                        <Link to={`/inventory?product=${m.product_id}`} className="font-medium hover:underline">
                          {m.product_name}
                        </Link>
                        <div className="num text-sm text-muted">{m.product_sku}</div>
                      </td>
                    )}
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <Badge tone={m.quantity_change > 0 ? "ok" : "neutral"}>
                        {MOVEMENT_LABELS[m.movement_type] ?? m.movement_type}
                      </Badge>
                      {m.unit_label && <div className="mt-1 num text-sm text-muted">{m.unit_label}</div>}
                    </td>
                    <td className={`px-4 py-2.5 text-right num font-medium ${m.quantity_change > 0 ? "text-ok" : "text-alert"}`}>
                      {signed(m.quantity_change)}
                    </td>
                    <td className="px-4 py-2.5 text-right num">{m.balance_after}</td>
                    <td className="px-4 py-2.5 text-right num whitespace-nowrap text-muted">{naira(m.unit_cost)}</td>
                    <td className="px-4 py-2.5 text-muted">{m.reason ?? ""}</td>
                    <td className="px-4 py-2.5 whitespace-nowrap">{m.user_name ?? "—"}</td>
                  </tr>
                ))}
                {!data.items.length && (
                  <tr>
                    <td colSpan={8} className="px-4 py-10 text-center text-muted">
                      {productId || type
                        ? "No stock movements match."
                        : "No stock movements yet. Opening stock entered on the Products page will appear here."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <Pager page={page} pageSize={PAGE_SIZE} total={data.total} noun={["movement", "movements"]}
            onPage={(p) => update({ page: String(p) })} />
        </>
      )}
    </>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-white px-4 py-3">
      <p className="text-sm text-muted">{label}</p>
      <p className="num text-xl font-semibold">{value}</p>
    </div>
  );
}
