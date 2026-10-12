# The accountant on migrated books, AI on, 2026-10-12

**What is tested.** `main` after #348 (pence P4), then this branch. Section R of `SCENARIOS.md` against a real model (Metis, `gpt-4.1-mini`, the production model) on two real companies migrated in O and P. Their books stay outside the repo; this file names them by shape only.
- **A Sepidar company:** three years of vouchers (1403–1405, the last year provisional), two banks, 24 parties, a 198-account chart beside the seed chart.
- **Two sister schools:** hand-kept Excel journals, codes that follow no standard, no parties, petty cash and loans as chart accounts.

There were 52 scenarios across the three companies. Each figure was checked against the page that shows it (income statement, balance sheet, debtors and creditors, trial balance, ledger). Each confirmed card was read back from the ledger, then undone.

## Results

The first pass ran on the wrong model (finding 1). On `gpt-4.1-mini`, the scenarios scored automatically went:
- **36%** before the fixes;
- **60%** with the first half of them;
- **82%** with all of them.

**What now holds for every model:**
- Balances, cash, a year or a month, debtors and creditors come from the tools and are copied, not retyped.
- A miscopied figure is corrected before it is shown.
- Cards post to the company's own accounts.
- A corrected card withdraws the old one.
- Tomans are converted, and a refused card is never reported as ready.

**What stays with the model:** a weaker one asks before drafting a card it has everything for, picks among similar accounts less well, and retypes a Persian name now and then. Which model is a cost decision; see "Which model".

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
14. **Figures were miscopied even from the ready-written twins.**
    - 21,721,200,283 came out as «۲۱٬۷۱۲٬۱۰۰٬۲۸۳», and 1,017,516,253 as «۱٬۰۱۷٬۵۱۷٬۲۵۳».
    - Before a reply goes out, a long grouped figure no tool returned, a few digits off (or reordered) from exactly one figure a tool did, is replaced by that figure. A sum the model worked out is left alone.
15. **A refused card was reported as ready.** The toman guard refused a 50-million-toman invoice carded at 50,000,000 rials; the model then said the invoice was ready. A refusal that names its own fix now goes back to the model once, and the false reply is never shown. A closed period or a future date is final and is not retried.
16. **A party's id was passed as its name** to `get_party_balances`, which found nothing. It now takes either.
17. **Tomans carded as rials.** «قسط وام ۲۰ میلیون تومان» was carded at 20,000,000 rials. A rial book's voucher or invoice whose amount is a figure the user wrote in tomans is now refused, with the rial amount to use.
18. **Choosing gpt-5.4 or 5.5 broke the chat.**
    - gpt-5.4-mini, gpt-5.4-nano and gpt-5.5 refuse function tools unless `reasoning_effort` is "none", and the app sent "low".
    - gpt-5, gpt-5-mini and gpt-5-nano refuse "none".
    - The rule now follows the version: "none" from 5.4 on, "low" before it.
19. **Choosing Claude Haiku 4.5 broke the chat.** It refuses adaptive thinking. A request is now sent again without the option the provider names (thinking or effort).
20. **A seed default the books never used.** Even gpt-4.1 credited the seed's 2120 «پیش‌دریافت از مشتریان» where the company keeps customer advances on its own account. A card on a seed default the books never used, when the company keeps that posting role on its own used account, is refused with the account to use, and the model gets one retry.
21. **"Whom do we owe" didn't net debit balances.** It totalled only the liability accounts that owe (23,672,063,635); the balance sheet nets one in debit (23,495,300,037). It now totals them as the balance sheet does.
22. **An empty provider account read as "try again in a moment".** The chat now says the AI provider's credit has run out and the owner can top it up, in all four languages. The phone reads the same code as before.
23. **Smaller fixes:**
    - an invoice card dated 2026-07-01 for «پشتیبانی مهر»: the prompt now says to leave `issue_date` out unless the user names a date;
    - the AI budget message read "You have used its AI allowance";
    - a party named where an account was expected now says where its balance is;
    - petty cash, loans and shared costs are found in a migrated chart (the prompt).

## Which model

All on the same final code, each on its own copy of the books, the 52 scenarios scored automatically:
- a figure must equal the report's;
- a card must use the right accounts and amount;
- a refused card must not exist.

Cost uses list prices (USD per million tokens) where they are known here; the others need Metis's price list. gpt-5.6-luna uses Metis's price, which the owner sent on 2026-10-12: $0.22 input, $0.022 cached input and $1.32 output per million tokens, up to a 270K context. Billing the uncached input at its $0.275 cache-write rate instead adds under $0.10.

| Model | Passed | Median turn | Tokens in / out per question | $ per 1,000 questions |
|---|---|---|---|---|
| claude-sonnet-5-5 | 51/52 (98%) | 12.1 s | 84,077 / 676 | ≈ 44 at Sonnet 4.x prices |
| gpt-4.1 | 50/52 (96%) | 9.0 s | 47,646 / 167 | 27.67 |
| gpt-5.6-luna | 47/52 (90%) | 11.5 s | 53,200 / 189 | 1.64 at Metis's price |
| gpt-4.1-mini (today's) | 43/52 (82%) | 8.2 s | 49,350 / 145 | 5.75 |
| gpt-5.4-mini | 43/52 (82%) | 10.1 s | 59,259 / 164 | not known here |
| gpt-5-mini | 42/52 (80%) | 14.1 s | 57,675 / 719 | 3.43 |
| gpt-5.4-nano | 40/52 (76%) | 8.8 s | 59,928 / 205 | not known here |
| claude-haiku-4-5 | 38/52 (73%) | 18.5 s | 62,220 / 427 | 11.46 |
| gpt-4.1-nano | 19/52 (36%) | 6.2 s | 42,474 / 87 | 1.29 |

About 95% of the input tokens are cached prompt (the system prompt and tool list), billed at the cached rate. claude-haiku-5-5 is listed by Metis but not enabled for this key. Gemini isn't on Metis's OpenAI-compatible path, so it would need its own chat adapter.

## Costs and limits

- **Per question:** gpt-4.1-mini sends about 20,000 input tokens per model call, mostly cached; a question takes 1–4 calls.
- **The daily allowance:** the default of 2,000,000 tokens per user is about 25–50 questions a day on these books.
- **On Opus:** the same questions cost about 38,000 tokens a call (finding 1).

## Recommendation

- **gpt-4.1-mini (today) passes 82% on these books.** Its misses are the hard ones: drafting a card it has everything for, choosing among similar accounts, retyping a name.
- **Switch to gpt-5.6-luna.** It passes 90%, eight points above today's model, for $1.64 per 1,000 questions, about 3.5× cheaper than gpt-4.1-mini's $5.75. It is a little slower (11.5 s median, against 8.2 s).
- **gpt-4.1 (96%) and claude-sonnet-5-5 (98%) are the most accurate,** at roughly 5–8× today's cost.
- **gpt-5.6-luna is the only model that is both cheaper and more accurate than today's.** gpt-5-mini is cheaper but slower and no better; the nanos and Haiku are weaker.

Switch in Settings → AI providers (model) and Settings → chat provider shape, one choice for every company after finding 1.

A last confirmation run on the final code could not run: the benchmark used up the Metis credit (402, «Insufficient credit»). The table above is the full run on the code before findings 20 and 21. Those two are deterministic: they help every model alike and change no comparison. Finding 22 makes that situation plain to users.

