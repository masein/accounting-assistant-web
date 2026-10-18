# Deep test findings, 2026-10-16

Each fix PR marks its rows here (Status: the PR that fixed it, or *question* when it waits on a decision).

Severity:
- **P1**: wrong data or money, safety risk, or a broken main flow.
- **P2**: wrong language or format, or a confusing main screen.
- **P3**: polish.

| # | Sev | Where | Finding | Seen in | Status |
|---|---|---|---|---|---|
| 1 | P1 | Manager reports → Balance sheet, UK books | The dates chosen were ignored. The page sent `to_date` / `comparative_to_date`, but the UK sheet reads `as_of` / `comparative_as_of`, so it always compared today with a year before, and its "what changed" windows did too. Asked for 2026-10-15 against 2026-09-16, it answered as of 2026-10-16 | AA7 | fixed (this PR) |
| 2 | P2 | Manager reports → Balance sheet chart, UK books | The Liabilities bar was only the creditors due after one year. A company owing VAT (due within one year) showed none, while the bar's own window listed it | AA8 | fixed (this PR) |
| 3 | P2 | Phone chat: a statement shared in with no words | «mellat-….csv» of two dated rows with a balance, shared from the bank's app without a message, was not taken as a statement: it went to the model (502 with none set up), where any words («صورتحساب ملت») imported it. A file the bank named, with a running balance on dated rows, is now its statement however short; a price list named for a bank still goes to the assistant | AE1, N44 | fixed (this PR) |
| 4 | P3 | Web chat on a phone | Reply, Copy and Share beside a message were 23 × 19 px | I5 | fixed (this PR): 32 × 32 on a touch screen |
| 5 | P3 | اشخاص on a phone → «Accounts that could be parties» | The type picker (customer, supplier, bank…) was squeezed to 26 px, its words unreadable | I5 | fixed (this PR): it keeps its width and the table scrolls |
| 6 | P2 | The chat's briefing in Persian (and the phone's notifications) | A count kept as a number stayed in Latin digits: «در ۳۰ روز: 0 سند برگشت خورده و 4 یادداشت بستانکار». Seen in a phone screenshot, which the automated checks don't read for digits | I (phone, ai-accountant) | fixed (this PR) |
| 7 | — | A party's transactions window | A sale to a customer (Dr receivables, Cr sales) is listed under «بستانکار», a payment from them under «بدهکار»: the columns are the party's own side (what they received, what they paid), and the balance is right. Iranian party statements (Sepidar's among them) list a customer's sale under «بدهکار». Should the columns follow the books' side? | U1 | *question* |
| 8 | — | The chart, Iranian books | A ledger added under «۱۱ دارایی‌های جاری» takes the next code after 1150 «پیش‌پرداخت هزینه‌ها» (1151, 1152…), and the balance sheet places an account by its code, so it counts as a prepayment, not a debtor, and isn't offered as a party. A debtor's sub-account belongs under 1112. Not a fault; worth a hint on the chart page? | X1 | note |

Not app faults (the harness, fixed in this PR):
- **Persian digits (#338).** Eleven checks looked for Latin digits and separators («1405/07/07», «60,000,000», «6112») on Persian pages that now write «۱۴۰۵/۰۷/۰۷», «۶۰٬۰۰۰٬۰۰۰», «۶۱۱۲», the owner's call of 2026-10-12. `qalib.lat()` reads them in Latin digits: C1, C10, C12, C16, C19, C20, C22, C23, D1, D6, D8.
- **The credit note's amount prompt (#366).** It now opens once the page knows whether goods can come back (a request), so C6/C7 waits for it; the prompt had opened over the next step.
- The new group's own first tries: entries dated inside D6's lock, a debtor ledger under 11 (finding 8), a refusal tried with an amount over the limit, Persian's «از … به …» read for an arrow, and statement rows that the earlier groups had already recorded.
- **English in Persian or Arabic (I3)** on the Inventory page and in the chat: «Widget 447», «Gadget 447», «📄 mellat-447.csv», the names the test gave its own items and file.
