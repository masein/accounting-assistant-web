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

All six checks pass in both languages after the fixes below. They run left to right in Spanish and right to left in Arabic, with:
- the app's words, dates and digits in the language: «23 sept – 13 oct · 42 filas», «٢ أكتوبر – ١٠ أكتوبر · ١٨ أسطر»;
- the first-run questions answered at once from the books;
- the statement card and its reply agreeing;
- the offline notice and the waiting line;
- the account sheet;
- the conversations sheet;
- the draft voucher with its Edit sheet.

**Seven findings, all fixed in this PR:**

1. **The accountant kept the old language.** With the app switched to Spanish after signing in, «¿Cuánto dinero tenemos?» was answered in Persian («موجودی نقد و بانک امروز: ۰ GBP»). The app told the server its language only at sign-in. The signed-in screen now does whenever the app's language differs from the account's, and the session remembers it (`ApiClient.setLanguage`).
2. **A statement sent again was mixed and said twice.** The reply read «Este archivo ya se importó. This file was already imported on 2026-10-11 as '…'. Ábrelo desde la tarjeta de abajo…», with three problems:
   - the importer's English note, with an ISO date;
   - "the card below", where the phone drew no card;
   - the same sentence twice, because the intake block carried the whole reply as its fallback.

   Now the note is in the reader's words with the books' calendar («Este archivo ya se importó el 11 oct 2026 como «mellat-octubre.csv»»), and the reply speaks of "its card". The phone draws the earlier import's card again, the intake's fallback is empty when the words follow, and the app leaves out a fallback that repeats them, which also fixes replies already stored. A statement that can't be read also says why in the reader's language.
3. **The card of a statement sent again didn't show.** It had the same block id as the first import's card, and the list keeps one item per id. It now has its own.
4. **A refused message was drawn twice.** The model was down, the turn failed, and the user's message was kept without the phone's id. Catching up after the file picker then drew it a second time beside the bubble offering «Reintentar». The message now gets the phone's id even when the turn fails.
5. **A file the server lost was pointed at, not sent.** An attachment's file was gone from disk, and the same file sent again was deduplicated to it, so the statement never reached the intake. A finished upload is now reused only while its file is still there. In this run this came from switching the test server's checkout, but a restore or a disk move does the same.
6. **The first-run questions stayed in the old language.** Switched from Spanish to Arabic, the intro was Arabic but the questions under it were still Spanish, because the chat's view model outlives the activity. They now follow the app's language, those on screen included.
7. **Arabic named the wrong button and kept the bank in Latin letters.** The summary said «اضغط «الإصلاح خطوة بخطوة»» under a button reading «إصلاح خطوة بخطوة» (the app's and the web's label), and named the bank «Mellat». It now matches the button, and gets «ملت» as Persian does: one script.

**An open question for the owner: digits in Arabic.** The phone writes Arabic-Indic digits for Arabic (since #316), while the web and most server sentences write Western ones. #331 made the statement summary Arabic-Indic, to match the card above it. So an Arabic screen can mix «£٠» with «0 GBP» («النقد والبنوك اليوم: 0 GBP.»). One style should be chosen for Arabic everywhere. *Answered the same day: Western digits everywhere (#336).*

**Seen and left as they are:**
- **Account names** are the company's own chart: the Default test company is a UK company on the Iranian chart, so its accounts read in Persian in any language, on the web too.
- **Spanish amounts** group with commas («661,300,000»), as on the web (scenario I12).
- **Messages written earlier** keep the language they were written in.

**Screens reviewed** (scratchpad): Spanish (`es-*`) and Arabic (`ar-*`) for each check, including the conversations and account sheets, the draft voucher, the Edit sheet, offline, and a statement sent again.
