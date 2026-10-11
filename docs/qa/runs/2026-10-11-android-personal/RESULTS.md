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

**Status: partly run.** The personal account was provisioned and seeded on the test server: three months of salary and food, 10 g of gold bought for 300,000,000 and worth 380,000,000 at today's rate. The live checks then stalled: the Mac was under heavy load (Docker's helper and Spotlight), and the freshly cold-booted emulator went unresponsive (an ANR at start, the package manager too slow to install). Checks 1 and 3–6 wait for the next run; scenarios N36 and N38 already cover the net worth answer and the books' chip in tests.

**Finding 1 (check 2, found reading the code before the run; fixed in this PR): a person was offered a company's questions.** The app had one list of first-run questions for every kind of books. Personal books were offered «کی بهم بدهکاره؟» ("who owes me?"), which the books answer with sales invoices ("0 sales invoices are still open"), and «به کی بدهکاریم؟» ("whom do we owe?"). Personal books now get their own list, in the first person, in all four languages: how much money I have, this month's spending, my budget, my net worth. The Persian business list says "we" throughout; it had mixed «بهم» (me), «بدهکاریم» (we) and «کردم» (I). Arabic's fast paths understand the first person («كم لدي من المال؟», «ميزانيتي»). `tests/test_mobile_first_run.py` reads the app's own lists and checks that every question, in every language and for both kinds of books, is answered at once from the books.
