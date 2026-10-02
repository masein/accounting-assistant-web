# Deep browser test, 2026-10-02: results

Scenarios: `docs/qa/SCENARIOS.md` (as of this run). Findings: `FINDINGS.md` next to this file.

**Setup.** A throwaway server (`aa-qa`, a fresh scratch database) running `main` up to #257; #258 merged during the run. Driven by Playwright (Chromium) through the real UI, with results read back through the API.

**Fake data.**
- **Tenants**, all created through the super-admin console: بازرگانی آرمان (ir, IRR, Jalali), Thames Studio Ltd (uk, GBP), and خانواده سارا (personal).
- **Arman's data**: 6 users, 11 parties, 2 stock items, about 30 vouchers, 10 invoices and bills, cheques, a 12-instalment loan, payroll, an asset, a cap table with a dividend, budgets and an FX rate.
- **Screenshots**: 400+.

**Legend.**
- ✅ pass.
- ❌ fail; the finding numbers are in FINDINGS.md.
- ✳ my test or scenario was wrong and has been corrected (not an app bug).

| ID | Result | Notes |
|---|---|---|
| A1 | ❌ | Tenants created correctly; super-admin 500 (#4), logo 404s (#5), raw region codes (#6) |
| A2 | ❌ | A new Iranian owner lands in English (#2) |
| A3 | ✅ | Profile saved; identifiers typed in Persian stored as 0–9; logo and signature stored; a fake PNG refused in Persian |
| A4 | ❌ | Every role lands on the right page with the right nav; the password rule message is English (#3) |
| A5 | ✅ | Tenant isolation: another company's party → 422/404 on read, edit and delete |
| A6 | ❌ | Sign-in page and error in English on a Persian browser (#1); no account enumeration; sign-out works |
| B1 | ✅ | Sub-account with a suggested code, rename, refusals in Persian |
| B2 | ✅ ✳ | An unbalanced opening entry posts the difference to "opening adjustments" and says how much; that's by design |
| B3 | ❌ | Codes keep Persian digits (#8); 15 unnamed inputs (#10); search across Arabic/Persian letterforms works; duplicate warning in Persian |
| B4 | ❌ | Barcodes keep Persian digits (#9); stock valuation right (40 × 250,000 = 10,000,000) |
| C1 | ❌ | Jalali picker, Persian-digit amounts, balance bar, attachment all work; the save confirmation is English and Gregorian (#13) |
| C3 | ❌ | Itemised invoice posts; due date a day early (#14, fixed by #258); VAT at 9% (#15, **question**); Persian PDF |
| C4 | ❌ | Partial then full payment work; statuses shown raw in English (#16) |
| C5 | ✅ | Bill and payment |
| C6 | ✅ | Quote → sent → accepted → converted, named invoice |
| C7 | ❌ | Credit note and void work; raw statuses (#16); credit note on a paid invoice disabled (#44) |
| C9 | ✅ | Received cheque → deposit → clear → settled; Sayad ID stored as 0–9; duplicate Sayad message English (#21) |
| C10 | ✅ | 12 instalments, first on 1405/07/30 |
| C11 | ❌ | Rule saved and run; the bank list ignores chart bank accounts (#23); the bank party's account name is English (#22) |
| C12 | ❌ | Persian-header Jalali CSV parsed (5 rows); re-upload asks to confirm in Persian; status `parsed` raw (#26) |
| C13 | ✅ | Bank SMS parsed and proposed |
| C15 | ✅ ✳ | Settings in Persian digits; employee claim in Persian. Under the threshold it skips the manager by design (my scenario expected the queue) |
| C16 | ✅ | Project, rate in Persian digits, 12.5 h → preview 18,750,000 with a Jalali range → invoiced once |
| C17 | ❌ | Order date empty (fixed by #258) |
| C18 | ✅ | Profiles (monthly and hourly), run for Mehr |
| C19 | ✅ ✳ | Depreciation starts the month after entering service (Article 149), so 4 × 10,000,000; my scenario said 5 |
| C20 | ✅ | Cap table 60/40 = 100%; contribution; dividend split 60,000,000 / 40,000,000 |
| C21 | ✅ | Budget saved; category is a bare code (#28) |
| C22 | ✅ | USD rate in Persian digits; Jalali effective date |
| D1 | ✅ | Search; account detail with Jalali dates |
| D2 | ❌ | TB balances (3,191,077,500 both sides); all 14 report types render; English column headers (#29); TB with only an end date is empty (#30); PDF and XLSX export |
| D3 | ✅ | Dashboard numbers consistent; UX overload (#36–#38) |
| D4 | ✅ | CEO / CFO in Persian |
| D5 | ✅ | Audit trail has the run's actions; audit findings in Persian |
| D6 | ❌ | Lock works and the refusal is Persian, but its dates are Gregorian (#31) |
| D7 | ✅ | TTMS season loads; Moadian lists the invoices with Jalali deadlines |
| D8 | ✅ | 13-week forecast in Jalali |
| E1 | ✅ | "The assistant can't answer right now" in Persian |
| E2 | ❌ | A CSV in the chat needs the AI (#43) |
| E3 | ✅ | Journal import preview |
| F1 | ✅ | Employee kept off invoices and settings; API write 403 in Persian |
| F2 | ✅ | Viewer: no write buttons; API write 403 in Persian |
| F3 | ✅ | Manager can't read invoices |
| G0 | ❌ | **Reset from the parties page gave the UK company the Iranian chart (#11/#33)** |
| G1 | ❌ | UK VAT 20% right; Moadian/TTMS shown for a UK company (#20); due date a day early in London (#258) |
| G2/G3 | ✅ | Spanish and Arabic sweeps: no untranslated UI text; Arabic mirrors right to left |
| H1 | ✅ | Personal tenant sees exactly its 6 pages; car loan plan |
| I | ❌ | 181 page visits (fa desktop/tablet/phone, en desktop/phone, es, ar, personal phone): **no JS errors, no failed requests, no slow page**; UX findings #36–#42 |

**Totals: 49 scenarios run. 29 pass (4 of those needed a corrected expectation), 20 fail.** 44 findings: 5 P1, 25 P2 (counting the two "?"), 14 P3.
