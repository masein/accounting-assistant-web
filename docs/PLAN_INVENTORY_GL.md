# Stock in the books (perpetual inventory)

**Decision (owner, 2026-10-13):** stock movements post to the general ledger.

Before this change, stock lived only in `inventory_movements`, valued by `app/services/inventory_costing.py` (weighted average or FIFO, recomputed from every movement). Nothing reached the ledger:
- no stock on the balance sheet;
- no cost of goods sold on the profit and loss;
- invoices didn't move stock.

The module cross-check M2 recorded this gap (`tests/test_module_crosscheck.py`).

## The design

**One switch per company, with a start date.** `AppSetting inventory_ledger = {"enabled": true, "from": "2026-10-01"}`, set on the Inventory page.
- Existing books have expensed their stock purchases, so posting from the first movement would count them twice.
- From the start date, stock posts. Before it, nothing changes.
- On switching on, the value of the stock on hand the day before is brought onto the books once: debit stock, credit the stock adjustment account (`STK-OPEN`). The UK convention is the closing-stock entry through cost of sales.

**The accounts** (posting categories in `account_resolver`, created on first use, overridable in Settings → posting roles):

| Category | UK (FRS 102) | Iran |
|---|---|---|
| `inventory` | 1000 Stocks | 1145 موجودی مواد و کالا (the seed's 1140 is accrued income) |
| `cogs` | 5010 Cost of goods sold | 5110 بهای تمام‌شده کالای فروش‌رفته (group 51 is created if missing) |
| `stock_adjustment` | 5900 Stock movement adjustment | 5190 تعدیل و ضایعات موجودی کالا |

The statements already have the lines:
- UK `10` → stocks, and `5` → cost of sales;
- Iran `114` → inventories, and `51` → cost of sales.

**What posts, and when:**
- **A purchase invoice's stock lines** (lines with an item, dated on or after the start) debit stock instead of purchases. The invoice also records an IN movement per line, at the line's net cost in the base currency. The movement keeps the value the invoice posted (`ledger_value`). (Phase G2.)
- **A sales invoice's stock lines** record an OUT movement. Revenue posts as before. (Phase G2.)
- **Cost of sales and adjustments** are posted by the **stock sync**, one journal per month and kind (`STK-2026-10`), and are recomputed whenever stock changes:
  - `cogs`, the costing engine's cost of the month's OUT movements that came from sales: debit cost of sales, credit stock;
  - `adjust`, the month's manual movements: counts, write-offs, stock found, opening stock entered by hand. A manual IN debits stock and credits the stock adjustment account; a manual OUT does the opposite. This kind also picks up any rounding left between an invoice's posted value and the engine's.
  - Production nets to nothing: components go out and the finished item comes in at their total.
- **Re-running the sync posts only the difference** against what was already posted for that month and kind (`inventory_postings` keeps each amount and its journal).
  - A back-dated purchase that changes the cost of later sales is therefore corrected in every open month.
  - A closed month is never changed. Movements dated in a closed period are refused, like every other posting (`assert_period_open`).
  - Switching between weighted average and FIFO, or anything else that moves a closed month's figures, is caught up in the first open month (`adjust`, described as such). So the ledger's stock always equals the costing engine's valuation from the first open month on.
- **A voided invoice** removes the movements it made. Its own entry is reversed as today, and the sync takes the cost of sales back.

**When the sync runs:** after every stock change (a movement, production, an invoice with stock lines, a void, a method change), and from the Inventory page. It posts nothing when nothing changed.

**What the page shows:** the switch and its date; the stock value in the books beside the costing engine's value; the last sync. If they differ (only possible through a closed month), it says by how much.

## Phases

| Phase | What | Done when |
|---|---|---|
| **G1, the engine** | Accounts and roles; the switch with the opening entry; `inventory_postings` and `inventory_movements.ledger_value` (migration 076); the sync; period locks on manual movements and production; the Inventory page's switch and status | Manual movements and production post; the balance sheet shows the stock and the P&L its adjustments; the ledger equals the valuation under both methods; a back-dated movement and a method change are caught up |
| **G2, invoices** | Purchase invoice stock lines debit stock and record INs; sales invoice stock lines record OUTs, and the sync posts their cost of sales; void; the invoice form picks an item per line; PO bills already carry their items | Buy 10 at 100 and 10 at 200, sell 15: cost of sales 2,250 (weighted average) or 2,000 (FIFO), stock 750 or 1,000, the same in the ledger, the statements and the valuation |
| **G3, the rest** | Stock returns on credit notes; stock counts (counted vs on hand → one adjustment); the AI accountant reads the ledger's stock; the close checklist runs the sync | A return puts the stock back at its cost; a count posts one adjustment |

Scenarios: section T in `docs/qa/SCENARIOS.md`.
