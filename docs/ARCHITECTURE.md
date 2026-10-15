# JD TechShop — Architecture & MVP Plan

*Delivering Happiness* · Sokoto, Nigeria · Currency: Nigerian naira (₦)

This document is the single source of truth for how the system is designed and why.
It is updated whenever an important decision changes.

---

## 1. MVP specification (one page)

JD TechShop IMS is a private, web-based back-office and point-of-sale system used by
the shop owner and sales staff. It runs as two parts: a **backend API** (Python/FastAPI)
that owns all data and business rules, and a **web interface** (React) that staff use in
a browser on a laptop, tablet or phone.

| # | Module | MVP scope |
|---|--------|-----------|
| A | Dashboard | Sales, gross profit, transactions, inventory value, stock alerts, debts, supplier balances, best sellers, recent activity — all from database queries, with date filters |
| B | Products & inventory | Catalogue with categories/brands, two tracking modes (serialized units with serial/IMEI, or quantity-based), low-stock thresholds, search/filter/sort/pagination |
| C | Purchases | Draft → posted purchase orders from suppliers, landed costs, partial payments, supplier balance |
| D | POS / sales | Fast search (name, SKU, barcode, serial, IMEI), unit picking, discounts, walk-in or named customer, split/partial/credit payments, printable receipt |
| E | Profit | Cost snapshot per sale line; actual unit cost for serialized items, weighted-average cost for quantity items |
| F | Customers & debts | Profiles, purchase history, receivables, repayments, due dates |
| G | Suppliers | Contacts, purchase history, payments, payables |
| H | Stock ledger | Append-only movement history for every change in stock |
| I | Reports | Sales, profit, stock, valuation, debts, payables, purchases, movements, by-employee, discounts/returns/reversals; CSV export and print layouts |
| J | Users & permissions | Owner and Sales Staff roles, enforced on the server; audit trail |

**Out of scope for MVP** (architecture leaves room for them): general ledger, payroll,
multi-branch, tax compliance, online store, loyalty, AI forecasting, native mobile apps,
enterprise integrations.

### What the reference product taught us

lumibusiness.io renders entirely with JavaScript, so its pages could not be read
directly. Public write-ups (TechCabal, Techpoint, Capterra) describe: product creation and
restocking, FIFO batch tracking, low-stock alerts, **IMEI tracking for electronics**,
discounts, sales history dashboard, customer profiles and purchase history, **customer
credit/debt tracking with repayments**, expense tracking by employee, and a POS with
multiple payment collection methods. Those confirmed features match our MVP.
We deliberately do **not** copy its restaurant, loyalty/wallet, storefront, banking or
AI-insight features (out of scope), and we use our own branding and code.

---

## 2. Architecture and why

```
 Browser (React app)  ──HTTPS/JSON──▶  FastAPI backend  ──SQL──▶  PostgreSQL
   - screens, forms                     - auth & roles               - all records
   - no business data stored            - business rules            - constraints
     in the browser                     - DB transactions           - append-only ledgers
```

| Choice | Reason, in plain English |
|--------|--------------------------|
| **PostgreSQL** (not SQLite) from day one | The shop's money and stock records need row locking (two cashiers selling the last charger at the same time), strict constraints, and reliable backups (`pg_dump`). Postgres is free and runs on a Mac. Using it from day one avoids a risky migration later. |
| **FastAPI + SQLAlchemy 2 + Alembic** | Python is readable; FastAPI validates every request with Pydantic; SQLAlchemy gives us real database transactions; Alembic records every schema change as a versioned migration so the database can be upgraded safely. |
| **React + Vite + TypeScript + Tailwind** | TypeScript catches mistakes (e.g. passing a price where a quantity is expected) before they reach the shop floor. Tailwind keeps the styling consistent. |
| **No large UI component library** | We use small hand-written components plus `lucide-react` icons. Fewer dependencies means fewer upgrades and less that can break. Charts are a small in-house SVG component (no chart library), with colours checked by a palette validator. |
| **Login via secure httpOnly cookie** | The login token is stored in a cookie JavaScript cannot read, which protects it if a malicious script ever runs on the page. |
| **Money as `NUMERIC`, never floating point** | Floats cause rounding errors (₦0.1 + ₦0.2 ≠ ₦0.3). All money is stored as exact decimals: totals to 2 places (kobo); unit costs to 4 places so weighted averages stay accurate. |
| **Modular monolith** | One backend, organised by business area (`products`, `sales`, `purchases`…). Simple to run on one machine; each module can grow independently later (e.g. multi-branch). |

---

## 3. Database entities and relationships

Legend: **PK** primary key, **FK** foreign key, **UQ** unique. Every table also has
`created_at`/`updated_at` timestamps (stored in UTC, displayed in Africa/Lagos time).

### People and access
- **users** — id PK, full_name, username UQ, password_hash, role (`owner`|`staff`),
  is_active, token_version (bumped to log out everywhere), failed_login_count, locked_until, last_login_at.
- **audit_logs** — id, user_id FK→users, action, entity_type, entity_id, details (JSON before/after), ip_address, created_at. *Insert-only.*
- **business_settings** — single row: business name, slogan, address, phone, receipt footer, low-stock default.
- **payment_methods** — id, name UQ (Cash, Bank transfer, POS…), is_active, sort_order.

### Catalogue
- **categories** — id, name UQ (case-insensitive), parent_id FK (optional, one level of subcategories only).
- **brands** — id, name UQ.
- **products** — id, sku UQ, barcode UQ (nullable), name, category_id FK, brand_id FK, model, specifications, description,
  `tracking_type` (`serialized`|`quantity`), condition (new/used/refurbished, for quantity items; serialized units carry their own),
  selling_price, `avg_cost` (weighted average, quantity items), `qty_on_hand` (cached, maintained only inside stock-changing transactions),
  reorder_level, default_supplier_id FK, image_path, is_active.
- **product_units** (serialized items only) — id, product_id FK, serial_number, imei1, imei2, condition, unit_cost (acquisition cost),
  status (`in_stock`|`reserved`|`sold`|`returned_to_supplier`|`damaged`|`missing`), purchase_line_id FK, sale_line_id FK, notes.
  **UQ** on serial_number, imei1, imei2 individually, and a check that one IMEI cannot appear in another unit's other slot: a database trigger copies every IMEI (either slot) into `imei_registry`, whose primary key is the IMEI. Units are never deleted and cannot move to another product (trigger). The commit-time stock check also requires a serialized product's `qty_on_hand` to equal its units that are `in_stock` or `reserved`.

### Buying
- **suppliers** — id, name, phone, email, address, notes, is_active.
- **purchases** — id, reference UQ (auto, e.g. PO-2026-00012), supplier_id FK, supplier_invoice_no, purchase_date,
  status (`draft`|`posted`|`reversed`), subtotal, additional_costs, total, posted_at, posted_by FK, idempotency_key UQ.
  UQ (supplier_id, supplier_invoice_no) to stop the same invoice being entered twice.
- **purchase_lines** — id, purchase_id FK, product_id FK, quantity, unit_cost, line_total, allocated_extra_cost, landed_unit_cost.
- **purchase_line_units** — serial/IMEIs and condition of each phone/laptop on a draft line; posting turns them into `product_units` (which keep `purchase_line_id`).
  Database triggers freeze a purchase and its lines once it is no longer a draft. Additional costs are allocated by line value, rounded to kobo, last line takes the remainder.
- **supplier_payments** — id, supplier_id FK, purchase_id FK (optional), amount > 0, method_id FK, paid_on, reference, notes, recorded_by FK, idempotency_key UQ, reversed flag. Append-only (trigger). A payment may not exceed the supplier's balance, nor the purchase's remaining balance when it is for one purchase.

### Selling
- **customers** — id, name, phone UQ (nullable, stored as 0XXXXXXXXXX or +<digits>), email, address, notes, is_walk_in, is_active. A built-in "Walk-in customer" row (created by migration 0005, at most one, unchangeable) is used for anonymous sales. Customers are never deleted (trigger).
- **sales** — id, receipt_no UQ (e.g. JDT-261014-0042), customer_id FK, sold_by FK→users, sold_at, status (`completed`|`reversed`),
  subtotal, discount_total, total, amount_paid_at_sale, due_date, idempotency_key UQ, reversal_of FK (self), notes.
- **sale_lines** — id, sale_id FK, product_id FK, unit_id FK (serialized), quantity, unit_price, discount, line_total,
  **unit_cost_snapshot**, **cost_total_snapshot** (frozen at sale time — profits never change later).
- **customer_payments** — id, customer_id FK, sale_id FK (optional), amount > 0, method_id FK, received_at, recorded_by FK, idempotency_key UQ, reversed flag.
- **customer_payment_allocations** — id, payment_id FK, sale_id FK, amount > 0, UQ(payment_id, sale_id). Which sales a later repayment paid off (one sale, or oldest-first across the balance). Payments taken at the till use `customer_payments.sale_id` instead. A sale's balance = total − till payments − allocations (reversed payments excluded). Permanent (trigger).
- **receipt_counters** — day PK, last_number: receipt numbers per shop day, allocated inside the sale's transaction (no gaps). Sales store `business_date` (Africa/Lagos) for "today" in reports.
  Sales, sale lines and customer payments are permanent (triggers). `product_units.sale_line_id` records which sale line sold a unit.
- **sale_returns** / **sale_return_lines** — what came back from a sale: value (line total share after discount), frozen cost, restocked or damaged; refund_amount = max(0, net paid − (total − returned)) with its method. Owner only.
- **supplier_returns** / **supplier_return_lines** — goods sent back from a posted purchase: credit at invoice price; stock leaves at landed cost (average unwound). Owner only.
- Reversals: sales, purchases and both payment tables carry reversed_at / reversed_by_id / reversal_reason (sales also reversal_refund). A database trigger allows exactly one reversal and nothing else. Units from a reversed purchase become `voided`.

### Stock and money ledgers (append-only)
- **stock_movements** — id, product_id FK, unit_id FK (nullable), quantity_change (+/−), movement_type
  (`opening`|`purchase`|`sale`|`customer_return`|`supplier_return`|`damage`|`missing`|`adjustment`|`reversal`),
  unit_cost, reference_type + reference_id, reason, user_id FK, created_at.
  Rule: `products.qty_on_hand` must always equal the sum of its movements. A deferred database trigger refuses any commit that breaks this, `python -m app.cli check` re-verifies it, and tests cover it.
- **stock_adjustments** / **stock_adjustment_lines** — id, status (`pending`|`approved`|`rejected`), requested_by, decided_by/at, decision_note, reason; lines: quantity change (quantity items) or unit + new status (serialized), kind damage/missing/adjustment. Anyone requests; only owner-approved adjustments create movements (approval re-checks stock). Decided once (trigger).
- **expenses** — id, category, amount, spent_on, method_id, note, recorded_by, reversal fields. Enables honest *net* profit. Permanent; one reversal allowed (trigger).

### Key relationships
- A product has many units (if serialized) and many stock movements.
- A purchase has many lines; posting it creates units/movements and raises the supplier balance.
- A sale has many lines; each line points to one unit (serialized) or a quantity; completing it creates movements.
- Customer balance = Σ(sale totals on credit) − Σ(payments) − Σ(returns credited). **Computed from transactions, never typed in.**
- Supplier balance = Σ(posted purchase totals) − Σ(supplier payments) − Σ(supplier returns). **Computed, never typed in.**

---

## 4. Project folder structure

```
jd-techshop/
├── README.md                  ← how to install, run, test, back up
├── docs/
│   ├── ARCHITECTURE.md        ← this file
│   ├── CHECKLIST.md           ← progress tracker
│   └── BACKUP_RESTORE.md
├── backend/
│   ├── .env.example
│   ├── requirements.txt
│   ├── alembic.ini
│   ├── alembic/versions/      ← database migrations
│   ├── app/
│   │   ├── main.py            ← FastAPI app, routers
│   │   ├── config.py          ← settings from environment variables
│   │   ├── database.py        ← engine, sessions
│   │   ├── cli.py             ← create-owner, other admin commands
│   │   ├── core/              ← security, permissions, audit, errors
│   │   ├── models/            ← SQLAlchemy tables (one file per area)
│   │   ├── schemas/           ← Pydantic request/response shapes
│   │   ├── services/          ← business rules (sales, stock, costing…)
│   │   └── api/               ← HTTP routes, thin; call services
│   └── tests/
└── frontend/
    ├── package.json
    ├── vite.config.ts         ← proxies /api to the backend in development
    └── src/
        ├── main.tsx, App.tsx  ← routes
        ├── lib/               ← API client, formatting (₦), auth context
        ├── components/        ← layout, sidebar, buttons, tables, dialogs
        └── pages/             ← one folder per module
```

Business rules live in `services/`, never in routes or the browser, so they can be tested directly.

---

## 5. Phased implementation checklist

See `docs/CHECKLIST.md` for live status.

- **Phase 1 — Discovery & architecture** (this document)
- **Phase 2 — Foundation:** structure, settings, Postgres + Alembic, users, roles, login, audit log, business settings, payment methods, health check, tests, frontend shell with login and role-aware sidebar, Mac setup guide.
- **Phase 3 — Core workflows** (in this order, each tested before the next):
  3a categories, brands, products, opening stock, stock ledger ·
  3b serialized units with IMEI/serial uniqueness ·
  3c suppliers, purchases (draft → post), supplier payments ·
  3d customers · 3e POS sale with locking + idempotency, receipts ·
  3f customer payments & debts · 3g returns, adjustments with approval, reversals.
- **Phase 4 — Dashboard & reports:** real queries, date filters (Africa/Lagos days), charts, CSV export, print layouts, expenses for net profit.
- **Phase 5 — QA:** the 15 acceptance tests as automated tests, concurrency tests, permission tests, backup/restore drill.

---

## 6. Business rules that must be correct from day one

1. **Stock only changes through the ledger.** Every change writes a `stock_movements` row in the same database transaction as the business document. No screen can type a new quantity directly.
2. **Posted records are never deleted or edited.** Mistakes are corrected with a reversal (a mirror-image record) that is audited and, for sensitive cases, owner-approved.
3. **All-or-nothing transactions.** A sale (lines + stock + payment + unit status) succeeds completely or not at all. Same for purchases and payments.
4. **No double posting.** Each sale/purchase/payment form carries a one-time key generated by the browser; the database rejects a second submission with the same key and returns the first result instead. The submit button also disables after one click.
5. **No overselling.** Stock rows are locked (`SELECT … FOR UPDATE`) while a sale is processed, and quantities cannot go below zero (database check).
6. **A serialized unit is sold at most once.** Unit status moves `in_stock → sold` under a lock; the database refuses a sale line for a unit that is not in stock.
7. **IMEI and serial numbers are unique** across all units, including across the IMEI 1/IMEI 2 slots.
8. **Profit uses frozen cost.** Each sale line stores the cost at the moment of sale: the unit's own acquisition cost for phones/laptops; the weighted-average cost for accessories. Changing a product's cost later never changes past profit.
9. **Weighted-average cost (accessories).** New average = (old qty × old avg + received qty × landed unit cost) ÷ (old qty + received qty). Recalculated only when stock is received.
10. **Revenue ≠ cash.** A credit sale is revenue on the day of sale but cash only when paid. Reports show both separately.
11. **Gross profit = net sales (after discounts and returns) − cost of goods sold.** Net profit is only shown when expenses are recorded, and is always labelled correctly.
12. **Payments cannot exceed what is owed.** Overpayments are rejected (no hidden credit balances in the MVP).
13. **Permissions are enforced by the server.** Hiding a button is a convenience, not security.
14. **Days are Sokoto days.** "Today" means midnight-to-midnight in Africa/Lagos time, even though timestamps are stored in UTC.
15. **One lock order everywhere.** Whoever changes what a customer or supplier owes locks that customer or supplier row first, then the document (sale / purchase), then products in id order, then units in id order. Payments lock the customer/supplier; returns and reversals lock the same row first (`FOR NO KEY UPDATE`, so new sales and purchases are not held up). Taking locks in one order means two people never deadlock, and a payment and a return never both act on the same old balance.

### Biggest risks to accuracy (and the guard for each)
| Risk | Guard |
|------|-------|
| Double-click creates two sales | Idempotency key + disabled button |
| Two cashiers sell the last unit | Row locks + non-negative check |
| A repayment and a return (or a supplier payment and a purchase reversal) at the same moment | Same lock order for both (rule 15); tested in `test_concurrency.py` |
| Same phone registered twice | Unique IMEI/serial indexes |
| Past profit changes when costs are edited | Cost snapshot on sale lines |
| Cached quantity drifts from history | Only the stock service writes quantities; consistency test compares to ledger |
| Debts typed in by hand drift from reality | Balances computed from transactions |
| Data loss | Daily `pg_dump` backups copied off the Mac; backup → restore → check is an automated test (`docs/PRODUCTION.md`) |

### Defaults I chose (reversible — tell me if any is wrong)
- Discounts: staff may apply up to a configurable limit (default 5% of the line); larger discounts are refused with a message to ask the owner, who can make the sale (no in-till owner override yet).
- Credit sales: staff may record credit sales for registered customers only (not walk-ins); owner can switch this off in Settings.
- Receipt numbers: `JDT-YYMMDD-NNNN`, restarting each day.
- VAT: not applied (out of scope); prices are entered as the shelf price.
- Cost prices: sales staff can look up products, prices and stock, but never see cost prices; only the owner manages the catalogue and opening stock.
- Products left without a SKU get one automatically (`JDT-00001`, `JDT-00002` …).
- IMEIs must be 15 digits with a valid check digit (Luhn), which catches most typing mistakes. If you ever meet a genuine phone whose IMEI fails this check, tell me and I will relax it.
- Sales staff can add and edit customers; only the owner can turn a customer off. Both owner and staff can take repayments; a repayment can be dated earlier but not in the future. Two customers may share a name but not a phone number.
- Returns and reversals are owner-only; anyone can request a stock adjustment, only the owner approves it. A sale with returns cannot be reversed (return the rest instead).
- Purchases and suppliers are owner-only (as in the sidebar). Draft purchases can be discarded; posted ones can only be reversed (Phase 3g).
- A purchase date or payment date cannot be in the future (shop time).
- Opening stock for phones/laptops can be entered in several batches, until the product's first purchase, sale or adjustment; after that, units arrive through purchases.


## 7. How report figures are defined

All periods are shop days (Africa/Lagos), inclusive. The dashboard and every report use the same queries (`backend/app/services/reports.py`).

| Figure | Definition |
|---|---|
| Gross sales | Totals of completed sales in the period (line discounts already taken off). Reversed sales are left out. |
| Returns | Value of items customers brought back in the period (by return date). |
| Net sales | Gross sales − returns. |
| Cost of goods | Cost frozen on the period's sale lines − cost of the period's returned items. |
| Gross profit | Net sales − cost of goods. |
| Stock losses | Damaged / missing stock written off in the period, at its cost. |
| Net profit | Gross profit − expenses − stock losses. Shown as "profit before expenses" when no expense was recorded. |
| Cash received | Customer payments (not reversed) in the period, by method. Refunds shown separately. |
| Credit given | The part of the period's sales not paid at the till. |
| Stock value | Now: each in-stock unit's own cost (phones/laptops) + quantity × average cost (accessories). |

A sale reversed later disappears from the period it was made in (reports always reflect the records as they stand now).
