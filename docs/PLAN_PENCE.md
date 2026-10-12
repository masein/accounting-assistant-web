# Keeping pence (2026-10-12)

**Decision.** The owner decided on 2026-10-12 to keep pence. Until now the books kept whole units of every currency. A Barclays statement's £7.50 fee went in as £7 and its closing £24,515.50 as £24,515. So once every row was posted, the balance check was still £1 out (docs/qa/runs/2026-10-12-android-uk). Pounds, dollars and euros keep their cents from now on. Rials don't change.

## How amounts are kept

- **Exact decimals in the currency's own unit.** £7.50 is stored as 7.50, never as 750 pence.
  - Money columns become `NUMERIC` without a fixed scale (`app/core/money.py`, `MoneyType`).
  - Postgres keeps each value as written, so whole amounts add up and print as integers, as the `BIGINT` columns did.
  - Rial books keep their values byte for byte. Converting the columns rewrites no data.
- **Whole amounts come back as Python `int`.** Only an amount with pence is a `Decimal`. Every report, sum and sentence that works today keeps working for whole amounts. Pence reach a code path only when someone actually has pence.
- **Why not integer pence (×100)?**
  - Every input, output and exchange-rate conversion would need the currency's scale.
  - A miss would be a silent 100× error, the worst kind in books.
  - Exact decimals fail loudly instead: a `Decimal` multiplied by a float raises. At worst they cut pence, which is today's behaviour.
- **Exchange rates are unchanged.** A rate still means one unit of the currency in units of the base. Base amounts are rounded to the base currency's places, not to whole units.

## The rules

1. **Places per currency (ISO 4217).**
   - Pounds, dollars and euros have 2 places.
   - Rials and tomans have 0: ISO gives IRR 2, but nobody writes a fraction of a rial.
   - Yen and won have 0.
   - Kuwaiti, Bahraini and Omani dinars have 3.
   - `app/core/money.exponent()` is the only table.
2. **An amount never has more places than its currency.** £7.505 and 2,500,000.5 rials are refused with a sentence a person can act on.
   - A unit price may go to 4 places (£0.1250). So may an hourly rate; a salary is an amount and has its currency's places.
   - A line's total is rounded to the currency's places.
3. **Rounding is half-up, at the point a figure is made:** VAT on a line, a payslip's tax, a base amount, a depreciation charge. It never happens when an amount is read.
4. **Display:**
   - A currency with places shows them wherever an amount is money: «£7.50», «£950.00», «£23,923.00».
   - Rials show none.
   - Chart axes and compact figures («£23.9k») stay compact.
5. **JSON:** whole amounts are integers and others are numbers with their places (`7.5`). Every client already reads integers.
   - That holds for every response, including routes that return a plain dict: pydantic writes a `Decimal` it finds in a dict as a string («"1250.75"»), so `money.install_json_numbers()` sends every amount through `for_json` first.
6. **Splits put the odd penny in one place.** An installment plan front-loads it (£100 in 3 is £33.34, £33.33, £33.33). A dividend gives it to the last holder in the allocation. Depreciation schedules and accrual periods add up to the exact total.
7. **Rials round as they always did.** Payroll and mileage were half-up, so they stay half-up; invoice lines kept Python's `round()`. The Iranian year-end (eidi, sanavat) is rials only and is unchanged.

## Phases (one PR each)

| Phase | What | Done when |
|---|---|---|
| **P1, storage** | `app/core/money.py`; about 70 money columns to `MoneyType`; migration 075 (`ALTER … TYPE numeric`). No behaviour changes | The full suite passes unchanged on SQLite and Postgres; a rial company's reports are byte for byte the same |
| **P2, the ledger and statements** | Journal lines (API, web forms, imports) take pence and are refused past their currency's places; base amounts, FX conversions and realised FX in the base currency's places; every report that reads the ledger keeps pence (no `int()` on sums; response fields `Money`); the statement reader keeps pence (CSV, Excel, OCR, vision) and the import rounds to the statement's currency; statement review reconciles to the penny; JSON columns and the phone's stream write amounts with pence; the chat's voucher tool and its prompt; the phone reads and edits pence (exact amounts, «£7.50»); the web shows «7.50» where it showed «7.5» | A Barclays statement with a £7.50 fee posts £7.50 and the balance check closes at 0 |
| **P3, sales and purchases** | Invoices, items, VAT per line, payments, credit notes, quotes, recurring invoices, purchase orders; their forms, PDFs (pence in words: «… pounds and fifty pence»), AR/AP and aging; HMRC VAT boxes 1–5 to the penny | A £19.99 + 20% VAT invoice is £23.99, paid in two parts to £0.00 |
| **P4, the other modules** | Payroll (UK rates, thresholds and rounding to the penny), expenses and mileage (45p a mile), petty cash, fixed assets, inventory, equity, budgets, commitments, recurring rules, savings goals, thresholds in settings | A £12.21 hourly rate and a payslip agree with HMRC's calculator to the penny |
| **P5, words and files** | The accountant's words (every tool's figures, the quick answers), notifications and digests, CSV and Excel exports (`#,##0.00`), cheque printing, close pack and report card; the web shows every amount in a currency with places with them («£950.00»), not only those with pence; averages and runway to the penny (subscription medians, goal pace, burn rate), the chat's chart blocks; the bots | Every sentence and file says £7.50 where the books hold 7.50 |
Imports (Sepidar, journals, Excel, SMS) take pence from P2 for statements and from P3/P4 for their module. Sepidar and bank SMS stay rial-only.

## Risks and how each is caught

- **A `Decimal` meets a float** (`amount * rate`). This raises, and only when pence exist. Every phase adds pence fixtures to its module's tests.
- **An `int()` on an amount drops pence silently.** Each phase greps its module for `int(` on amounts and replaces them with `money.plain()`.
- **Raw SQL sums on Postgres return `Decimal`,** as they did for `BIGINT`. Formatting goes through the money formatters, never through `f"{x:,}"`, once a figure can have pence.
- **JS numbers.** Amounts stay below 2^53 at two places. The web formats with the currency's places and parses inputs with `parseFloat`, then rounds to them.
- **Old phone builds.** None is released, so the block contract can carry decimals.
