// Thin wrapper around fetch for talking to the JD TechShop API.
// - Sends the login cookie automatically (same origin).
// - Adds the X-JDT-Client header the server requires on every change (CSRF protection).
// - Turns server errors into readable messages.

export class ApiError extends Error {
  status: number;
  fieldErrors: Record<string, string>;
  constructor(status: number, message: string, fieldErrors: Record<string, string> = {}) {
    super(message);
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  let res: Response;
  const isForm = body instanceof FormData; // file uploads: the browser sets the content type
  try {
    res = await fetch(`/api${path}`, {
      method,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        ...(body !== undefined && !isForm ? { "Content-Type": "application/json" } : {}),
        ...(method !== "GET" ? { "X-JDT-Client": "web" } : {}),
      },
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check that the backend is running and try again.");
  }

  if (res.status === 204) return undefined as T;

  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON response */
  }

  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/login") && onUnauthorized) onUnauthorized();
    const fieldErrors: Record<string, string> = {};
    for (const e of data?.errors ?? []) if (e.field) fieldErrors[e.field] = e.message;
    const message =
      typeof data?.detail === "string" ? data.detail : `Request failed (${res.status}).`;
    throw new ApiError(res.status, message, fieldErrors);
  }
  return data as T;
}

export const api = {
  get: <T,>(path: string) => request<T>("GET", path),
  post: <T,>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T,>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T,>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T,>(path: string) => request<T>("DELETE", path),
  /** POST a file as multipart form data (field name "file"). */
  upload: <T,>(path: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<T>("POST", path, form);
  },
};

/** "?a=1&b=x" from an object, leaving out empty values. */
export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "" && v !== false) sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

// ---- Shared types (mirror backend schemas) ----
export type Role = "owner" | "staff";

export interface User {
  id: number;
  full_name: string;
  username: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface BusinessSettings {
  business_name: string;
  slogan: string | null;
  address: string | null;
  phone: string | null;
  email: string | null;
  receipt_footer: string | null;
  currency_code: string;
  default_reorder_level: number;
  staff_max_discount_percent: string;
  allow_staff_credit_sales: boolean;
  updated_at: string;
}

export interface PaymentMethod {
  id: number;
  name: string;
  is_active: boolean;
  sort_order: number;
}

export interface AuditEntry {
  id: number;
  user_id: number | null;
  user_name: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  details: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string;
}

export type TrackingType = "serialized" | "quantity" | "service";
export type Condition = "new" | "used" | "refurbished";

export interface Category {
  id: number;
  name: string;
  parent_id: number | null;
  parent_name: string | null;
  is_active: boolean;
}

export interface Brand {
  id: number;
  name: string;
  is_active: boolean;
}

export interface Product {
  id: number;
  sku: string;
  barcode: string | null;
  name: string;
  category_id: number | null;
  category_name: string | null;
  brand_id: number | null;
  brand_name: string | null;
  model: string | null;
  specifications: string | null;
  description: string | null;
  tracking_type: TrackingType;
  condition: Condition | null;
  selling_price: string;
  /** null for sales staff: they never see cost prices */
  avg_cost: string | null;
  /** Owner only: what the stock on hand cost (phones: each unit's own cost). */
  stock_value: string | null;
  /** Owner only: cost of one item on hand (or the last known cost when none). */
  cost_each: string | null;
  qty_on_hand: number;
  reorder_level: number | null;
  effective_reorder_level: number;
  is_low_stock: boolean;
  is_active: boolean;
  /** opening stock can still be entered (before purchases, sales or adjustments) */
  opening_stock_open: boolean;
  created_at: string;
  updated_at: string;
}

export type MovementType =
  | "opening" | "purchase" | "sale" | "customer_return" | "supplier_return"
  | "damage" | "missing" | "adjustment" | "reversal";

export interface StockMovement {
  id: number;
  product_id: number;
  product_name: string | null;
  product_sku: string | null;
  unit_id: number | null;
  /** the unit's IMEI 1, or its serial number */
  unit_label: string | null;
  quantity_change: number;
  balance_after: number;
  movement_type: MovementType;
  unit_cost: string | null;
  reference_type: string | null;
  reference_id: number | null;
  reason: string | null;
  user_id: number | null;
  user_name: string | null;
  created_at: string;
}

export type UnitStatus = "in_stock" | "reserved" | "sold" | "returned_to_supplier" | "damaged" | "missing" | "voided";

export interface Unit {
  id: number;
  product_id: number;
  product_name: string;
  product_sku: string;
  serial_number: string | null;
  imei1: string | null;
  imei2: string | null;
  condition: Condition;
  /** null for sales staff */
  unit_cost: string | null;
  status: UnitStatus;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface Supplier {
  id: number;
  name: string;
  phone: string | null;
  email: string | null;
  address: string | null;
  notes: string | null;
  is_active: boolean;
  purchases_total: string;
  paid_total: string;
  /** what the shop still owes this supplier */
  balance: string;
  created_at: string;
}

export type PurchaseStatus = "draft" | "posted" | "reversed";

export interface PurchaseUnit {
  serial_number: string | null;
  imei1: string | null;
  imei2: string | null;
  condition: Condition;
}

export interface PurchaseLine {
  id: number;
  product_id: number;
  product_name: string;
  product_sku: string;
  tracking_type: TrackingType;
  quantity: number;
  unit_cost: string;
  line_total: string;
  allocated_extra_cost: string;
  landed_unit_cost: string;
  units: PurchaseUnit[];
  returned_quantity: number;
  /** posted serial/IMEI lines: units from this line still in stock */
  units_in_stock: { id: number; label: string }[];
}

export interface Purchase {
  id: number;
  reference: string;
  supplier_id: number;
  supplier_name: string;
  supplier_invoice_no: string | null;
  purchase_date: string;
  status: PurchaseStatus;
  subtotal: string;
  additional_costs: string;
  total: string;
  amount_paid: string;
  /** credit for goods sent back to the supplier */
  returned_credit: string;
  balance_due: string;
  notes: string | null;
  line_count: number;
  reversed_at: string | null;
  reversal_reason: string | null;
  returns: { id: number; returned_at: string; reason: string; credit_total: string; recorded_by_name: string | null; items: string[] }[] | null;
  created_by_name: string | null;
  posted_by_name: string | null;
  posted_at: string | null;
  created_at: string;
  lines: PurchaseLine[] | null;
}

export interface SupplierPayment {
  id: number;
  supplier_id: number;
  supplier_name: string;
  purchase_id: number | null;
  purchase_reference: string | null;
  amount: string;
  method_id: number;
  method_name: string;
  paid_on: string;
  reference: string | null;
  notes: string | null;
  recorded_by_name: string | null;
  reversed: boolean;
  reversal_reason: string | null;
  created_at: string;
}

/**
 * A one-time key for a form, so submitting it twice records it once.
 * Uses getRandomValues, which (unlike randomUUID) also works over plain http on the shop network.
 */
export function newKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export interface Customer {
  id: number;
  name: string;
  /** stored as 08031234567 or +447700900123 */
  phone: string | null;
  email: string | null;
  address: string | null;
  notes: string | null;
  is_walk_in: boolean;
  is_active: boolean;
  created_at: string;
  /** what the customer owes now, always calculated from sales and payments */
  owed: string;
  /** the part of it past its due date */
  overdue: string;
}

export interface UnpaidSale {
  sale_id: number;
  receipt_no: string;
  business_date: string;
  total: string;
  returned: string;
  paid: string;
  balance: string;
  due_date: string | null;
  overdue: boolean;
}

export interface CustomerDebt {
  customer_id: number;
  customer_name: string;
  sales_total: string;
  paid_total: string;
  owed: string;
  overdue: string;
  unpaid_sales: UnpaidSale[];
}

export interface CustomerPayment {
  id: number;
  reference_no: string;
  customer_id: number;
  customer_name: string;
  customer_phone: string | null;
  amount: string;
  method_name: string;
  received_on: string;
  received_at: string;
  reference: string | null;
  recorded_by_name: string | null;
  at_till: boolean;
  reversed: boolean;
  reversal_reason: string | null;
  allocations: { sale_id: number; receipt_no: string; amount: string }[];
  /** what the customer owes at the time this is read (not at the time of payment) */
  customer_owes_now: string;
}

export interface PosProduct {
  id: number;
  name: string;
  sku: string;
  barcode: string | null;
  tracking_type: TrackingType;
  condition: Condition | null;
  selling_price: string;
  qty_on_hand: number;
}

export interface PosUnit {
  id: number;
  serial_number: string | null;
  imei1: string | null;
  imei2: string | null;
  condition: Condition;
}

export interface PosResult {
  kind: "product" | "unit";
  product: PosProduct;
  unit: PosUnit | null;
}

export interface SaleLine {
  id: number;
  product_id: number;
  product_name: string;
  product_sku: string;
  tracking_type: TrackingType;
  unit_id: number | null;
  serial_number: string | null;
  imei1: string | null;
  imei2: string | null;
  condition: Condition | null;
  quantity: number;
  unit_price: string;
  discount: string;
  line_total: string;
  /** owner only */
  unit_cost_snapshot: string | null;
  cost_total_snapshot: string | null;
  returned_quantity: number;
  /** Typed in at the till (quick sale) */
  quick_name: string | null;
  /** Quick sale the owner hasn't reviewed yet */
  review_pending: boolean;
}

export interface SalePayment {
  id: number;
  method_name: string;
  amount: string;
  reference: string | null;
  received_at: string;
}

export interface Sale {
  id: number;
  receipt_no: string;
  customer_id: number;
  customer_name: string;
  customer_phone: string | null;
  is_walk_in: boolean;
  sold_by_name: string;
  sold_at: string;
  business_date: string;
  status: "completed" | "reversed";
  subtotal: string;
  discount_total: string;
  total: string;
  amount_paid: string;
  /** value of items brought back */
  returned_total: string;
  balance_due: string;
  due_date: string | null;
  notes: string | null;
  item_count: number;
  reversed_at: string | null;
  reversed_by_name: string | null;
  reversal_reason: string | null;
  reversal_refund: string;
  returns: SaleReturnSummary[] | null;
  /** owner only */
  profit: string | null;
  lines: SaleLine[] | null;
  payments: SalePayment[] | null;
}

export interface SaleReturnSummary {
  id: number;
  returned_at: string;
  reason: string;
  value_total: string;
  refund_amount: string;
  recorded_by_name: string | null;
  /** [sale_line_id, quantity, restocked] */
  items: [number, number, boolean][];
}

export interface Adjustment {
  id: number;
  status: "pending" | "approved" | "rejected";
  reason: string;
  requested_by_name: string;
  requested_at: string;
  decided_by_name: string | null;
  decided_at: string | null;
  decision_note: string | null;
  lines: {
    product_id: number;
    product_name: string;
    product_sku: string;
    unit_id: number | null;
    unit_label: string | null;
    unit_new_status: string | null;
    quantity_change: number;
    kind: "damage" | "missing" | "adjustment";
  }[];
}

export interface PeriodFigures {
  date_from: string;
  date_to: string;
  sales_count: number;
  items_sold: number;
  gross_sales: string;
  discounts: string;
  returns: string;
  net_sales: string;
  average_sale: string;
  credit_given: string;
  /** the rest are owner only (null for sales staff) */
  cost_of_goods: string | null;
  gross_profit: string | null;
  gross_margin: string | null;
  expenses: string | null;
  expenses_recorded: boolean | null;
  stock_losses: string | null;
  net_profit: string | null;
  cash_received: string | null;
  refunds: string | null;
  received_by_method: { method: string; amount: string }[] | null;
}

export interface DashboardData {
  period: PeriodFigures;
  by_day: { day: string; net_sales: string; gross_profit: string | null }[];
  best_sellers: { product_id: number; name: string; quantity: number; net_sales: string }[];
  low_stock: { product_id: number; name: string; sku: string; qty_on_hand: number; reorder_level: number }[];
  low_stock_count: number;
  out_of_stock_count: number;
  stock_value: string | null;
  retail_value: string | null;
  customers_owe: string | null;
  customers_overdue: string | null;
  owed_to_suppliers: string | null;
  pending_adjustments: number | null;
  /** Quick sales whose cost isn't in profit yet */
  quick_sales_to_review: number | null;
  recent_activity: { when: string; who: string | null; action: string; detail: string }[] | null;
}

export interface ReportColumn {
  key: string;
  label: string;
  kind: "text" | "money" | "int" | "date" | "datetime" | "percent";
}

export interface ReportData {
  key: string;
  title: string;
  subtitle: string;
  columns: ReportColumn[];
  rows: Record<string, string | number | null>[];
  totals: Record<string, string | number | null> | null;
  notes: string[];
}

export interface Expense {
  id: number;
  category: string;
  amount: string;
  spent_on: string;
  method_name: string | null;
  note: string | null;
  recorded_by_name: string | null;
  created_at: string;
  reversed: boolean;
  reversal_reason: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/** A quick-sale item (sold before it was in the catalogue), waiting for or after the owner's review. */
export interface QuickLine {
  line_id: number;
  sale_id: number;
  receipt_no: string;
  sold_at: string;
  business_date: string;
  sold_by_name: string;
  customer_name: string;
  quick_name: string;
  quantity: number;
  unit_price: string;
  line_total: string;
  reviewed: boolean;
  reviewed_at: string | null;
  reviewed_by_name: string | null;
  /** null = only its cost was entered */
  linked_product_name: string | null;
  cost_total: string;
}
