# The accountant on migrated books, AI on, 2026-10-12

**What is tested.** `main` after #348 (pence P4), then this branch. Section R of `SCENARIOS.md` against a real model (Metis, `gpt-4.1-mini`, the production model) on two real companies migrated in O and P. Their books stay outside the repo; this file names them by shape only.
- **A Sepidar company:** three years of vouchers (1403–1405, the last year provisional), two banks, 24 parties, a 198-account chart beside the seed chart.
- **Two sister schools:** hand-kept Excel journals, codes that follow no standard, no parties, petty cash and loans as chart accounts.

There were 52 scenarios across the three companies. Each figure was checked against the page that shows it (income statement, balance sheet, debtors and creditors, trial balance, ledger). Each confirmed card was read back from the ledger, then undone.

## Results

The first pass ran on the wrong model (below). Three passes ran on `gpt-4.1-mini`: before the fixes, with most of them, and with all of them.

**The read answers:**
- Balances, cash, a year's revenue and expenses, a named month and its follow-up, month against month, debtors and creditors are now exact.
- The model still garbles a Persian account name now and then when it retypes one («زیراسخت» for «زیرساخت»). The figures come from the tools and are copied, not retyped.

**The cards:**
- Stationery, a school's supplies and a loan instalment post to the company's own accounts.
- A corrected card withdraws the old one.

**What stays with the model:** gpt-4.1-mini sometimes asks before drafting a card it has everything for (a transfer between two banks, a salary). A stronger model does this less often; see the recommendation at the end.

## Findings, fixed on this branch

1. **Every company but the platform's own chatted through another model.** The chat-provider choice («openai» to use the Metis model in Settings) was saved under the super-admin's own company. Every other company auto-detected, and chatted through the Anthropic model whenever its key was set, whatever Settings showed. The choice is now platform-wide, like the AI config. A choice saved the old way still holds.
2. **The chat's cash wasn't the dashboard's.** «کل موجودی نقد و بانک» counted 1110 and the banks only, leaving out petty cash (111003), a figure no page shows. The tool now uses the company cash predicate (#340).
3. **"Who owes us?" read invoices only.** A migrated company has balances and no invoices, so it was told nobody owed anything while its debtors report listed six. There is now `get_party_balances`, the debtors and creditors report by party. The quick answer gives open invoices, then the books' balances when they show more. «به کی بدهکاریم؟» with no creditor parties now names the balance sheet's liabilities (a school's loan and its sister school).
4. **A named bank got all the cash.** «موجودی بانک ملت» took the total-cash quick answer. Now it gives that bank's own balance, and goes to the model when the name matches no bank or several.
5. **The seed chart won over the company's own.** The alias table sent «اجاره», «ملزومات» and "stationery" to the seed's never-used 6112, «حقوق» to 6110 and «فروش» to 4110. Two changes fix it:
   - accounts the books use rank first, and an alias points to the company's posting-role account;
   - an account named for what was asked beats one an alias points to.

   Synonym groups also connect «لوازم التحریر» with «نوشت افزار», «بانک» with "bank", and similar pairs.
6. **A corrected card left the old one confirmable.** «مبلغش رو ۲.۵ میلیون کن» drew a new card; confirming both posted twice. Money cards now take `replaces` ("previous" or a token). The orchestrator withdraws that card if it is the same user's, in the same thread, and still pending. The web marks it changed, and the phone draws it as replaced.
7. **Jalali dates read as AD.** The model sent «امسال» as 1405-01-01..1405-07-20; the statement read the year 1405 AD and reported zero. A date field in a 13xx or 14xx year is now converted before any tool sees it.
8. **Months and years were guessed.**
   - The model computed Tir as 06-22..07-21, a day short, so Tir's salaries landed in Mordad.
   - It answered "the month before Khordad" with Dey 1405.
   - It answered «۱۳۹۹» with 1404's figures, labelled «۱۳۹۹ (۱۴۰۴)».

   The spending summary and the financial statement now take `month` («1405-05») and `year` («1404»), in the company's calendar. "This year" defaults to 1 Farvardin in a Jalali company, not 1 January.
9. **Dates and amounts were retyped wrong.**
   - The model wrote «۷ مرداد» for 2026-07-28, which is ۶ مرداد.
   - It wrote «۲۱٬۷۱۲٬۱۰۰٬۲۸۳» for 21,721,200,283.
   - It restated 19,681,882,453 as «۱۹٬۶۸۱ میلیارد», a thousand times too much.

   Tool results now carry `…_jalali` words for every date (Jalali company) and `…_fa` digits for every amount (Persian reader), and the prompt has the model copy them.
10. **Parties weren't found.**
    - «بانک آینده» ranked «بانک دی فرمانیه» first, on the shared «بانک».
    - «علیرضا حیدری» matched «حیدری علیرضا» at 0.5.
    - «ابر اروان», asked for as a supplier, was "not found" because it is a client.

    Generic words («بانک», «شرکت», «آقای», "Ltd") no longer count, and the words match in any order. Another type is offered when none of the asked type matches.
11. **Persian digits in a code found nothing:** «۱۱۱۰۰۵۰۰۱». Codes, queries and references are now normalised to ASCII digits.
12. **"How many months of money?" read the 13 weeks** ("enough for 3 months") beside a 0.4-month runway. The forecast tool now returns the dashboard's runway and burn.
13. **An English question got a Persian answer.** The reader's saved language won over the message's. A message plainly in the other script is now answered in that script's language.
14. **Smaller fixes:**
    - an invoice card dated 2026-07-01 for «پشتیبانی مهر»: the prompt now says to leave `issue_date` out unless the user names a date;
    - the AI budget message read "You have used its AI allowance";
    - a party named where an account was expected now says where its balance is;
    - petty cash, loans and shared costs are found in a migrated chart (the prompt).

## Costs and limits

- **Per question:** gpt-4.1-mini sends about 20,000 input tokens per model call, mostly cached; a question takes 1–4 calls.
- **The daily allowance:** the default of 2,000,000 tokens per user is about 25–50 questions a day on these books.
- **On Opus:** the same questions cost about 38,000 tokens a call (finding 1).

## Recommendation

`gpt-4.1-mini` is fast (6–10 s) and cheap, but on real books it often asks before acting and occasionally retypes names. The tools now carry the exact figures and dates, and a stronger chat model (`gpt-4.1`) would close most of what remains. It can be switched in Settings → AI providers, one choice for every company after finding 1.
