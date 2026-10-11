# The Android app in Spanish and Arabic, 2026-10-11

**What is tested.** `main` after #330–#333:
- uploads in pieces (#330);
- the statement summary in the reader's digits (#331);
- dates in the books' calendar (#332);
- conversations opened again (#333).

The app was set to Spanish, then Arabic, with Android's per-app language. It ran on the emulator against the test server: the Default company (Gregorian, GBP) and the e2e owner. Scenario N24 ("the app in Arabic and Spanish") is the base. Every screen is checked for words left in another language, digits, dates, and layout (Arabic right to left).

## The checks, in each language

1. **First run:** a new conversation shows the first-run questions in the language. Tapping the cash question answers from the books at once, with the figure in the language's digits.
2. **A statement:** a CSV from Downloads, through the file picker. Its card and the reply carry dates in the language's months, counts in its digits and the bank's name.
3. **Uploading and offline:** the percentage while a file goes, then airplane mode. The "no connection" notice and the waiting line under the message are in the language.
4. **The account sheet:** the role, the app lock, notifications, crash reports and the phones, in the language.
5. **The conversations sheet:** each conversation's date in the language's months.
6. **A draft voucher** (seeded, since the test server has no AI): the card, its lines and the Edit sheet, in the language and its digits.

## Results

(filled in as the checks run)
