# Persian glossary

One term per concept across the web app, the phone app and the server's messages. Agreed in the Persian copy review of 2026-10-31 (four passes over the Persian users read; see docs/qa/SCENARIOS.md group BD). tests/test_fa_glossary.py keeps the words this table rules out away from what users read. Add a row here before introducing a new term.

Decided from the seven reviewers' term lists. The basis, in order: established Iranian accounting software (حسابفا، سپیدار، هلو) and Iranian accounting standards; then the app's own nav labels where they are already right.

| Concept (English) | Use | Never / only |
|---|---|---|
| The AI assistant | **دستیار** | «حسابدار» (that is the human Accountant role). The phone app's name stays «حسابدار» for now (owner, 2026-10-31) |
| Addressing the user | polite **شما** forms everywhere in UI, buttons, notifications and the assistant's voice | «تو» forms («بنویس، بفرست، دفاترت»). Only the user's own suggested questions (chips the user taps) stay first-person colloquial |
| Invoice (sales) / bill (supplier) | **فاکتور فروش** / **فاکتور خرید** (short: فاکتور) | «صورتحساب» or «صورت‌حساب» for invoices; «قبض» except utility bills |
| To bill a client / billed | **صدور فاکتور** / **فاکتورشده** | «صورتحساب کنید» |
| Bank statement | **صورت‌حساب بانکی** (with نیم‌فاصله; short: صورت‌حساب) | — |
| Moadian e-invoice | **صورتحساب الکترونیکی** (its legal name, as the tax office writes it) | — |
| The Moadian system | **سامانه مؤدیان** (with ؤ; owner, 2026-10-31) | «مودیان» |
| Credit note | **برگ بستانکار** (kept for now; the owner means to change it later. «برگشت از فروش/خرید» stays where it already means returns) | «یادداشت بستانکار»، «اعلامیه…» |
| Recurring | **دوره‌ای** (e.g. پرداخت دوره‌ای، فاکتور دوره‌ای); the feature title may say «تکرارشونده» | «تکراری» (that means *duplicate*, as in duplicate rows) |
| Rule | **قاعده** | «قانون» (law) |
| Chart of accounts | **کدینگ حساب‌ها** | «نمودار حساب‌ها»، «جدول حساب‌ها»، «سرفصل حساب‌ها» (a «سرفصل» is one heading account; fine in that sense) |
| Trial balance | **تراز آزمایشی** | «مرور حساب» |
| Balance sheet | **ترازنامه** (formal statement title may stay «صورت وضعیت مالی» where it is the UK/IFRS statement) | — |
| Cash flow statement | **صورت جریان‌های نقدی** (title of the revised Iranian standard No. 2, already on the app's statements) | «صورت جریان وجوه نقد» (the old title), «جریان نقد» as the statement name |
| Financial statements | **صورت‌های مالی** | bare «صورت‌ها» |
| Equity | **حقوق مالکانه** | «حقوق صاحبان سهام» (mixed) |
| Net worth | **دارایی خالص** (owner, 2026-10-31) | «ارزش خالص»، «خالص دارایی». A person's chart says it too: 31 «دارایی خالص», 3110 «دارایی خالص اولیه» (migration 086). «ارزش خالص» stays for stock's net value |
| Opening voucher / balances | **سند افتتاحیه** (the voucher), **مانده‌های افتتاحیه** (the balances) | «تراز افتتاحیه» as a voucher description |
| Aging (report) | **تحلیل سنی …** (column header «سن بدهی» is fine) | bare «سنی …» |
| Bank reconciliation | **مغایرت‌گیری بانکی**; a difference is **مغایرت** | «فرق دارد» for a reconciliation difference |
| Matching one row / approving a match | **تطبیق** | — |
| Mapping accounts on import | **معادل‌سازی** | «نگاشت» |
| Migration (from old software) | **انتقال اطلاعات** (nav: «انتقال اطلاعات») | «مهاجرت» |
| Entities / parties | **اشخاص** (section, lists), **طرف حساب** (the party on a document; plural «طرف حساب‌ها») | «موجودیت»، «طرف مقابل» (except TTMS «طرف معامله», official) |
| Supplier | **تأمین‌کننده** | «فروشنده» for a supplier |
| Product / item | **کالا** | «محصول»، «قلم» |
| Stock count | **انبارگردانی** | «شمارش موجودی انبار» |
| Voucher line | **ردیف** | «سطر» |
| Undo (just now) | **واگرد** | — |
| Reverse a posted entry | **برگرداندن سند**; the entry made is **سند معکوس** | «برگشت خورده» for vouchers (that verb is for bounced cheques only) |
| Cheque: endorse to a third party | **خرج چک** / status **خرج‌شده** | «واگذار/واگذارشده» for endorsing |
| Cheque: deposit for collection | **واگذاری به بانک** / status **در جریان وصول** | «به بانک سپردن» |
| Cheque: honoured / bounced | **پاس شدن** / **برگشت خوردن** | «پرداخت شدن» for a cheque clearing |
| Sayad id | **شناسهٔ صیادی** | «شناسه صیاد» |
| National id | **کد ملی** (person, 10 digits), **شناسهٔ ملی** (company, 11 digits) | — |
| Petty cash | fund **تنخواه‌گردان**, holder **تنخواه‌دار** | holder as «تنخواه‌گردان» |
| Pay run | **لیست حقوق** (the run); action **محاسبهٔ حقوق** | «اجرای حقوق»، «اجرا» |
| Pay profile | **مشخصات حقوقی** | «پروفایل پرداخت/حقوق» |
| Statutory (payroll) | **قانونی** / **طبق قانون** | «رسمی» |
| Prorated pay | **کارکرد ناقص** | «تسهیم» |
| Accrual voucher | **ثبت تعهدی** | «هزینهٔ تعهدی» for income accruals |
| Audit trail / log | **سابقهٔ رویدادها** | «ردگیری»، «لاگ ممیزی»، «حسابرسی‌شده» for "logged" |
| Approvals queue | **کارتابل تأیید** | «صف تأییدها» |
| Runway / burn rate | **دوام نقدینگی** / **نرخ مصرف نقدینگی** (short label «نرخ مصرف») | «بقای مالی»، «نرخ سوخت» |
| Locale | **قالب کشور** | «محل»، «منطقه»، «محلی» |
| Reporting standard setting | **استاندارد گزارشگری مالی** | «محل گزارش‌گیری» |
| Demo data | **دادهٔ نمایشی** | «دادهٔ آزمایشی» (clashes with تراز آزمایشی) |
| Over budget | **فراتر از بودجه** / «بیشتر از بودجه خرج شد» | «بودجه رد شد»، «از بودجه جلو زدیم» |
| Cushion (savings) | **ذخیرهٔ احتیاطی** | «پشتوانه» |
| Refund | **استرداد** (invoices); «بازپرداخت» only for expense claims and loans | — |
| Customer credit on an invoice | **بستانکاری** | «اعتبار» |
| Chat (feature) / a conversation | **چت** / **گفتگو** | «گفت‌وگو» (pick one: گفتگو) |
| Load/upload, download | **بارگذاری**, **دانلود** | «بارگیری» (reads as download), «آپلود» |
| Share / send | **اشتراک‌گذاری** / **ارسال** | «ارسال» for Share; «فرستادن» on buttons |
| Link | **لینک** | «پیوند» |
| Password; reset | **رمز عبور**; **بازنشانی** | «گذرواژه»، bare «رمز» in labels |
| Merge | **ادغام** | «یکی کردن» |
| Parse / read a file | **خواندن** | «تجزیه» |
| On/off settings | **فعال / غیرفعال** (exception: a page whose own switch says روشن/خاموش) | — |
| My finances | **امور مالی من** | «پول من» |
| Mark-as buttons | **ثبت ارسال / ثبت پذیرش / ثبت تأیید / تأیید** (an action) | «ارسال شد / تأیید شد» on a *button* (that is a status) |
| Cancel | **انصراف** to close a dialog or drop a card; **لغو** to cancel a record or action | — |
| Card buttons in Persian text | quote the Persian labels «تأیید» / «انصراف» | "Confirm"/"Cancel" in English |
| Notifications | **اعلان‌ها**; «هشدار» only for real warnings | — |
| "First" | **ابتدا** in UI text | — |
| Left out of totals | **لحاظ نمی‌شوند** | «حساب نمی‌شوند» |
| Link/code validity | **معتبر است** | «کار می‌کند» |

## Writing conventions
- Figures in Persian text: Persian digits, «,» between thousands and «٫» for the decimal point («۱,۲۵۰,۰۰۰», «۷٫۵۰»; the owner's choice, 2026-10-31: «٬» drew as an apostrophe on the phone), «٪» for percent. Account codes in Persian digits too («۱۱۱۰»). Document numbers and ids («INV-12»), formats («YYYY-MM-DD») and keyboard keys keep their Latin digits.
- «،» «؛» «؟» «» in Persian text. In right-to-left text an arrow between two places is «←», not «→».
- نیم‌فاصله as already used. Leave the ezafe «ـهٔ» and «همه/همهٔ» as they are (no mass change: low value, high churn).
- Keep every placeholder, HTML tag and escape exactly.
