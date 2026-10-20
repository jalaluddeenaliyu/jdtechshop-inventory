import { History, PackagePlus, Pencil, Plus, ScanLine, Search, Tags, Truck } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import {
  Badge,
  Button,
  Checkbox,
  Dialog,
  Notice,
  PageHeader,
  Pager,
  SelectInput,
  Spinner,
  TextArea,
  TextInput,
} from "../components/ui";
import {
  api,
  ApiError,
  qs,
  type Brand,
  type Category,
  type Condition,
  type Page,
  type Product,
  type TrackingType,
} from "../lib/api";
import { useAuth } from "../lib/auth";
import { naira } from "../lib/format";
import { CatalogManager } from "./CatalogManager";
import { AddUnitsForm } from "./Units";

const PAGE_SIZE = 25;

type Filters = {
  q: string;
  category_id: string;
  brand_id: string;
  stock: "" | "in" | "low" | "out";
  tracking_type: "" | TrackingType;
  include_inactive: boolean;
  sort: "name" | "sku" | "price" | "qty" | "updated";
  descending: boolean;
};

const NO_FILTERS: Filters = {
  q: "", category_id: "", brand_id: "", stock: "", tracking_type: "",
  include_inactive: false, sort: "name", descending: false,
};

type Mode =
  | { kind: "add" }
  | { kind: "edit"; product: Product }
  | { kind: "opening"; product: Product }
  | { kind: "units"; product: Product }
  | { kind: "catalog" }
  | null;

/** Top-level categories, each followed by its subcategories (indented). */
export function categoryOptions(categories: Category[]) {
  const top = categories.filter((c) => c.parent_id === null);
  const orphans = categories.filter(
    (c) => c.parent_id !== null && !top.some((t) => t.id === c.parent_id),
  );
  return [
    ...top.flatMap((t) => [
      { id: t.id, label: t.name },
      ...categories.filter((c) => c.parent_id === t.id).map((c) => ({ id: c.id, label: `— ${c.name}` })),
    ]),
    ...orphans.map((c) => ({ id: c.id, label: `${c.parent_name} › ${c.name}` })),
  ];
}

export function ProductsPage() {
  const { user } = useAuth();
  const isOwner = user?.role === "owner";
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<Product> | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [brands, setBrands] = useState<Brand[]>([]);
  const [mode, setMode] = useState<Mode>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const loadLookups = useCallback(() => {
    api.get<Category[]>("/categories").then(setCategories).catch(() => {});
    api.get<Brand[]>("/brands").then(setBrands).catch(() => {});
  }, []);
  useEffect(loadLookups, [loadLookups]);

  // Wait until typing pauses before searching.
  useEffect(() => {
    const t = setTimeout(() => {
      setFilters((f) => (f.q === search.trim() ? f : { ...f, q: search.trim() }));
      setPage(1);
    }, 250);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback(() => {
    setLoadError(null);
    api
      .get<Page<Product>>(`/products${qs({ ...filters, page, page_size: PAGE_SIZE })}`)
      .then(setData)
      .catch((e) => setLoadError(e.message));
  }, [filters, page]);
  useEffect(load, [load]);

  function setFilter<K extends keyof Filters>(key: K, value: Filters[K]) {
    setFilters((f) => ({ ...f, [key]: value }));
    setPage(1);
  }

  function done(message: string) {
    setMode(null);
    setFlash(message);
    load();
  }

  const filtered = JSON.stringify({ ...filters, q: search.trim() }) !== JSON.stringify(NO_FILTERS);

  return (
    <>
      <PageHeader
        title="Products"
        description={
          isOwner
            ? "Your catalogue of phones, laptops and accessories. Stock only changes through recorded movements, never by typing a number."
            : "Look up products, prices and how many are in stock."
        }
        actions={
          isOwner && (
            <>
              <Button variant="secondary" onClick={() => setMode({ kind: "catalog" })}>
                <Tags className="size-4" aria-hidden /> Categories & brands
              </Button>
              <Button onClick={() => { setFlash(null); setMode({ kind: "add" }); }}>
                <Plus className="size-4" aria-hidden /> Add product
              </Button>
            </>
          )
        }
      />
      {flash && <div className="mb-4"><Notice tone="success">{flash}</Notice></div>}

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 2xl:grid-cols-[2fr_1fr_1fr_1fr_1fr]">
        <div className="relative sm:col-span-2 lg:col-span-4 2xl:col-span-1">
          <label htmlFor="product-search" className="sr-only">Search products</label>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" aria-hidden />
          <input
            id="product-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name, SKU, barcode or model"
            className="block w-full h-10 rounded-md border border-line bg-white pl-9 pr-3 text-[15px] placeholder:text-muted/70
              focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40"
          />
        </div>
        <FilterSelect label="Category" value={filters.category_id} onChange={(v) => setFilter("category_id", v)}>
          <option value="">All categories</option>
          {categoryOptions(categories).map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
        </FilterSelect>
        <FilterSelect label="Brand" value={filters.brand_id} onChange={(v) => setFilter("brand_id", v)}>
          <option value="">All brands</option>
          {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </FilterSelect>
        <FilterSelect label="Stock" value={filters.stock} onChange={(v) => setFilter("stock", v as Filters["stock"])}>
          <option value="">Any stock level</option>
          <option value="in">In stock</option>
          <option value="low">Low or out of stock</option>
          <option value="out">Out of stock</option>
        </FilterSelect>
        <FilterSelect
          label="Sort by"
          value={`${filters.sort}:${filters.descending ? "desc" : "asc"}`}
          onChange={(v) => {
            const [sort, dir] = v.split(":");
            setFilters((f) => ({ ...f, sort: sort as Filters["sort"], descending: dir === "desc" }));
            setPage(1);
          }}
        >
          <option value="name:asc">Name A–Z</option>
          <option value="price:asc">Price: low to high</option>
          <option value="price:desc">Price: high to low</option>
          <option value="qty:asc">Stock: lowest first</option>
          <option value="qty:desc">Stock: highest first</option>
          <option value="updated:desc">Recently changed</option>
          <option value="sku:asc">SKU</option>
        </FilterSelect>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2">
        <FilterSelect label="Tracking" inline value={filters.tracking_type}
          onChange={(v) => setFilter("tracking_type", v as Filters["tracking_type"])}>
          <option value="">Serial/IMEI and quantity items</option>
          <option value="serialized">Serial / IMEI items only</option>
          <option value="quantity">Quantity items only</option>
        </FilterSelect>
        {isOwner && (
          <Checkbox label="Show turned-off products" checked={filters.include_inactive}
            onChange={(e) => setFilter("include_inactive", e.target.checked)} />
        )}
        {filtered && (
          <Button variant="ghost" className="h-8" onClick={() => { setSearch(""); setFilters(NO_FILTERS); setPage(1); }}>
            Clear filters
          </Button>
        )}
      </div>

      {loadError && <Notice tone="error">{loadError}</Notice>}
      {!data && !loadError && <Spinner />}
      {data && (
        <>
          <ProductTable
            products={data.items}
            isOwner={isOwner}
            filtered={filtered}
            onEdit={(p) => { setFlash(null); setMode({ kind: "edit", product: p }); }}
            onOpening={(p) => { setFlash(null); setMode({ kind: p.tracking_type === "serialized" ? "units" : "opening", product: p }); }}
          />
          <Pager page={page} pageSize={PAGE_SIZE} total={data.total} noun={["product", "products"]} onPage={setPage} />
        </>
      )}

      <Dialog wide open={mode?.kind === "add"} title="Add product" onClose={() => setMode(null)}>
        {mode?.kind === "add" && (
          <ProductForm categories={categories} brands={brands}
            onDone={(p) => {
              if (p.tracking_type === "serialized") {
                // Phones/laptops: go straight on to scanning each unit's IMEI and cost.
                setFlash(`Added ${p.name}. Now scan or type each unit's IMEI or serial number and what it cost.`);
                setMode({ kind: "units", product: p });
                load();
              } else {
                done(p.qty_on_hand > 0
                  ? `Added ${p.name} with ${p.qty_on_hand} in stock (SKU ${p.sku}).`
                  : `Added ${p.name} (SKU ${p.sku}). Use “Add stock” on its row when you have some.`);
              }
            }} />
        )}
      </Dialog>
      <Dialog wide open={mode?.kind === "edit"} title="Edit product" onClose={() => setMode(null)}>
        {mode?.kind === "edit" && (
          <ProductForm product={mode.product} categories={categories} brands={brands}
            onDone={(p) => done(`Saved changes to ${p.name}.`)} />
        )}
      </Dialog>
      <Dialog open={mode?.kind === "opening"} title="Add stock you already have" onClose={() => setMode(null)}>
        {mode?.kind === "opening" && (
          <OpeningStockForm product={mode.product}
            onDone={(p) => done(`Added stock: ${p.qty_on_hand} × ${p.name}.`)} />
        )}
      </Dialog>
      <Dialog extraWide open={mode?.kind === "units"} title="Add units" onClose={() => setMode(null)}>
        {mode?.kind === "units" && (
          <AddUnitsForm product={mode.product}
            onDone={(p, count) => done(`Added ${count} ${count === 1 ? "unit" : "units"} of ${p.name}. ${p.qty_on_hand} now in stock.`)} />
        )}
      </Dialog>
      <Dialog wide open={mode?.kind === "catalog"} title="Categories & brands"
        onClose={() => { setMode(null); loadLookups(); load(); }}>
        {mode?.kind === "catalog" && <CatalogManager onChanged={loadLookups} />}
      </Dialog>
    </>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  children,
  inline = false,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  children: React.ReactNode;
  inline?: boolean;
}) {
  return (
    <label className={inline ? "flex items-center gap-2 text-sm text-muted" : "block"}>
      <span className={inline ? "" : "sr-only"}>{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`${inline ? "h-9" : "w-full h-10"} rounded-md border border-line bg-white px-3 text-[15px] text-ink
          focus:outline-none focus:border-board focus:ring-2 focus:ring-pad/40`}
      >
        {children}
      </select>
    </label>
  );
}

function StockCell({ p }: { p: Product }) {
  return (
    <div className="flex flex-col-reverse items-end gap-1 sm:flex-row sm:items-center sm:justify-end sm:gap-2 whitespace-nowrap">
      {p.is_active && p.qty_on_hand === 0 && <Badge tone="alert">Out</Badge>}
      {p.is_active && p.qty_on_hand > 0 && p.is_low_stock && <Badge tone="pad">Low</Badge>}
      <span className="num font-medium">{p.qty_on_hand}</span>
    </div>
  );
}

function hasCost(p: Product) {
  return p.cost_each != null && Number(p.cost_each) > 0;
}

/** Cost price with the profit each sale makes at today's selling price. */
function CostCell({ p }: { p: Product }) {
  if (!hasCost(p)) {
    return <td className="hidden md:table-cell px-2 sm:px-4 py-3 text-right text-sm text-muted whitespace-nowrap">Not yet known</td>;
  }
  const cost = Number(p.cost_each);
  const profit = Number(p.selling_price) - cost;
  return (
    <td className="hidden md:table-cell px-2 sm:px-4 py-3 text-right whitespace-nowrap">
      <div className="num">{naira(cost)}</div>
      <div className={`text-sm num ${profit < 0 ? "text-alert font-medium" : "text-muted"}`}>
        {profit < 0 ? `Loss ${naira(-profit)}` : `Profit ${naira(profit)}`}
      </div>
    </td>
  );
}

function ProductActions({ p, onEdit, onOpening }: {
  p: Product;
  onEdit: (p: Product) => void;
  onOpening: (p: Product) => void;
}) {
  return (
    <>
        {p.tracking_type === "quantity" && p.opening_stock_open && (
          <Button variant="ghost" className="h-8 whitespace-nowrap" onClick={() => onOpening(p)}>
            <PackagePlus className="size-4" aria-hidden /> Add stock
          </Button>
        )}
        {p.tracking_type === "serialized" && p.opening_stock_open && (
          <Button variant="ghost" className="h-8 whitespace-nowrap" onClick={() => onOpening(p)}>
            <ScanLine className="size-4" aria-hidden /> Add units
          </Button>
        )}
        {!p.opening_stock_open && p.is_active && (
          <Link to={`/purchases/new?product=${p.id}`} title="Record stock bought from a supplier"
            className="inline-flex items-center gap-2 rounded-md px-3 h-8 text-sm font-medium text-board hover:bg-board/5 whitespace-nowrap">
            <Truck className="size-4" aria-hidden /> Restock
          </Link>
        )}
        <Link to={p.tracking_type === "serialized" ? `/inventory?tab=units&product=${p.id}` : `/inventory?product=${p.id}`}
          title="Stock history"
          className="inline-flex items-center gap-2 rounded-md px-3 h-8 text-sm font-medium text-board hover:bg-board/5">
          <History className="size-4" aria-hidden /><span className="sr-only">History</span>
        </Link>
        <Button variant="ghost" className="h-8" title="Edit product" onClick={() => onEdit(p)}>
          <Pencil className="size-4" aria-hidden /><span className="sr-only">Edit</span>
        </Button>
    </>
  );
}

function ProductTable({
  products,
  isOwner,
  filtered,
  onEdit,
  onOpening,
}: {
  products: Product[];
  isOwner: boolean;
  filtered: boolean;
  onEdit: (p: Product) => void;
  onOpening: (p: Product) => void;
}) {
  if (!products.length) {
    return (
      <div className="rounded-lg border border-line bg-white px-5 py-10 text-center text-muted">
        {filtered
          ? "No products match these filters."
          : isOwner
            ? "No products yet. Use “Add product” to create your first one."
            : "No products have been added yet."}
      </div>
    );
  }
  return (
    <div className="relative overflow-x-auto rounded-lg border border-line bg-white">
      <table className="w-full text-left text-[15px]">
        <thead className="border-b border-line text-sm text-muted">
          <tr>
            <th className="px-2 sm:px-4 py-3 font-medium">Product</th>
            <th className="px-2 sm:px-4 py-3 font-medium text-right">Selling price</th>
            {isOwner && <th className="hidden md:table-cell px-2 sm:px-4 py-3 font-medium text-right">Cost price</th>}
            <th className="px-2 sm:px-4 py-3 font-medium text-right">In stock</th>
            {isOwner && <th className="hidden xl:table-cell px-2 sm:px-4 py-3 font-medium text-right">Stock value</th>}
            {isOwner && <th className="hidden min-[1340px]:table-cell px-2 sm:px-4 py-3"><span className="sr-only">Actions</span></th>}
          </tr>
        </thead>
        <tbody>
          {products.map((p) => (
            <tr key={p.id} className={`border-b border-line last:border-0 align-top ${p.is_active ? "" : "text-muted"}`}>
              <td className="px-2 sm:px-4 py-3 xl:min-w-56">
                <div className="font-medium">
                  {p.name}
                  {!p.is_active && <span className="ml-2"><Badge tone="off">Turned off</Badge></span>}
                </div>
                <div className="text-sm text-muted">
                  <span className="num">{p.sku}</span>
                  {p.brand_name && <> · {p.brand_name}</>}
                  {p.model && <> · {p.model}</>}
                  {p.condition && p.condition !== "new" && <> · {p.condition}</>}
                  {p.category_name && <span> · {p.category_name}</span>}
                  {p.tracking_type === "serialized" && <span> · Serial/IMEI</span>}
                </div>
                {isOwner && (
                  <div className="min-[1340px]:hidden mt-1.5 -ml-3 flex flex-wrap gap-0.5">
                    <ProductActions p={p} onEdit={onEdit} onOpening={onOpening} />
                  </div>
                )}
              </td>
              <td className="px-2 sm:px-4 py-3 text-right num whitespace-nowrap">
                {naira(p.selling_price)}
                {isOwner && hasCost(p) && (
                  <div className="md:hidden text-sm text-muted">Cost {naira(p.cost_each)}</div>
                )}
              </td>
              {isOwner && <CostCell p={p} />}
              <td className="px-2 sm:px-4 py-3"><StockCell p={p} /></td>
              {isOwner && (
                <td className="hidden xl:table-cell px-2 sm:px-4 py-3 text-right num whitespace-nowrap">
                  {p.qty_on_hand > 0 && hasCost(p) ? naira(p.stock_value) : "—"}
                </td>
              )}
              {isOwner && (
                <td className="hidden min-[1340px]:table-cell px-2 sm:px-4 py-2">
                  <div className="flex justify-end gap-1">
                    <ProductActions p={p} onEdit={onEdit} onOpening={onOpening} />
                  </div>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------- Add / edit product ----------------

type ProductFormState = {
  opening_quantity: string;
  opening_unit_cost: string;
  name: string;
  sku: string;
  barcode: string;
  category_id: string;
  brand_id: string;
  model: string;
  tracking_type: TrackingType;
  condition: Condition;
  selling_price: string;
  reorder_level: string;
  specifications: string;
  description: string;
  is_active: boolean;
};

function initialForm(p?: Product): ProductFormState {
  return {
    opening_quantity: "",
    opening_unit_cost: "",
    name: p?.name ?? "",
    sku: p?.sku ?? "",
    barcode: p?.barcode ?? "",
    category_id: p?.category_id ? String(p.category_id) : "",
    brand_id: p?.brand_id ? String(p.brand_id) : "",
    model: p?.model ?? "",
    tracking_type: p?.tracking_type ?? "quantity",
    condition: p?.condition ?? "new",
    selling_price: p ? String(Number(p.selling_price)) : "",
    reorder_level: p?.reorder_level != null ? String(p.reorder_level) : "",
    specifications: p?.specifications ?? "",
    description: p?.description ?? "",
    is_active: p?.is_active ?? true,
  };
}

function ProductForm({
  product,
  categories,
  brands,
  onDone,
}: {
  product?: Product;
  categories: Category[];
  brands: Brand[];
  onDone: (p: Product) => void;
}) {
  const [form, setForm] = useState<ProductFormState>(() => initialForm(product));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const editing = !!product;
  const hasStock = (product?.qty_on_hand ?? 0) > 0;

  const set = (k: keyof ProductFormState) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
      setForm({ ...form, [k]: e.target.value });

  // A product's current (possibly turned-off) category/brand stays selectable while editing.
  const categoryChoices = categoryOptions(categories);
  if (product?.category_id && !categoryChoices.some((c) => c.id === product.category_id)) {
    categoryChoices.push({ id: product.category_id, label: `${product.category_name} (turned off)` });
  }
  const brandChoices = brands.map((b) => ({ id: b.id, label: b.name }));
  if (product?.brand_id && !brandChoices.some((b) => b.id === product.brand_id)) {
    brandChoices.push({ id: product.brand_id, label: `${product.brand_name} (turned off)` });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const deactivating = editing && product.is_active && !form.is_active;
    if (deactivating && hasStock && !window.confirm(
      `${product.name} still has ${product.qty_on_hand} in stock. Turn it off anyway? It will be hidden from the product list and from sales.`,
    )) return;
    setBusy(true);
    setError(null);
    setErrors({});
    const body: Record<string, unknown> = {
      name: form.name,
      sku: form.sku,
      barcode: form.barcode,
      category_id: form.category_id ? Number(form.category_id) : null,
      brand_id: form.brand_id ? Number(form.brand_id) : null,
      model: form.model,
      tracking_type: form.tracking_type,
      condition: form.tracking_type === "quantity" ? form.condition : null,
      selling_price: form.selling_price.replaceAll(",", "").trim(),
      reorder_level: form.reorder_level.trim() === "" ? null : Number(form.reorder_level),
      specifications: form.specifications,
      description: form.description,
    };
    if (editing) body.is_active = form.is_active;
    else {
      if (!form.sku.trim()) delete body.sku; // let the server generate one
      if (form.tracking_type === "quantity" && (form.opening_quantity.trim() || form.opening_unit_cost.trim())) {
        body.opening_quantity = form.opening_quantity.trim() === "" ? null : Number(form.opening_quantity);
        body.opening_unit_cost = form.opening_unit_cost.replaceAll(",", "").trim() || null;
      }
    }
    try {
      const saved = editing
        ? await api.patch<Product>(`/products/${product.id}`, body)
        : await api.post<Product>("/products", body);
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
      {error && <Notice tone="error">{error}</Notice>}
      <TextInput label="Product name" value={form.name} error={errors.name} onChange={set("name")}
        placeholder="e.g. Samsung Galaxy A54 128GB" autoFocus={!editing} />

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">How is it counted?</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {([
            ["serialized", "Serial / IMEI", "Phones, laptops, tablets: each unit is recorded with its own serial or IMEI number."],
            ["quantity", "Quantity", "Chargers, cases, cables: counted, not individually identified."],
          ] as const).map(([value, title, hint]) => (
            <label key={value}
              className={`flex gap-3 rounded-md border p-3 ${form.tracking_type === value ? "border-board bg-board/5" : "border-line"}
                ${editing && product.qty_on_hand > 0 ? "opacity-70" : "cursor-pointer"}`}>
              <input type="radio" name="tracking_type" value={value} className="mt-1 accent-board"
                checked={form.tracking_type === value} disabled={editing && product.qty_on_hand > 0}
                onChange={() => setForm({ ...form, tracking_type: value })} />
              <span>
                <span className="block text-sm font-medium">{title}</span>
                <span className="block text-sm text-muted">{hint}</span>
              </span>
            </label>
          ))}
        </div>
        {editing && product.qty_on_hand > 0 && (
          <p className="text-sm text-muted">This product has stock, so the way it is counted can no longer change.</p>
        )}
      </fieldset>

      {!editing && form.tracking_type === "quantity" && (
        <div className="rounded-md border border-line bg-paper/60 p-4">
          <p className="text-sm font-medium">Stock you have now</p>
          <p className="text-sm text-muted">Leave blank if you have none yet. Later stock comes in through Restock (a purchase).</p>
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <TextInput label="How many do you have?" type="number" inputMode="numeric" min={1} step={1}
              value={form.opening_quantity} error={errors.opening_quantity} onChange={set("opening_quantity")}
              placeholder="e.g. 20" />
            <TextInput label="Cost price, each (₦)" inputMode="decimal" value={form.opening_unit_cost}
              error={errors.opening_unit_cost} onChange={set("opening_unit_cost")} placeholder="What you paid for one" />
          </div>
        </div>
      )}
      {!editing && form.tracking_type === "serialized" && (
        <div className="rounded-md border border-line bg-paper/60 p-4 text-sm">
          <p className="font-medium">Next: add the units you have</p>
          <p className="text-muted">After you click “Add product”, you’ll type or scan each phone’s IMEI (or serial number) and what that unit cost you.</p>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <TextInput label="Selling price (₦)" inputMode="decimal" value={form.selling_price}
            error={errors.selling_price} onChange={set("selling_price")} placeholder="0.00" />
          <ProfitHint price={form.selling_price}
            cost={!editing ? form.opening_unit_cost : product?.cost_each ?? ""} />
        </div>
        {form.tracking_type === "quantity" ? (
          <SelectInput label="Condition" value={form.condition} onChange={set("condition")}>
            <option value="new">New</option>
            <option value="used">Used</option>
            <option value="refurbished">Refurbished</option>
          </SelectInput>
        ) : (
          <div className="text-sm text-muted sm:pt-7">Condition is recorded on each unit when it is added.</div>
        )}
        <SelectInput label="Category" value={form.category_id} error={errors.category_id} onChange={set("category_id")}>
          <option value="">No category</option>
          {categoryChoices.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
        </SelectInput>
        <SelectInput label="Brand" value={form.brand_id} error={errors.brand_id} onChange={set("brand_id")}>
          <option value="">No brand</option>
          {brandChoices.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
        </SelectInput>
        <TextInput label="SKU" value={form.sku} error={errors.sku} onChange={set("sku")}
          autoCapitalize="characters" spellCheck={false}
          hint={editing ? "Your own product code." : "Leave blank and one is made for you (JDT-00001…)."} />
        <TextInput label="Barcode" value={form.barcode} error={errors.barcode} onChange={set("barcode")}
          spellCheck={false} hint="Optional. Scan it into this box." />
        <TextInput label="Model" value={form.model} error={errors.model} onChange={set("model")}
          placeholder="e.g. SM-A546E" />
        <TextInput label="Low-stock level" type="number" inputMode="numeric" min={0} value={form.reorder_level}
          error={errors.reorder_level} onChange={set("reorder_level")}
          hint="Blank uses the shop default from Settings." />
      </div>
      <TextArea label="Specifications" value={form.specifications} error={errors.specifications}
        onChange={set("specifications")} placeholder="e.g. 8GB RAM, 128GB storage, 6.4″ screen" />
      <TextArea label="Description" value={form.description} error={errors.description}
        onChange={set("description")} rows={2} />
      {editing && (
        <Checkbox label="Product is on sale" checked={form.is_active}
          hint="Turned-off products are hidden from the list and from sales. Their history is kept."
          onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
      )}
      <div className="flex justify-end pt-2">
        <Button type="submit" loading={busy}>{editing ? "Save changes" : "Add product"}</Button>
      </div>
    </form>
  );
}

/** "Profit ₦X each (Y%)" under the selling price, once a cost is known. */
function ProfitHint({ price, cost }: { price: string; cost: string }) {
  const p = Number(price.replaceAll(",", "").trim());
  const c = Number(String(cost).replaceAll(",", "").trim());
  if (!price.trim() || !String(cost).trim() || !Number.isFinite(p) || !Number.isFinite(c) || c <= 0) return null;
  const profit = p - c;
  const margin = p > 0 ? Math.round((profit / p) * 100) : 0;
  return (
    <p className={`mt-1.5 text-sm ${profit < 0 ? "text-alert font-medium" : "text-muted"}`}>
      {profit < 0
        ? `This sells at a loss of ${naira(-profit)} each.`
        : `Profit ${naira(profit)} each (${margin}% of the price).`}
    </p>
  );
}

// ---------------- Opening stock ----------------

function OpeningStockForm({ product, onDone }: { product: Product; onDone: (p: Product) => void }) {
  const [quantity, setQuantity] = useState("");
  const [unitCost, setUnitCost] = useState("");
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const qty = Number(quantity);
  const cost = Number(unitCost.replaceAll(",", ""));
  const value = Number.isInteger(qty) && qty > 0 && Number.isFinite(cost) && unitCost.trim() ? qty * cost : null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError(null);
    const problems: Record<string, string> = {};
    if (!/^\d+$/.test(quantity.trim()) || Number(quantity) < 1) {
      problems.quantity = "Enter how many you have: a whole number, 1 or more.";
    }
    if (!unitCost.trim() || !Number.isFinite(cost) || cost < 0) {
      problems.unit_cost = "Enter what each one cost you, e.g. 4500.";
    }
    setErrors(problems);
    if (Object.keys(problems).length) return;
    setBusy(true);
    try {
      onDone(await api.post<Product>(`/products/${product.id}/opening-stock`, {
        quantity: quantity.trim() === "" ? null : Number(quantity),
        unit_cost: unitCost.replaceAll(",", "").trim() || null,
        note,
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
        How many <span className="font-medium text-ink">{product.name}</span> do you have on the shelf right now,
        and what did each one cost you? This is entered <span className="font-medium text-ink">once</span>;
        later changes come from purchases, sales and approved adjustments.
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="grid gap-4 sm:grid-cols-2">
        <TextInput label="Quantity" type="number" inputMode="numeric" min={1} step={1} value={quantity}
          error={errors.quantity} onChange={(e) => setQuantity(e.target.value)} autoFocus />
        <TextInput label="Cost per item (₦)" inputMode="decimal" value={unitCost}
          error={errors.unit_cost} onChange={(e) => setUnitCost(e.target.value)} placeholder="0.00" />
      </div>
      <TextInput label="Note" value={note} error={errors.note} onChange={(e) => setNote(e.target.value)}
        placeholder="e.g. Shelf count on 15 October" />
      <p className="text-sm text-muted">
        Stock value: <span className="num font-medium text-ink">{value === null ? "—" : naira(value)}</span>
      </p>
      <div className="flex justify-end pt-2">
        <Button type="submit" loading={busy}>Record opening stock</Button>
      </div>
    </form>
  );
}
