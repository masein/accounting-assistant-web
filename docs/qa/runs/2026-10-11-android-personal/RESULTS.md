# Personal books on the Android phone, 2026-10-11

**What is tested.** `main` after #334. A personal account (Iranian locale, rials, Persian) has three months of salary and food spending and 10 g of gold bought at an older price. It signs in on the emulator against the test server. Scenarios N36 (net worth) and N38 (the books' chip and calendar) are the base; the personal web is covered elsewhere.

## The checks

1. **Signing in:** the books' chip is saffron, the personal colour, from the first sign-in. The account sheet names the role.
2. **First run:** the questions offered fit personal books: no "who owes us?" for a person, unless a person's books can answer it.
3. **Spending:** «این ماه چقدر خرج کردم؟» ("how much did I spend this month?") answers from the books at once.
4. **Net worth:** «دارایی خالص من چقدره؟» ("what's my net worth?") gives the figure, twelve months as a line, and the gold at today's rate, with the dates in Jalali.
5. **The widget and the shortcuts:** they speak to a person (cash, the budget left, record spending).
6. **The conversations sheet and dates:** Jalali, Persian digits.

## Results

**Status: run in full on 2026-10-12** (the first attempt stalled on an overloaded host; see below). All six checks pass after the fixes:
1. **Signing in:** the books' chip «دفتر شخصی مریم» is saffron from the first sign-in, and the session says `personal` and `jalali`.
2. **First run:** the personal questions (finding 1, #335).
3. **Spending:** «خرج این ماه تا امروز: ۹۵٬۰۰۰٬۰۰۰ ریال.», with food as its one line.
4. **Net worth:** 1,034,000,000 rials (654,000,000 in the bank, plus 10 g of gold at today's 380,000,000, +80,000,000), the twelve-month line, and the holding, all in Jalali dates and Persian digits (after finding 3).
5. **The widget:** its summary for the personal account is the bank's 654,000,000 rials, with no budget line since none is set. Placing a widget through `adb` isn't practical; the widget's drawing is covered by its tests (#324).
6. **The conversations sheet:** «۲۰ مهر», «۳ پیام».

**The first attempt (2026-10-11 evening):** the Mac was under heavy load (Docker's helper and Spotlight), and the cold-booted emulator stopped responding: an ANR at start, and installs too slow to finish. The run waited for a calm host.

**Finding 1 (check 2, found reading the code before the run; fixed in this PR): a person was offered a company's questions.** The app had one list of first-run questions for every kind of books. Personal books were offered «کی بهم بدهکاره؟» ("who owes me?"), which the books answer with sales invoices ("0 sales invoices are still open"), and «به کی بدهکاریم؟» ("whom do we owe?"). Personal books now get their own list, in the first person, in all four languages: how much money I have, this month's spending, my budget, my net worth. The Persian business list says "we" throughout; it had mixed «بهم» (me), «بدهکاریم» (we) and «کردم» (I). Arabic's fast paths understand the first person («كم لدي من المال؟», «ميزانيتي»). `tests/test_mobile_first_run.py` reads the app's own lists and checks that every question, in every language and for both kinds of books, is answered at once from the books.

**Finding 2 (fixed in #337): the briefing's month and figures scrambled.** The opening briefing said «درآمد در 1405-06 100٪ کم شد — 0 در برابر میانگین 420,000,000» ("income fell 100% in 1405-06: 0 against an average of 420,000,000"), which the right-to-left bubble showed as «100 06-1405٪». Insights the chat says (the briefing, the phone's notifications) now name the month in the reader's words («شهریور ۱۴۰۵», "Shahrivar 1405"), and in Persian write the figures in Persian digits, like the chat's other sentences (`insight_service.chat_wording`). The web's pages keep their own figures. The insight itself was fair: the seed had no salary in Shahrivar.

**Finding 3 (fixed in #337): the net worth line invented wealth.** The twelve-month line said «+۶۵۴٬۰۰۰٬۰۰۰ در ۱۲ ماه» ("+654,000,000 over 12 months"), starting at 380,000,000 a year ago, when this person owned nothing. Today's 10 g of gold was counted in every month, before it was bought, and at yesterday's rate: the rate lookup takes a later rate when none is older. The trend (shared with the web's My finances) now counts a holding from its account's first posting, and a month gone by takes a rate dated on or before it, or its book value. Gold that was never posted (inherited) still counts throughout, where a rate of the time exists. The line now starts at 0 and reads «+۱٬۰۳۴٬۰۰۰٬۰۰۰ در ۱۲ ماه».
