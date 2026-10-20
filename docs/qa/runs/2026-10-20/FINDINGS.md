# Deep test findings, 2026-10-20

Each fix PR marks its rows here (Status: the PR that fixed it, or *question* when it waits on a decision).

Severity:
- **P1**: wrong data or money, safety risk, or a broken main flow.
- **P2**: wrong language or format, or a confusing main screen.
- **P3**: polish.

| # | Sev | Where | Finding | Seen in | Status |
|---|---|---|---|---|---|
| 1 | P1 | Weekly and monthly reviews, books whose amounts have places (UK pence; a person's books in Omani rials, the owner's own) | A review kept an amount with places as text: Thames' week was stored with `"income": "2459.97"`, and the card compared it with a number (`TypeError: '>=' not supported between 'str' and 'int'`). The bell lost the review, the web chat's `/reviews` failed, and the **phone's opening briefing answered 500**: nothing of it showed, not the insights nor the approvals waiting. Reviews are now kept with their figures as numbers, and one kept before the fix is read back as numbers | AL16-en (the reviews job's own traceback, then 500 on `/api/mobile/v1/briefing`) | fixed (this PR) |
| 2 | P1 | The same books: a category running hot | The check for a category at 1.5× its usual multiplied a float by a Decimal, so in books with pence or baisa a hot category stopped the review being made at all. Found reading the code behind finding 1; reproduced in pytest (AL18) | AL18 (pytest) | fixed (this PR) |
| 3 | P1 | Can I afford it?, books with places | Half of what is spare each month (the installment test) and a dollar price times its rate were a Decimal times a float: `TypeError` for a person counting in Omani rials whenever the answer reached the installment plans, or the price was in another currency. Reproduced in pytest (AM11) | AM11 (pytest) | fixed (this PR) |
| 4 | P2 | اشخاص (Entities): renaming a bank | The chat's rename card renames a bank party and the account named for it together (AK7), but the Entities page renamed only the party: «تجارت 165» became «تجارت مرکزی 165» while its account stayed «حساب بانکی تجارت 165» on every card and report | AK7-web | fixed (this PR) |
| 5 | P3 | The web chat's briefing | The page remembers the day it gave the briefing in its own browser storage, so each new browser (the phone and the laptop, a cleared browser) asked again, and the same «Before we start…» stood three times in one conversation within a minute. The server now says it once a day in a conversation unless it changed, as the phone's briefing already did | I (thames_owner, ar, desktop) | fixed (this PR) |
| 6 | P2 | حسابرسی (Audit) → the audit trail's details | A voucher made through `POST /transactions` shows its details as raw JSON, with Persian escaped: `{"id": "8a7d9dab-…", "date": "2026-10-13", "reference": null, "description": "\u0647\u0632\u06…"}`. The vouchers the earlier groups imported show the same. Not new in this release; flagged now because this run's own vouchers were at the top | I (arman_owner, every size) | fixed (#386) |
| 7 | P3 | The review card | Its small words are under 11 px: the week and month labels under the bars (10.56 px), the badge «مرور هفتگی» (10.88 px), «معمول» (9.92 px). On the Persian week card «معمول» also sits over this week's bar. In English the columns cut their labels: «21 Sha…», «This w…» | I5 (AL17, arman_owner every size), AL16 | fixed (#386) |
| 8 | P3 | A company's review in English | «1 invoices overdue», and a Persian debtor's name inside the English sentence scrambles its order: «the oldest is 10 ,(فروشگاه مهرگان (شعبه ۲ days.» (the name wants its own direction) | AL16 (arman_cfo, en) | fixed (#386) |

Not app faults:
- **AL5's first try.** A person's home page on the web is the chat, which shows their reviews on arrival, and so marks them seen, two seconds before the check opened the bell. The phone's feed is now read before the web signs in, and the bell is checked on a company owner's dashboard.
- **Port 8899 on the host** is held by an SSH tunnel that isn't the QA server (`qa_up.sh`'s last health check printed a 404). The Playwright runs reach the server through its own network, so they weren't affected; probes from the host must go through `docker exec aa-qa`.
- **English in Persian or Arabic (I3).** «📄 mellat-…csv», «Widget …», «Gadget …»: the names the test gave its own file and items. On Thames' Arabic chat, the English and Spanish briefings are the history of the earlier English and Spanish passes, kept as they were said (finding 5 is why there were three).
