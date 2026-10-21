# A company's own account codes (scenarios AN)

The owner, 2026-10-21, after importing فرهنگ نو's Excel journals: «the coding is still wrong in دفتر کل». The
import keeps the app's codes and remembers the school's beside them (#378, option b), so the Ledger shows
6132 «هزینه های عمومی» with 613201… under it where the school writes 3000 with 3001…, and the school's
sub-accounts don't even keep their order (their 3001 became 613203). Asked which codes the books should
carry, the owner chose **the school's codes only**.

## Why that can't simply be a rename

The app reads what an account *is* from its code's digits, in about 95 places (the 2026-10-21 survey):
`classify_account_code` (51 calls, and every debit/credit sign), the Iranian and UK statement buckets,
cash and bank (`cash_service`, `cfo_intelligence`), receivables and payables (`api/reports.py`,
`party_accounts`), posting roles' required first digit, the fixed `POSTING_CODES`, the chart's
"a child's code starts with its parent's", and 15 places in the web pages. The school's numbering
says nothing of the kind: creditors 1600, provisions 1700, loans 1800, tuition 2000, expenses 3000–5000,
and 5000 is both rent and «سود و زیان». Renamed as they are, creditors would be assets, tuition a
liability and the P&L empty.

## The design: each account keeps the app's code for what it is

- `accounts.class_code` (migration 083): the app's own code for what the account is, when its `code`
  is the company's own. Empty for every chart made here (the class is the code), so nothing changes
  for any other company.
- `app/services/chart_codes.py`:
  - `class_of(code)`: the class of an account by its code, from a map of the company's
    `class_code`s loaded once per database session (from the session the request is already
    querying with) and dropped when an account changes;
  - `class_expr()`: the same in SQL (`coalesce(class_code, code)`) for the `LIKE '4%'` filters.
- Every rule that reads meaning from a code reads its class: `classify_account_code`, the statement
  buckets, cash, receivables and payables, posting roles, the fixed posting codes (found by class),
  the chart's parent rule (a company's own chart nests by parent, not by prefix), and the web pages'
  drill-downs (the server sends each code's class).
- The safety net: **renumbering a company's accounts with their classes kept changes no figure.** A
  test books a year of every kind of entry, takes the trial balance, the balance sheet, the P&L, the
  cash flow, the dashboard, the CFO figures, the insights and the reviews, renumbers every account
  to arbitrary codes (their classes kept), and takes them again: they are the same.

## Phases (one PR each)

1. **The engine** (invisible): `class_code`, `chart_codes`, every rule through it, the safety net.
2. **Switching a company to its own codes**, a confirm-gated action (the companies console and the
   company's settings): a preview lists each account's new code (its `source_codes`, the code its
   journal used most), the clashes to settle (two accounts claiming one code; one account with two),
   the app's own accounts that are unused and would hold a code the company uses (put aside, not
   deleted), and the headings, which take the company's ledger structure. On apply: `class_code` =
   the old code, `code` = the new, parents as the company's chart has them, and every stored
   reference to an old code (statements, commitments, parties, fixed assets, recurring rules, savings
   goals, holdings, petty cash, learned preferences, trial balances, posting roles, the journal
   map) follows. An audit row; reversible the same way.
3. **Importing with the company's own codes**: once a company uses its own codes, a journal's codes
   are its accounts' codes; a new code is made under the ledger its journal names, its class
   inferred from the title and the ledger (and asked when unsure).
4. **The web pages**: drill-downs and defaults by class.

## Scenarios

| ID | Scenario | Steps | Expected |
|----|----------|-------|----------|
| AN1 | Nothing moves for anyone else | Any company with the app's chart | Every report, card and figure is as before; `class_code` is empty |
| AN2 | Renumbered, the same figures | A year of every kind of entry; renumber every account to arbitrary codes, classes kept | The trial balance, balance sheet, P&L, cash flow, dashboard, CFO figures, insights and reviews give the same figures (only the codes differ) |
| AN3 | The school's codes in the Ledger | فرهنگ نو, switched to its own codes | The Ledger reads 3000 «هزینه های عمومی» (دفتر کل) with 3001 «پذیرایی…», 3002 «ملزومات مدرسه», 3003…, 3006; 4000 «هزینه های پرسنلی» with 4001, 4002; 1602 «وزارت دارایی»; no 6132, 613201 or 219703 |
| AN4 | The statements still right | AN3's books | Tuition (2000) is revenue, creditors (1600…) liabilities, the expenses (3000–5000) on the P&L, the banks (1100, 1101) and petty cash (1110) cash: the same balance sheet and P&L as before the switch |
| AN5 | Clashes settled first | A journal giving 5000 to both «هزینه اجاره» and «سود و زیان», and 1310 to rent | The preview asks which keeps 5000 (the journal's own use first); nothing changes until it's settled |
| AN6 | Everything follows | A statement, a recurring rule, a bank party, a savings goal on an account that is renumbered | Each keeps pointing at the same account under its new code |
| AN7 | Typing their codes | «۵۰۰ هزار تومان ملزومات، کد ۳۰۰۲» in the chat; the voucher form's account box; the chart's search | 3002 is «ملزومات مدرسه» everywhere; a new account is made with the next code under its ledger (3007) |
| AN8 | The next year's file | 1406's journal in the same codes, dropped after the switch | Each code is its account: nothing to match by hand |
