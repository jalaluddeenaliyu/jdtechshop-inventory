# Build checklist

Legend: ✅ done and tested · 🔶 in progress · ⬜ not started

_Last updated: 15 October 2026 — Phase 5 complete (installing on the shop Mac is yours: `docs/PRODUCTION.md` §2–3)._

## Phase 1 — Discovery & architecture ✅
- ✅ Reference product reviewed (site is JavaScript-only; features taken from public reviews)
- ✅ MVP spec, architecture, entities, folder structure, business rules (`docs/ARCHITECTURE.md`)

## Phase 2 — Foundation ✅
- ✅ Project structure (backend / frontend / docs / scripts)
- ✅ Configuration from environment variables; production refuses weak secret
- ✅ PostgreSQL + Alembic migrations (upgrade and downgrade tested)
- ✅ Users with Owner / Sales Staff roles
- ✅ Login with bcrypt hashing, httpOnly session cookie, 12-hour sessions
- ✅ Account lockout after 5 wrong passwords (15 minutes)
- ✅ Instant logout everywhere on password reset, deactivation or role change
- ✅ Permissions enforced on the server (tested by calling the API directly as staff)
- ✅ CSRF protection (custom header required on all changes)
- ✅ Cannot remove the last active owner or demote yourself
- ✅ Append-only audit log (database trigger blocks edits and deletes)
- ✅ Business settings (name, slogan, receipt footer, staff discount limit, credit-sale rule)
- ✅ Configurable payment methods (turned off, never deleted)
- ✅ Health-check endpoint
- ✅ Readable validation and error messages; server errors never expose internals
- ✅ Frontend: login, role-aware sidebar, Users, Settings (business, payments, password, activity log)
- ✅ Responsive layout (desktop and phone), keyboard focus, reduced motion
- ✅ Owner bootstrap command (`python -m app.cli create-owner`)
- ✅ Backup and restore scripts, restore drill verified
- ✅ 40 automated backend tests passing

## Phase 3 — Core workflows ✅
- ✅ 3a Categories, brands, products (serialized vs quantity), opening stock, stock ledger
  - ✅ Backend: categories (one level of subcategories), brands, products, opening stock, stock ledger API
  - ✅ Stock changes only through the ledger; the database refuses a quantity that does not match its movements
  - ✅ Stock ledger is append-only (database trigger blocks edits and deletes)
  - ✅ Weighted-average cost on stock received; opening stock once only, safe against double submit
  - ✅ Staff can look up products but never see cost prices
  - ✅ `python -m app.cli check` verifies every product quantity against its movements
  - ✅ 42 new automated tests (82 total)
  - ✅ Frontend: Products page (search, category/brand/stock/tracking filters, sorting, paging; add/edit product; opening stock; categories & brands manager)
  - ✅ Frontend: Inventory page (stock ledger, filter by product and movement type, per-product stock and value)
  - ✅ Staff see products, prices and stock but no costs and no Inventory page; phone layout keeps price and stock visible
- ✅ 3b Serialized units: serial / IMEI 1 / IMEI 2 with uniqueness across slots
  - ✅ Units table with own condition, cost and status; units are never deleted or moved between products
  - ✅ IMEI registry (database trigger) makes every IMEI unique across all units and both slots; serial numbers unique
  - ✅ IMEIs checked for 15 digits and a valid check digit; serials normalised to capitals
  - ✅ Opening stock unit by unit (all-or-nothing batches), allowed until the product's first purchase/sale/adjustment
  - ✅ Each unit enters with its own ledger movement; database refuses a quantity that differs from units on hand
  - ✅ Owner can correct a unit's serial/IMEIs, condition and notes (audited); cost and status are not editable
  - ✅ Search products and units by any part of a serial or IMEI; staff never see unit costs
  - ✅ Frontend: “Add units” grid (scanner-friendly: Enter moves to the next box), Inventory “Serial / IMEI units” tab with search, filters and edit
  - ✅ 24 new automated tests (106 total), including two owners registering the same IMEI at once
- ✅ 3c Suppliers; purchases draft → posted; weighted-average cost; supplier payments; balances
  - ✅ Suppliers (turned off, never deleted); balance = posted purchases − payments, always calculated
  - ✅ Draft purchases: editable, discardable, checked on save (products, serial/IMEIs, duplicate supplier invoice, no future dates)
  - ✅ Posting is all-or-nothing: quantity items via one ledger movement (weighted average updated), serial/IMEI items become units at their landed cost
  - ✅ Transport & other costs spread over lines by value (exact to the kobo), giving each item's landed cost
  - ✅ Posting twice changes nothing; posted purchases and their lines are frozen by database triggers
  - ✅ Supplier payments: against a purchase or on account, never more than owed (supplier row locked), one-time form key stops double recording, append-only
  - ✅ Opening stock closes once a product has purchases, sales or adjustments; the Products page hides the button then
  - ✅ Frontend: Suppliers (balances, add/edit, pay, history), Purchases list, purchase editor with product search, scanner-friendly units, live landed-cost preview, posted view with payments
  - ✅ 43 new automated tests (149 total), including simultaneous payments that would overpay
- ✅ 3d Customers (with built-in walk-in customer)
  - ✅ Customers with name, phone, email, address, notes; never deleted (database trigger), turned off instead (owner only)
  - ✅ Phone numbers stored one way (+234 803…, 234803…, 0803… are the same number), so one number = one customer
  - ✅ Built-in walk-in customer created by the migration; the database refuses to change it, delete it or add a second one
  - ✅ Staff can add and edit customers (needed at the till); search by any part of the name, email or phone
  - ✅ Frontend: Customers page with search, add/edit form (reusable on the sales screen)
  - ✅ 20 new automated tests (169 total)
- ✅ 3e POS: search by name/SKU/barcode/serial/IMEI, unit picking, discounts with limits, row locking, idempotency keys, receipts
  - ✅ One all-or-nothing transaction per sale: lines, stock movements, unit status, payments
  - ✅ Products then units locked in id order: no double-selling the last item or the same phone, no deadlocks (tested with simultaneous cashiers)
  - ✅ Prices always from the product; per-line discounts with the staff limit from Settings (owner unlimited, never above the price)
  - ✅ Cost frozen per line (unit cost for phones, weighted average for accessories); owner sees profit, staff never see cost
  - ✅ Split payments; never more than the total; credit only for registered customers, staff only when Settings allow; optional due date
  - ✅ One-time key per cart: double submit (even at the same moment) records one sale
  - ✅ Receipt numbers JDT-YYMMDD-NNNN per shop day, no gaps; sales, lines and payments are permanent (database triggers)
  - ✅ Frontend: till with scan-to-add (barcode → product, IMEI → that phone), unit picker, cart, customer search/add, split payment, change calculator, credit warning; sales history; printable receipt
  - ✅ 20 new automated tests (189 total)
- ✅ 3f Customer payments and debts (no overpayment)
  - ✅ What a customer owes is calculated (completed sales − payments), never typed in; overdue = unpaid sales past their due date
  - ✅ Repayments for one sale, or towards the balance (oldest sales first, via `customer_payment_allocations`), so every receipt's balance is right
  - ✅ No overpayment: customer row locked while checking (tested with two cashiers at once); one-time form key stops double recording
  - ✅ Payments and allocations are permanent (database triggers); audited with how the money was allocated
  - ✅ Frontend: "Owes" column and "only customers who owe" filter, customer statement with Receive payment / Pay this, printable payment receipt (PAY-000123), "already owes" warning at the till
  - ✅ Fixed in shared dialogs: forms now open with the right field focused, and closing a dialog inside another no longer closes both
  - ✅ 11 new automated tests (200 total)
- ✅ 3g Customer & supplier returns, stock adjustments with owner approval, reversals
  - ✅ Customer returns (owner): value first reduces what is owed, only over-payment is refunded; restock or book as damaged; profit undone at frozen cost
  - ✅ Sale reversal (owner, with reason): everything back to stock, everything paid refunded, sale kept and marked reversed
  - ✅ Supplier returns (owner): stock leaves at landed cost (average unwound exactly), supplier credited the invoice price; supplier can end up owing the shop
  - ✅ Purchase reversal (owner): only while all its stock is still here and nothing is paid or returned; units voided, average restored
  - ✅ Stock adjustments: anyone requests (damaged / missing / found / count), only owner-approved ones move stock; approval re-checks stock; staff see their own requests
  - ✅ Customer and supplier payment reversals (owner, with reason); blocked where a refund depended on the payment
  - ✅ Database: sales, purchases and payments allow exactly one reversal and nothing else; returns and adjustment lines permanent; adjustments decided once
  - ✅ Frontend: Return items / Reverse sale on receipts, Return to supplier / Reverse on purchases, Reverse on payments, Inventory → Adjustments tab (staff too)
  - ✅ 39 new automated tests (239 total)

## Phase 4 — Dashboard & reports ✅
- ✅ Dashboard figures from real queries with date filters (Today, Yesterday, 7 days, this/last month, custom — Africa/Lagos days)
  - ✅ Net sales, gross profit and margin, net profit (labelled "before expenses" when none recorded), cash received by method, credit given
  - ✅ Sales-by-day chart with hover tooltip, best sellers, stock alerts, stock value, debts, supplier balances, pending adjustments, recent activity
  - ✅ Sales staff see sales, items and stock alerts — never cost or profit
- ✅ Sales, profit and loss, best/slow sellers, stock levels, valuation, low/out of stock
- ✅ Debts, supplier balances, purchases, stock movements, by-employee, discounts/returns/reversals, money in and out (cash-up)
- ✅ Simple expenses for honest net profit (owner; permanent, reversible once with a reason)
- ✅ CSV export (Excel-friendly) and print layouts for every report
- ✅ 11 new automated tests (250 total) — every figure checked against a day worked out by hand

## Phase 5 — Quality assurance ✅
- ✅ All 15 acceptance tests automated (`backend/tests/test_acceptance.py`, one test each — list below)
  - ✅ #14 kills the real server without warning, restarts it, and checks data, login and receipt numbering carry on
  - ✅ #15 runs the real `backup.sh` → `restore.sh` into a new database, compares counts and totals, runs `app.cli check` on the copy and checks the protection triggers came back
  - ✅ #13 recomputes every dashboard and report headline straight from the tables
- ✅ Concurrency tests (`backend/tests/test_concurrency.py`): 8 cashiers / 5 chargers (gapless receipts), opposite-order carts (no deadlock), sale vs write-off approval, sale vs return to supplier, two owners reversing one sale, return vs reversal, two returns of the last item, repayment vs return, supplier payment vs return, supplier payment vs purchase reversal
  - ✅ Found and fixed three real races: repayment + return at the same moment deadlocked; supplier payment + return to that supplier deadlocked; a supplier payment could slip in against a purchase being reversed. Fix: one lock order everywhere (customer/supplier first), architecture rule 15
- ✅ Test suite twice as fast (test database skips waiting for disk on commit): 283 tests in about 1½ minutes
- ✅ Production run guide (`docs/PRODUCTION.md`)
  - ✅ `scripts/run-server.sh`: production mode, HTTPS-only cookies, waits for the database after a power cut, listens on this Mac only
  - ✅ `scripts/install-autostart.sh`: launchd jobs — server starts at login and restarts itself; backup every evening (missed runs happen on wake)
  - ✅ `deploy/Caddyfile`: HTTPS for the shop's devices at https://jdtechshop.local, security headers
  - ✅ `backup.sh`: second copy to a USB drive / cloud folder (`BACKUP_COPY_DIR`), works from scheduled jobs
  - ✅ Rehearsed in production mode on this Mac; launchd files validated
  - ⬜ (you) Install on the shop Mac: move the folder, auto-start, Caddy, trust the certificate on each device
  - ⬜ (you) First real-paper print of a receipt and a report

## After launch — usability fixes (20 October 2026)
- ✅ Add product asks "How many do you have?" and "Cost price, each" — saved with the product in one step (one ledger movement, same rules as opening stock)
- ✅ Adding a phone/laptop goes straight on to entering each unit's IMEI and cost
- ✅ Products list: Cost price with profit (or loss) per item, In stock, Stock value; phones valued from each unit's own cost, matching the reports
- ✅ Profit hint under the selling price while adding or editing a product
- ✅ Restock button: opens a new purchase with the product already on it; add a new supplier without leaving the purchase
- ✅ Duplicate product names refused (ignoring capitals/spaces), pointing to the existing product
- ✅ Fixed: a dialog that opens right after another closes (Add product → Add units) no longer shuts itself
- ✅ Product table fits phones, tablets and laptops (actions move under the name on narrow screens)
- ✅ 12 new automated tests (295 total)
- ✅ Import from Excel/CSV (Products → Import from Excel): downloadable template (Products / Example / How to fill sheets, drop-downs, IMEI columns kept as text)
  - ✅ Accessories one row each (quantity + cost); phones/laptops one row per unit with IMEI or serial and its own cost; missing categories and brands created
  - ✅ Preview first, with every problem listed by row; all-or-nothing save using the same rules as the screens (product checks, IMEI checks, ledger, audit)
  - ✅ Reads common column names (Item, Qty, Cost…), ₦ and commas in prices, CSV with , or ; — owner only
  - ✅ 9 new automated tests (304 total)

## Acceptance tests
| # | Test | Status |
|---|------|--------|
| 1 | Receiving stock (purchase) sets quantity, weighted-average cost, units and supplier balance | ✅ automated |
| 2 | IMEI/serial units registered; a duplicate is refused in either IMEI slot, in purchases too | ✅ automated |
| 3 | Selling a quantity item lowers stock through the ledger; staff never see cost | ✅ automated |
| 4 | A phone sold by IMEI cannot be sold again | ✅ automated |
| 5 | Stock can never go below zero (sale, adjustment, or SQL that skips the app) | ✅ automated |
| 6 | Profit uses the cost frozen at the time of sale | ✅ automated |
| 7 | Staff discounts are limited by Settings; no discount is ever above the price | ✅ automated |
| 8 | Credit sale, partial repayment, overpayment refused | ✅ automated |
| 9 | Customer return restocks and reduces debt before refunding | ✅ automated |
| 10 | Reversing a sale restores stock and money and keeps the audited record | ✅ automated |
| 11 | Submitting the same sale (or payment) twice records it once | ✅ automated |
| 12 | Staff cannot do owner-only actions through the API | ✅ automated (full list in `test_permissions.py`) |
| 13 | Dashboard and report totals match the records | ✅ automated |
| 14 | Data available after restart | ✅ automated (real server killed and restarted) |
| 15 | Backup and restore works in a test environment | ✅ automated (real scripts, fresh database) |

_The original list of 15 was not in the project files; this list was rebuilt from the business rules. Correct any line that differs from what you agreed._
