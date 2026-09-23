# Finance Ledger Plan — Sales Order → Invoice → Payment → Ledger

Status: **planned, not started**
Go-live date: **25 Sep 2026**

---

## 1. Goal

Track money owed and collected for every sale, with a clear ledger per customer, sales employee and sales order.

Standard order-to-cash flow:

```
Sales Order (Draft)  ──confirm──►  Sales Order (Confirmed, locked)
                                        │
                                        ▼
                                   AR Invoice (auto-created, fixed bill)
                                        │
                                        ▼
                                   Payment(s)  (one or many, partial or full)
                                        │
                                        ▼
                                   Ledger / Reports
```

---

## 2. Decisions agreed

| # | Decision |
|---|----------|
| 1 | Sales orders get a new **status**: `Draft` or `Confirmed`. `delivery_status` (Pending/Delivered) stays as-is and is separate. |
| 2 | The order form has two buttons, **Save as Draft** and **Confirm Order**. Only Confirm shows a popup: *"This will create the invoice and lock the order. Continue?"* |
| 3 | The status is called **Confirmed**, not "Completed". |
| 4 | Confirming an order **auto-creates the invoice**. Manual invoice creation is removed, except for old confirmed orders that have no invoice. |
| 5 | **Confirmed orders are locked**: no editing and no deleting. The Edit/Delete buttons are hidden, and a direct URL shows *"This order is confirmed and locked. It can't be edited."* |
| 6 | **Drafts don't touch stock at all** — no deduction, no reservation, no "hold" tracking of any kind. A draft is just saved data until confirmed. *(Revised: the earlier "on hold" popup/tracking idea below is dropped — kept simple per direct instruction not to add restrictions/mechanics in the model that aren't needed.)* |
| ~~7~~ | ~~Product search shows In stock / On hold / Available, with a clickable on-hold popup.~~ **Dropped.** No hold concept exists, so there's nothing to show here. Product search just shows real stock as it does today. |
| 8 | Saving a draft is **never blocked by stock**, no matter how much it asks for — the model applies no restriction at draft-save time. Instead, the **HTML/view layer shows a non-blocking warning** when a line's quantity exceeds the product's real stock (e.g. "Stock: 5, you're ordering 10"), computed by comparing against `Product.stock_quantity` directly (already fetched per row via the existing `get_product_info` AJAX call). Confirming is the only hard block: `SalesOrder.confirm()` checks real stock and refuses if anything's short, shown to the user as a clear, specific dialog/message (not a generic banner) listing exactly which products and by how much. |
| 9 | **Go-live cutoff of 25 Sep 2026**: all existing orders are marked Confirmed and locked, with no invoices backfilled and no ledger entries. Any fixes to old data will be done directly in the database later. |
| 10 | **Opening balance**: every customer gets one entry of **0**, dated 25 Sep 2026. An admin fills in the real amounts before production. |
| 11 | Payments attach to the **invoice**, not the sales order. |
| 12 | A sales order can't be edited after it's invoiced. Corrections will use **credit/debit notes** later (out of scope for now). |

---

## 3. Current code — relevant facts

- `SalesOrderItem.save()` / `.delete()` ([models.py](uniworlderp/models.py)) create `StockTransaction` rows (OUT / RET / IN). `StockTransaction.save()` then updates `Product.stock_quantity`. This is the only place sales stock moves.
- `SalesOrderItem.clean()` **blocks** saving when stock is too low. For drafts this check becomes a warning, and the hard check moves to confirm.
- `ARInvoice.payment_status` (P/C) is a **manual field** in the invoice form. It will become computed.
- `ARInvoice.save()` sets `total_amount` = sum of invoice item totals. That total ignores the sales order's `discount` and `shipping`, so the **invoice total would not match the order total**. We need to add `discount`/`shipping` to the invoice (or copy the order total over).
- Invoice creation is manual today (`ARInvoiceCreateView`, `invoice_create_from_sales_order`), with one invoice per order (`OneToOne` + unique constraint).
- Pre-existing issues this work touches (fix them along the way):
  - `SalesOrderDeleteView.delete()` deletes items in the view, **and** `SalesOrder.delete()` deletes them again, which risks returning stock twice.
  - The sales order views reference `reverse_lazy('customer_vendor:sales_order_search')`, but no URL has that name.
  - Debug `print()` calls with ✓/❌ characters in the sales order model and views can crash on Windows consoles.

**Templates actually read (not just the views)**, confirming what the UI work in Steps 1/3/4 is starting from:

- `sales_order/list.html`: the column labeled **"Status" is actually `delivery_status`** (Pending/Delivered) — there is no order-status column at all today. The new Draft/Confirmed status (Step 1) needs its **own** new column, not a repurposing of this one. There's only a single free-text search box (`?search=`), no filter dropdowns whatsoever. It already has a per-row **Invoice column** (Create Invoice / View Invoice / Print Invoice) and a **Return column** — some of the navigation from point 5 already exists at the sales-order level; a **Payment** column/link still needs adding once `Payment` exists (Step 4). The "Create Invoice" link shows whenever `not sales_order.invoice`, with no regard to status — once drafts exist this would wrongly offer "Create Invoice" on a draft, so this logic needs the status check added when Step 1/3 land. Uses the same shared `pagination.html`.
  - Uses `{% for num in page_obj.paginator.page_range %}` — reconfirmed here.
- `invoice/list.html`: also just **one free-text search box** (`ARInvoiceListView.get_queryset`, `?search=` matching id/customer/employee/date via `icontains`) — no dedicated sales-order filter, no date-range fields at all, confirming point 1 needs real filter fields, not a wider search box.
  - **Found a live bug, unrelated to this project but in the exact area we're touching**: the status badge does `{% if invoice.payment_status == 'paid' %}...{% elif invoice.payment_status == 'partial' %}...{% else %}` — but the model's actual stored values are `'P'`/`'C'` (`PAYMENT_STATUS_CHOICES`), never the strings `'paid'`/`'partial'`. Neither branch can ever match, so **every invoice shows the red "Pending" badge today regardless of its real status**. This gets fixed as a side effect of Step 4 (payment_status becomes computed as Pending/Partial/Paid and the template comparison will be corrected to match), but flagging it now since it's a real, currently-live display bug.
  - `paginate_by = 100` here vs `20` on the sales order list — inconsistent, worth aligning when these pages get their UI pass.

---

## 4. Build steps

Each step is built, tested and reviewed before the next one starts.

### Step 1 — Order status, confirm flow, locking, go-live date

- `settings.FINANCE_GO_LIVE_DATE = date(2026, 9, 25)` is the only place the date is defined.
- `SalesOrder` new fields:
  - `status` — `draft` / `confirmed` (default `draft`, indexed)
  - `confirmed_at` (datetime, null)
  - `confirmed_by` (FK User, null)
- Migration: schema change, plus a data migration that sets **all existing orders to `confirmed`**.
- Order form: **Save as Draft** + **Confirm Order** buttons, with a confirm popup for the second.
- `SalesOrderUpdateView` / `SalesOrderDeleteView`: if the order is confirmed, show a message and redirect to the view page.
- Order list: new **Status** column (Draft/Confirmed badge) and status filter, a **Draft since** age for drafts, and Edit/Delete hidden for confirmed orders.
- Order view page: a **Confirm Order** button for drafts (with permission).
- No new custom permission — confirming is gated by the existing `change_salesorder` permission, checked in the view and hidden/shown in the template accordingly (not a new model-level permission).
- **Order list filters, combinable**: order status (Draft/Confirmed), delivery status (Pending/Delivered), customer, sales employee, date range — any combination at once, not one-at-a-time.
- **Pagination fix**: `templates/includes/pagination.html` (shared by every paginated list — sales orders, customers, invoices, etc.) currently renders `{% for num in page_obj.paginator.page_range %}` with no limit, so a list with 20+ pages shows 20+ page-number links in a row with nothing to collapse them — that's why it overflows and is hard to use on a laptop. Fix it once, here, for every list at once: show first page, last page, current page ± 2, "…" for the gaps, and collapse to just Prev/Next + "Page X of Y" below a width breakpoint.

### Step 2 — Stock: deduct on confirm, warn (don't block) on draft

**Revised, simpler than originally sketched — no hold tracking of any kind.** Built and confirmed in the model already (see the note at the top of this file / Section 9's A1): a draft never touches stock and is never blocked by it; `SalesOrder.confirm()` is the sole place stock is checked and moved.

- `SalesOrderItem.save()` / `.delete()` never create a `StockTransaction`. `clean()` has no stock check either. A draft item can be saved for any quantity, always.
- `SalesOrder.confirm(user)` (atomic): totals the needed quantity per product across the order's items, checks each against real `Product.stock_quantity`, blocks with a clear per-product shortage list if anything's short, otherwise creates one `OUT` `StockTransaction` per item (reference `SO-<id>` — this **is** the trace/log) and sets `status='confirmed'`, `confirmed_at`, `confirmed_by`.
- **No on-hold number, no clickable popup, no separate reservation tracking.** Product search/list keep showing real stock as they do today — nothing new to compute or display there.
- **The only new UI feedback needed**: (a) on the order form, a non-blocking warning next to a line item when its quantity exceeds that product's real stock (compare against the `stock_quantity` already returned by `get_product_info`) — informational only, doesn't stop the draft from saving; (b) when Confirm is blocked, show the shortage list from `confirm()`'s error as a clear, specific dialog/message, not a generic banner — the user needs to see exactly which products and by how much.

### Step 3 — Auto invoice on confirm

- The confirm action (same transaction) creates an `ARInvoice`:
  - customer, sales employee and sales order copied from the order
  - invoice date = confirm date, due date = +30 days
  - invoice items copied from the order items (price, quantity, line total after product discount)
  - order `discount` and `shipping` copied, so **invoice total = order total**
- New `ARInvoice` fields: `discount`, `shipping`, and `invoice_number` if you want a printable number.
- Invoice becomes **read-only**. Payment status is removed from the form.
- Manual "Create Invoice" button: only shown for **confirmed orders with no invoice** (old orders).
- The invoice view page shows the total, paid so far, balance due and a payments table.
- **Invoice list / search UI**: search by sales order #, by customer, or by date — for the date, one **date-range picker control** (a single calendar widget with a start and end), not two separate raw `<input type="date">` boxes. Picking one day (e.g. 25 Sep) just sets both ends of the range to that date. Same range-picker component is reused on the Payments page and the Ledger (see Step 7).

### Step 4 — Payments

- **`PaymentMethod`** (admin-managed master list), e.g. "Cash", "City Bank A/C 1234", "Cheque", "bKash", "Nagad".
  - `name`, `kind` (`cash` / `bank` / `cheque` / `mobile`), `is_active`, `details` (account no. etc.)
- **`Payment`**
  - `invoice` (FK ARInvoice; one invoice has many payments)
  - `customer`, `sales_employee` (copied from the invoice, for fast reports)
  - `payment_date`, `amount` (> 0, ≤ balance due; no overpayment)
  - `method` (FK PaymentMethod)
  - Details by kind:
    - cash → `received_by` (who collected the cash)
    - bank → `bank_name`, `account_number`, `transaction_reference`
    - cheque → `cheque_number`, `cheque_date`, `cheque_bank`
    - mobile → `mobile_provider`, `mobile_number`, `transaction_id`
  - `notes` (free text on every payment slip)
  - `created_by`, `created_at`, `updated_at`
- `ARInvoice.payment_status` is computed from payments:
  - `Pending` (nothing paid) → `Partial` (some paid) → `Paid` (balance 0)
- **Recording payments is done on the invoice page**: total / paid / balance, a table of that invoice's payments, and an **Add Payment** popup. The form only shows the fields for the chosen method.
- **A separate Payments page** (menu item) lists every payment across all invoices, built with the Step 7 UI pattern (date-range control, KPIs, then filters, then table):
  - **filters, all combinable at once** (not one-at-a-time): date range, customer, sales employee, payment method, received by, invoice/order — any mix of these together
  - columns: date, payment #, customer, invoice / SO #, method, amount, received by, notes
  - each row links to its invoice, plus a daily/period total at the bottom (e.g. "cash collected today")
  - view-only: new payments are always added from an invoice, so every payment belongs to a bill
  - this page gets a real UI pass, not the plain filter-row-plus-table style of the current reports page — see Step 7
- A **payment slip / receipt** print page, reachable from both places.
- Edit and delete payments with permission (`change_payment`, `delete_payment`).
- **Easy back-and-forth navigation between Sales Order ↔ Invoice ↔ Payment**, on all three pages:
  - Sales Order page: a **"View Invoice"** button/link once one exists.
  - Invoice page: a **"View Sales Order"** link back, plus **"Add Payment"** and the payments table right there.
  - Payment row / receipt: links to both its invoice and that invoice's sales order.
  - This is just linking already-related objects (`ARInvoice.sales_order`, `Payment.invoice`) — no new data needed, just the UI wiring.

### Step 5 — Customer opening balance

- **`CustomerOpeningBalance`**: `customer` (one-to-one), `amount` (default 0), `as_of_date` (= go-live date), `notes`, `updated_by`.
- Data migration: one row with **amount 0** for every existing customer.
- New customers get a 0 row automatically.
- Admin screen: a customer list with an editable opening balance column, for bulk entry before production.

### Step 1c — Fix existing reports to exclude drafts

Every existing sales report queries `SalesOrder` / `SalesOrderItem` directly with **no status filter** (the field doesn't exist yet). Once drafts exist, all of these would silently count unconfirmed orders as real sales unless each one is updated to filter `sales_order__status='confirmed'` (or `status='confirmed'`). This has to land in **Step 1**, at the same time the status field is added, not later — otherwise there's a window where reports are wrong.

Confirmed affected views (each queries `SalesOrder`/`SalesOrderItem` independently, so each needs its own fix):

- `report_views.py`: `ReportView`, `ReportPrintView`, `ReportExcelView` (main Sales Report + Customer/Product/Employee/Date summary tabs), `CustomerReportView`, `CustomerReportPrintView`, `CustomerReportExcelView`, `ProductWiseReportView`, `ProductWiseReportPrintView`, `ProductWiseReportExcelView`, `CustomerWiseReportView`, `CustomerWiseReportPrintView`, `CustomerWiseReportExcelView`
- `sales_order_report_views.py`: `SalesOrderReportView`, `SalesOrderReportPrintView`, `SalesOrderReportExcelView`
- `customer_views.py`: `SalesOrderListView` (the "sales orders for this customer" tab)
- `sales_order_views.py`: `SalesOrderItemDetailedListView` (detailed list)

**Not affected**: `StockReportView`, `MinimumStockReportView`, `SingleProduct*ReportPrintView` — these read `StockTransaction` / `Product.stock_quantity` directly, and Step 2 already ensures drafts never create a `StockTransaction`, so stock reports stay correct automatically.

**Duplication found while checking this**: `ReportView`, `ReportPrintView` and `ReportExcelView` each independently repeat the same ~50 lines of filter-building code (customer/product/sales_employee/date range) almost word-for-word; the same 3x duplication exists in `sales_order_report_views.py`, `ProductWiseReport*` and `CustomerWiseReport*`. That's why the draft-exclusion fix above has to touch 15+ places instead of ~5, and it's a standing risk — a filter fix applied to the screen view can be forgotten in print/Excel. **While touching each family for the status fix, extract its filter/query building into one shared function** (e.g. `report_queries.py`), called by the screen view, the print view and the Excel view for that family. Not a merge of the different report *types* into one view (Sales Report, Product-wise, Customer-wise, Sales-Order-wise genuinely show different things) — just de-duplicating the three renderings of each one. The new finance reports (Step 6) are built with this shared-function pattern from the start, so they don't repeat the same mistake.

**Sales Order list itself** (`SalesOrderListView` in `sales_order_views.py`) is the one place that should keep showing drafts — it needs the opposite treatment: a status column/badge and a filter, not an exclusion.

### Step 6 — Ledger & reports

**Where things live** (standard ERP split: work screens vs reports):

- **Work screens** (daily entry): Sales Orders, Invoices (with Add Payment), and a Payments list.
- **Reports**: added as **new tabs on the existing `/erp/reports/` page** (`ReportView`, `uniworlderp/templates/reports/report.html`), not a separate page. That page already has exactly the filter set we need — customer, sales employee, date range — plus the tab pattern (`tab-button` / `tab-content`) and a per-tab "Export as CSV" button, so the finance tabs reuse the same form, filters and export mechanism as Sales Report / Customer Summary / Product Summary / etc.

New tabs to add:

| Tab | Shows | Answers |
|---|---|---|
| Customer Ledger | Opening balance + invoices + payments, with a running balance | Who owes what, and why? |
| Outstanding / Due | Unpaid and partly paid invoices with balance due | What is still owed right now? |
| Collections | Payments received, by method / received by / employee | What came in, and who collected it? |
| Receivables Aging | Each customer's balance grouped 0–30 / 31–60 / 61–90 / 90+ days | Who should we chase first? |
| Finance Summary | Totals per customer / employee / invoice | Where do we stand overall? |

`ReportView.post()` already computes `customer_summary` / `sales_employee_summary` / `date_summary` from `SalesOrderItem`; the new tabs add sibling summaries computed from `Payment` / `ARInvoice` instead, passed into the same template context and rendered as new `tab-content` blocks + `tab-button`s, following the existing markup exactly.

**Print and Excel for the new tabs**, matching the existing convention (`ReportView` → `ReportPrintView` / `ReportExcelView`, one pair per report family):
- `FinanceReportPrintView` / `FinanceReportExcelView` — Customer Ledger, Outstanding/Due, Collections, Receivables Aging, Finance Summary, same filters as the screen version.
- The Customer Ledger print/Excel output includes the **opening balance row** (Section 5) as the first line, exactly as it appears on screen, so the printed statement and the on-screen ledger always agree.
- CSV export uses the same per-table `export-button` pattern already on the page (`data-table-id`), so no new export mechanism is introduced — the new tables just plug into it.
- **Combined filters must match across all three** (tab / print / Excel): customer + sales employee + product/order + date range, all applied together, not a reduced subset in print or Excel. Built via the shared filter function described in Step 1c, so the screen, print and Excel outputs literally call the same query and can't drift apart.

**Checked `reports/report.html` directly — two different levels of support exist today, and the finance tabs need the stronger one:**

| Tab | Print (server) | Excel (server) | CSV (client) | Totals |
|---|---|---|---|---|
| Report (main) | ✅ `ReportPrintView` | ✅ `ReportExcelView` | ✅ | Computed server-side |
| Customer/Product/Employee/Date summary tabs | ❌ none | ❌ none | ✅ only | **Client-side JS**, parsed back out of the rendered table cells by column position (`calculateTotalBreakdown`) — breaks if a column is added/reordered |

Every finance tab (Customer Ledger, Outstanding/Due, Collections, Aging, Finance Summary) needs the **main Report tab's treatment** — a real server-side Print view and Excel view with totals computed in Python, not the summary tabs' client-side-only approach — since these numbers are money owed, not a nice-to-have export.

Two things found in this template that should **not** be copied into the finance tabs:
- The "Export as PDF" button (added via JS to every `.export-button`) depends on `import html2pdf from 'html2pdf.js'` inside a plain, non-module `<script>` tag — that import syntax is invalid there, so this button is already broken/dead today.
- The commented-out "Product-wise"/"Customer-wise" tabs and their JS are unused leftover code.

- **Customer ledger (statement)**, one row per event, oldest first, built with the Step 7 UI pattern (date presets → KPIs → filters → table → row drill-down):

  | Date | Type | Ref | Debit (owed) | Credit (paid) | Balance | Notes |
  |---|---|---|---|---|---|---|
  | 25 Sep 2026 | Opening balance | — | 0.00 | | 0.00 | |
  | 26 Sep 2026 | Invoice | INV-12 / SO-1042 | 50,000 | | 50,000 | |
  | 28 Sep 2026 | Payment (Cash, rcvd by Atiq) | PAY-7 | | 20,000 | 30,000 | "paid part, rest next week" |

  With a date range, rows before the start date roll into a **balance brought forward** line.
- **Filters, combinable** (any mix at once): date range (via the Step 7 presets), customer, sales employee, sales order / invoice.
- **Summary views**:
  - per customer: invoiced, paid, outstanding
  - per sales employee: sales invoiced, collected, outstanding across their customers
  - per order/invoice: total, paid, balance, status
- Print and Excel export, following the existing `report_views.py` pattern.
- New permission: **`view_ledger`**. Only admins or users with this permission can see ledger/reports.

### Step 7 — Shared UI pattern: date presets, KPIs, filters, drill-down

This layout is used by the **Ledger** tab (primary) and reused by the **Payments** page (Step 4), top to bottom:

```
┌─────────────────────────────────────────────────────────┐
│  Day │ Month │ Year │ Range   ← date presets (tabs)       │
│  (Range reveals the Step 3 date-range picker)             │
├─────────────────────────────────────────────────────────┤
│  [Invoiced]  [Collected]  [Outstanding]  [Overdue: N]      │  ← KPI cards, react to date range + filters below
├─────────────────────────────────────────────────────────┤
│  Customer ▾   Sales Employee ▾   Invoice/Order ▾   ...    │  ← other filters, combinable, applied on top of the date range
├─────────────────────────────────────────────────────────┤
│  [ table of actual rows, e.g. one per customer/invoice ]  │
└─────────────────────────────────────────────────────────┘
```

- **Date presets**, exactly as described: **Day** sets from = to = today. **Month** sets from = first day of the current month, to = today (this matches the default `ReportView` already uses). **Year** sets from = 1 Jan of the current year, to = today. **Range** reveals the manual date-range picker (Step 3) so the user sets from/to themselves. Internally all four presets just produce a `from`/`to` pair — the rest of the page only ever deals with one date range, however it was picked. **Default on page load: Month.**
- **KPI cards**, recomputed whenever the date range or filters change (e.g. for the Ledger: total invoiced, total collected, total outstanding, count of overdue invoices; for Payments: total collected, payment count, split by method).
- **Other filters** sit below the KPIs and combine with the date range and each other — customer + employee + invoice, all at once, not one-at-a-time.
- **Row drill-down**: clicking a row (a customer on the Ledger, a payment on the Payments page) opens a detail panel showing that customer's info, their sales order(s), invoice(s), current balance, and a compact **mini ledger timeline** — opening balance → each transaction tile → closing balance, laid out as a small horizontal flow rather than a full table, so the shape of "what happened" is visible at a glance.
- Applies first to the Ledger tab; the Payments page reuses the same date-preset bar and filter-combination behavior, but its own KPI set and table (Step 4).

---

## 5. Permissions summary

| Permission | Allows |
|---|---|
| `change_salesorder` (existing) | Also gates confirming a draft order (creates the invoice) — no new permission added |
| `add_payment` / `change_payment` / `delete_payment` | Record, edit or remove payments |
| `view_ledger` | See the ledger and finance reports |
| `change_customeropeningbalance` | Enter opening balances |
| `view_salesorder` | See the on-hold breakdown popup |

---

## 6. Testing

- Unit tests for each step (draft vs confirmed stock, blocked confirm on low stock, auto invoice totals including discount/shipping, locking, partial/multiple payments, computed status, overpayment blocked, ledger running balance, date-range brought-forward, permission gates).
- Smoke test against a **copy** of `db.sqlite3` after each step. The real database is never written to.
- Check that existing orders become confirmed and that their stock is unchanged after the migration.

---

## 7. Open questions (answer before or during the build)

1. **Payment method list**: is an admin-managed list (e.g. several bank accounts, bKash, Nagad) OK, or do you just want fixed choices (Cash / Bank / Cheque / Mobile)?
2. **Cash "received by"**: should this be a sales employee, a system user, or free text?
3. **Bounced cheques**: should a cheque payment have a status (Pending / Cleared / Bounced), so it only counts once cleared?
4. **Sales returns** after confirmation: should a return reduce what the customer owes? This needs credit notes and is currently out of scope.
5. **Invoice number format**: keep the plain ID, or use something like `INV-2026-0001`?

---

## 9. Sales Order build plan — this iteration (Steps 1 + 2 combined, Invoice/Payment/Ledger come after)

Read in full before writing this: `sales_order_views.py`, `sales_order/list.html`, `sales_order/form.html`, `includes/pagination.html`, plus `models.py` (`SalesOrder`, `SalesOrderItem`, `StockTransaction`) and `forms.py`'s `SalesOrderForm`/`SalesOrderItemFormSet`. Built and tested as one unit before moving to Invoice.

**Verified which template file is actually live**: `SalesOrderCreateView`, `SalesOrderUpdateView` and `SalesOrderDetailView` (`sales_order_views.py` lines 77/155/300) all use `template_name = 'sales_order/form.html'` — confirmed by grep, all three, no ambiguity. There is also a **`sales_order/forma.html`** sitting in the same folder (note the typo) that nothing references anywhere in the codebase — dead, same situation as the `sales_employee/form.html` duplicate found earlier in this session. Left untouched; all UI work below targets `form.html`, not `forma.html`.

**A1. Model** (`models.py`)
- `SalesOrder`: add `status` (`draft`/`confirmed`, default `draft`, indexed), `confirmed_at` (datetime, null), `confirmed_by` (FK User, null, `on_delete=SET_NULL`).
- New `SalesOrder.confirm(user)` method, atomic: locks the order's products (`select_for_update`), re-checks real stock for every item (blocks with a clear per-product list if short), creates one `OUT` `StockTransaction` per item (reference `SO-<id>`), sets `status='confirmed'`, `confirmed_at=now()`, `confirmed_by=user`.
- `SalesOrderItem.save()` / `.delete()`: only create a `StockTransaction` when `self.sales_order.status == 'confirmed'`. A draft item save moves no stock at all — "on hold" is a live aggregate query (`SalesOrderItem.objects.filter(sales_order__status='draft', product=X).aggregate(Sum('quantity'))`), not a stored value.
- No new custom permission — confirming is gated by the existing `change_salesorder` permission (checked in the view/template), per your instruction to keep permission logic in the view/frontend rather than adding new permission codenames to models.
- Migration 1 (schema): the three new fields.
- Migration 2 (data): set every existing `SalesOrder` to `status='confirmed'`. Two defaults I'm choosing since the earlier decisions didn't pin these down — flag if you want different: `confirmed_at = order_date` (best available historical stand-in) and `confirmed_by = owner` (the order's existing owner field). Stock is untouched either way since these orders already moved stock at creation time under the old behavior.

**A2. Views** (`sales_order_views.py`)
- `SalesOrderCreateView` / `SalesOrderUpdateView`: read `request.POST.get('action')` (`'draft'` or `'confirm'`). Draft = save as today. Confirm = save, then call `order.confirm(request.user)` in the same transaction; on a stock-shortage error, roll back and re-show the form with the error, order stays a draft.
- `SalesOrderUpdateView`: if `self.object.status == 'confirmed'`, redirect straight to the view page with *"This order is confirmed and locked. It can't be edited."* — never render the edit form for a confirmed order.
- `SalesOrderDeleteView`: same lock check, blocks delete on a confirmed order.
- New `SalesOrderConfirmView` (POST-only, `permission_required='uniworlderp.change_salesorder'`) — a small dedicated endpoint (same shape as the existing `SalesEmployeeAssignCustomerView` pattern) so a draft can be confirmed straight from the list or view page without reopening the full edit form.
- `SalesOrderListView.get_queryset()`: add `status` and `delivery_status` filters, combinable with each other and the existing `search` box, not replacing it.
- `SalesOrderDetailView` / list context: `can_edit` becomes `has_perm AND status == 'draft'` — locked is structural, not just a permission check.

**A3. Templates**
- `sales_order/form.html`: replace the single "Save Order" button with **"Save as Draft"** and **"Confirm Order"** (`name="action" value="draft"` / `"confirm"`), each setting the same hidden `action` value the view reads. Confirm shows a JS confirm dialog first — wording for *this* iteration: *"This will confirm the order and lock it from further edits. Continue?"* (mentions the invoice once Step 3 actually wires it in).
- Same template: when a row's quantity exceeds the product's real stock (already known client-side from `get_product_info`'s `stock_quantity`), show a **non-blocking inline warning** next to that row — e.g. "Only 5 in stock, ordering 10" — styled distinctly (amber/warning), never prevents saving the draft.
- When `SalesOrder.confirm()` rejects a confirm for insufficient stock, the view passes its per-product shortage list through; the template shows this as a **clear dialog/message naming each short product and by how much** — not a generic "error saving" banner — so the user immediately understands why confirm failed and what to fix.
- `sales_order/list.html`: the existing "Status" column is `delivery_status` — rename its header to **"Delivery"** and add a new **"Order Status"** column (Draft/Confirmed badge) so the two aren't confused. Add the two filter dropdowns (order status, delivery status) above the table, combinable with the search box. Hide Edit/Delete on confirmed rows, add a **Confirm** button on draft rows (permission-gated, hits `SalesOrderConfirmView`), and a **"Draft since"** relative-age label on drafts.
- `templates/includes/pagination.html`: window the page-number list (first, last, current ± 2, "…" for gaps) and collapse to Prev/Next + "Page X of Y" under a width breakpoint — fixes every paginated list in the app at once, sales orders included.

**A4. Tests**
- Model: `confirm()` blocked by insufficient stock (order stays draft, no `StockTransaction`s created, error lists every short product); `confirm()` creates the right `OUT` transactions and sets `confirmed_at`/`confirmed_by`; a draft item save/delete creates zero `StockTransaction`s regardless of quantity, including quantities that exceed real stock.
- View: create-as-draft (no stock movement, no block, even over stock) vs create-and-confirm (stock deducted, blocked with a clear message if short); edit/delete blocked once confirmed (redirect + message, not a 500 or silent no-op); list filters combine correctly (status + delivery + search together); `change_salesorder` permission enforced on both the form's confirm action and the dedicated confirm endpoint.
- Migration: existing orders come out `confirmed`, stock figures unchanged before/after.
- Smoke test against a **copy** of `db.sqlite3` (never the real file) after the migration runs, confirming the whole real dataset flips over cleanly.

Once this is built and tested end-to-end, the next unit is **Invoice** (Step 3), then **Payment** (Steps 4–5), then **Ledger** (Steps 6–7) — each read-then-plan-then-build-then-test the same way.

---

## 8. Out of scope (later)

- Credit / debit notes for correcting invoiced orders
- Automatic expiry of old drafts
- Vendor side (purchase orders → bills → payments to vendors)
- Advance / unallocated payments (customer pays before an invoice exists)
