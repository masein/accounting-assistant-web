# The Android app with UK books, in English, 2026-10-12

**What is tested.** `main` after #343. The app ran on the emulator (Pixel 8, API 36, English) against the test server, with a UK company seeded for the run:
- «Hartley & Co Ltd», on the UK chart, in GBP, with a Gregorian calendar;
- a bank, «Barclays Business» (1201);
- a client, Aria Design Ltd, owing INV-1001 (£1,200 + 20% VAT = £1,440);
- a supplier, Northwind Supplies, owed NW-778 (£360);
- rent, office and travel costs this month (£1,077), against an overheads budget of £2,000;
- an owner, `e2e_uk`, whose language is English.

The test server has no model, so every answer comes from the deterministic paths. Scenarios N40–N43.

## The checks

1. **N40, the first run and the six questions.** The questions were cash, who owes us, what we owe, spending, the budget and the forecast. Every answer is in English, with Gregorian dates and the UK chart's account names. The forecast is drawn over Gregorian weeks («Today, 23 Nov, 4 Jan 2027»).
2. **N41, a Barclays statement.** A CSV from Downloads (Date, Description, Money out, Money in, Balance; days first), then Fix step by step.
3. **N42, a draft voucher with VAT.** Its card, the Edit sheet with a UK-style date, then Post.
4. **N43, the app's own screens.** The account and conversations sheets, dark mode, offline, a chosen file, undo, and reopening the app.

## Results

All four pass after the fixes below. The balance check under N41 is out by £1, because the books keep whole pounds (see the owner's question below).

**Eleven findings, fixed in this PR:**

1. **The statement wasn't read.** «I couldn't tell the statement's columns apart. Upload it on the Bank statements page…»: the phone has no such page. The reader knew Debit/Credit and Persian headings but not the UK banks' ones. It now reads them all, with dates day first:
   - Barclays: «Money out / Money in»;
   - HSBC: «Paid out / Paid in», «Details»;
   - Monzo: «Name» with a signed «Amount».

   A heading that merely contains «in» or «out» («Account in use») isn't taken for money.
2. **The statement was checked against the wrong account.** The file says «Barclays», but the bank on file is «Barclays Business». Only Iranian banks' names were matched, so the statement fell back to the chart's 1200. Its own account 1201 was then excluded as "another bank's", so all 5 rows were "not in the books" and the gap read £24,515. Now a statement's bank name, as whole words, found in exactly one bank's name is that bank. Two Barclays accounts still need the account number. The card now reads: 3 matched, 2 not in the books, a gap of £592.
3. **A customer's payment was offered as a sale.** «ARIA DESIGN LTD INV-1001 £600» was drafted as Dr 1201 / Cr 4000 Sales. That counts the sale twice and leaves INV-1001 open. Fix step by step now offers a payment on the open invoice the row names, by its number or as the one open invoice of the party it names («ARIA DESIGN» without «Ltd»). The card reads Dr 1201 / Cr 1100 Trade debtors, and posting it leaves £840 on the invoice. Money out does the same for a supplier's bill (Dr trade creditors). Two candidates is not a guess: those rows stay vouchers.
4. **Undo under a payment card failed.** Undo knew only vouchers. Now, within the window, a payment's entry goes and the payment comes off the invoice, so the balance reopens. The statement row is offered again. After the window, Reverse posts a compensating entry instead.
5. **Pounds were written two ways.** The cash card said «£23,923» while the sentences, the forecast chart, the statement card and the widget said «23,923 GBP». The sentences now write «£23,923», «€1,440» or «1,440 IRR» (Persian keeps «۲۳٬۹۲۳ ریال»). On the phone, the chart, the statement's gap, tables and the widget all go through one helper. Rial tables keep bare numbers.
6. **«1 sales invoices are still open».** Now «1 sales invoice is still open», and likewise for supplier bills, in all four languages.
7. **The same question asked twice lost its table.** Every quick answer to «what do we owe?» had the block id `list_invoices:fast-payables`. The phone keeps one card per id, so the second answer was only words. Each answer now has its own ids.
8. **A reopened posted card lost its seal; an undone one came back posted.** History sent only «executed». Now it also sends the entry's reference and date, and whether it was undone since. An app older than this one still reads «executed» as posted.
9. **A chosen file covered the last message.** The composer and what sits above it float over the conversation, which kept a fixed 104dp clear. The conversation now keeps clear of their measured height and moves up when they grow; the offline notice is covered the same way.
10. **An undone window left an empty ring, and the seal covered the last amount.** The ring now shows only while undo is possible. A posted card's footer is tall enough for the seal to sit under the lines.
11. **The attach menu didn't mention CSV.** The picker took CSVs, but the menu said «Choose a file (PDF, Excel)». It now says «(PDF, Excel, CSV)», in all four languages.

**Seen and left as they are:**
- **The gap's colour.** While rows are still to record, the statement card's gap is now grey, not red; the server already calls it information until they're posted.
- **The Edit sheet's date.** It is a text field with an ISO hint («Like 2026-10-10»). «11/10/2026» was read as 11 October, the UK way. A date picker would be friendlier.
- **Card titles.** A payment card's header says «Draft voucher», like every proposal; its lines show what it does.
- **Not checked live:** the widget (its «£23,923» is covered by a unit test) and the app shortcuts.

**An open question for the owner: pence.** The books keep whole pounds, though the money columns are documented as "the smallest unit". As a result:
- a statement's £7.50 fee is posted as £7;
- its closing balance £24,515.50 is compared as £24,515;
- once every row is in, the balance check is out by £1.

Rounding instead of cutting would not help: hand-keyed amounts are cut, so the two would stop matching. Keeping pence means storing pence (×100) for currencies that have them: a migration and a change to every money input and output. The chat, the web and the phone would read and write pounds and pence.

**Screens reviewed** (scratchpad):
- the first-run answers;
- the statement card before and after the fixes;
- each difference;
- the payment card and its seal;
- the Edit sheet with a UK date;
- the account and conversations sheets;
- dark mode;
- the attach menu and the chosen file;
- offline, then back online;
- a reopened posted card, and an undone one;
- the question asked twice.
