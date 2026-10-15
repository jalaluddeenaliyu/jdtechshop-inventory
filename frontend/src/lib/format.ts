const TZ = "Africa/Lagos";

const nairaFmt = new Intl.NumberFormat("en-NG", {
  style: "currency",
  currency: "NGN",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** ₦1,250,000.00 — amounts arrive from the API as strings to keep exact decimals. */
export function naira(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? nairaFmt.format(n) : "—";
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-NG", {
    timeZone: TZ,
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function todayLong(): string {
  return new Intl.DateTimeFormat("en-NG", {
    timeZone: TZ,
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date());
}

export function roleLabel(role: string): string {
  return role === "owner" ? "Owner" : "Sales staff";
}

export function trackingLabel(t: string): string {
  return t === "serialized" ? "Serial / IMEI" : "Quantity";
}

export const MOVEMENT_LABELS: Record<string, string> = {
  opening: "Opening stock",
  purchase: "Purchase",
  sale: "Sale",
  customer_return: "Customer return",
  supplier_return: "Return to supplier",
  damage: "Damaged",
  missing: "Missing",
  adjustment: "Adjustment",
  reversal: "Reversal",
};

/** +12 / −4, with a real minus sign. */
export function signed(n: number): string {
  return n > 0 ? `+${n}` : `−${Math.abs(n)}`;
}

export const UNIT_STATUS_LABELS: Record<string, string> = {
  in_stock: "In stock",
  reserved: "Reserved",
  sold: "Sold",
  returned_to_supplier: "Returned to supplier",
  damaged: "Damaged",
  missing: "Missing",
  voided: "Voided (purchase reversed)",
};

/** Today's date in shop time, as YYYY-MM-DD (for date inputs). */
export function todayISO(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" })
    .format(new Date());
}

/** "15 Oct 2026" from a YYYY-MM-DD date (no time zone shift). */
export function dateOnly(isoDate: string | null | undefined): string {
  if (!isoDate) return "—";
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Intl.DateTimeFormat("en-NG", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(y, m - 1, d)));
}

export const PURCHASE_STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  posted: "Posted",
  reversed: "Reversed",
};

/** 08031234567 → "0803 123 4567"; other numbers are shown as stored. */
export function phone(value: string | null | undefined): string {
  if (!value) return "—";
  const m = value.match(/^(0\d{3})(\d{3})(\d{4})$/);
  return m ? `${m[1]} ${m[2]} ${m[3]}` : value;
}

/** Add days to a YYYY-MM-DD date. */
export function addDays(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

export type PeriodPreset = "today" | "yesterday" | "7d" | "month" | "last-month" | "custom";

/** [from, to] for a preset, in shop days. */
export function presetRange(p: PeriodPreset): [string, string] {
  const today = todayISO();
  const [y, m] = today.split("-").map(Number);
  switch (p) {
    case "yesterday": return [addDays(today, -1), addDays(today, -1)];
    case "7d": return [addDays(today, -6), today];
    case "month": return [`${today.slice(0, 7)}-01`, today];
    case "last-month": {
      const first = new Date(Date.UTC(y, m - 2, 1)).toISOString().slice(0, 10);
      const last = new Date(Date.UTC(y, m - 1, 0)).toISOString().slice(0, 10);
      return [first, last];
    }
    default: return [today, today];
  }
}

export const PRESET_LABELS: Record<PeriodPreset, string> = {
  today: "Today", yesterday: "Yesterday", "7d": "Last 7 days", month: "This month",
  "last-month": "Last month", custom: "Custom dates",
};
