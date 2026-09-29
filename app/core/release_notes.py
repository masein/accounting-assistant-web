"""What's-new registry: one entry per release, shown as a short tour to each
user the first time they log in after the update.

How it works
------------
* ``CURRENT_RELEASE`` is bumped with every deploy that ships user-visible
  change, and a ``Release`` describing the highlights is appended to
  ``RELEASES`` (newest last). Versions are dates, ``YYYY.MM.DD``, optionally
  with a ``.N`` suffix for a second release on the same day.
* ``users.last_seen_release`` remembers what each user has already been
  shown. ``NULL`` means "existing user from before this feature" → they see
  the current release once. A brand-new user is created with the current
  release so their first login isn't a changelog.
* ``whats_new_for(role, last_seen)`` returns the releases newer than what
  the user saw, with highlights filtered to the pages that role can open.

Copy lives here (not in the JS packs) so a release note can never be out of
step with the code that shipped it; the four UI languages are mandatory and
tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LANGUAGES = ("en", "fa", "es", "ar")

CURRENT_RELEASE = "2026.09.29.1"


@dataclass(frozen=True)
class Highlight:
    key: str
    title: dict[str, str]
    body: dict[str, str]
    page: str | None = None                          # SPA page "Show me" opens
    page_by_role: dict[str, str] = field(default_factory=dict)
    roles: tuple[str, ...] | None = None              # None = everyone
    locales: tuple[str, ...] | None = None            # None = every company locale

    def for_role(self, role: str | None, locale: str | None = None) -> dict[str, Any] | None:
        r = (role or "owner").lower()
        if self.roles is not None and r not in self.roles:
            return None
        if self.locales is not None and locale is not None and locale.lower() not in self.locales:
            return None
        return {
            "key": self.key,
            "title": dict(self.title),
            "body": dict(self.body),
            "page": self.page_by_role.get(r, self.page),
        }


@dataclass(frozen=True)
class Release:
    version: str
    date: str
    highlights: tuple[Highlight, ...]


_BOOKS = ("owner", "cfo", "accountant", "personal")
_SME_BOOKS = ("owner", "cfo", "accountant")

RELEASES: tuple[Release, ...] = (
    Release(
        version="2026.09.22",
        date="2026-09-22",
        highlights=(
            Highlight(
                key="chat-statement",
                page="ai-accountant",
                roles=_BOOKS,
                title={
                    "en": "Drop a bank statement into the chat",
                    "fa": "صورتحساب بانکی را در گفتگو رها کنید",
                    "es": "Suelta un extracto bancario en el chat",
                    "ar": "أسقط كشف الحساب البنكي في المحادثة",
                },
                body={
                    "en": "Attach the bank's PDF, image, CSV or Excel. The assistant reads every row, checks it against your books and takes you through the differences one at a time — nothing is posted without your Confirm.",
                    "fa": "PDF، عکس، CSV یا اکسل بانک را پیوست کنید. دستیار همهٔ ردیف‌ها را می‌خواند، با دفاتر مقایسه می‌کند و اختلاف‌ها را یکی‌یکی با شما جلو می‌برد — بدون تأیید شما چیزی ثبت نمی‌شود.",
                    "es": "Adjunta el PDF, imagen, CSV o Excel del banco. El asistente lee cada fila, la compara con tus libros y te guía por las diferencias una a una; nada se registra sin tu confirmación.",
                    "ar": "أرفق ملف البنك بصيغة PDF أو صورة أو CSV أو Excel. يقرأ المساعد كل صف ويقارنه بدفاترك ويأخذك عبر الفروق واحدًا تلو الآخر — لا يُسجَّل شيء دون تأكيدك.",
                },
            ),
            Highlight(
                key="statement-review",
                page="bank-statements",
                roles=_BOOKS,
                title={
                    "en": "“Check against books” on every statement",
                    "fa": "«بررسی با دفاتر» روی هر صورتحساب",
                    "es": "«Comparar con los libros» en cada extracto",
                    "ar": "«مقارنة مع الدفاتر» على كل كشف",
                },
                body={
                    "en": "One button lists what the bank shows and the books don't, entries the bank never saw, amount mismatches, duplicates and the closing-balance gap — each with a one-click fix.",
                    "fa": "یک دکمه نشان می‌دهد چه چیزی در بانک هست و در دفاتر نیست، چه سندی را بانک ندیده، کجا مبلغ فرق دارد، تکراری‌ها و اختلاف مانده پایانی — هرکدام با یک کلیک قابل رفع.",
                    "es": "Un botón enumera lo que muestra el banco y no los libros, asientos que el banco no vio, importes distintos, duplicados y la diferencia de saldo final, cada uno con su arreglo en un clic.",
                    "ar": "زر واحد يعرض ما يظهره البنك ولا تظهره الدفاتر، والقيود التي لم يرها البنك، واختلافات المبالغ، والتكرارات، وفرق الرصيد الختامي — مع إصلاح بنقرة واحدة لكل منها.",
                },
            ),
            Highlight(
                key="entity-statement",
                page="entities",
                roles=_SME_BOOKS,
                title={
                    "en": "Entity transactions read like a statement of account",
                    "fa": "تراکنش‌های طرف حساب مثل صورتحساب",
                    "es": "Las transacciones por entidad se leen como un estado de cuenta",
                    "ar": "معاملات الطرف تُقرأ ككشف حساب",
                },
                body={
                    "en": "“View transactions” now shows Debtor, Creditor and Remaining per row, and an “Add transaction” button records a new entry with the entity already linked.",
                    "fa": "«مشاهده تراکنش‌ها» حالا ستون‌های بدهکار، بستانکار و مانده دارد و دکمهٔ «ثبت تراکنش» سند جدید را با همان طرف حساب پیوندشده ثبت می‌کند.",
                    "es": "«Ver transacciones» muestra ahora Debe, Haber y Saldo por fila, y «Añadir transacción» registra un asiento con la entidad ya vinculada.",
                    "ar": "«عرض المعاملات» يعرض الآن مدين ودائن والرصيد لكل صف، وزر «إضافة معاملة» يسجل قيدًا جديدًا مع ربط الطرف مسبقًا.",
                },
            ),
            Highlight(
                key="undo-removes",
                page="ai-accountant",
                roles=_BOOKS,
                title={
                    "en": "Undo now removes the entry",
                    "fa": "واگرد حالا سند را حذف می‌کند",
                    "es": "Deshacer ahora elimina el asiento",
                    "ar": "التراجع يحذف القيد الآن",
                },
                body={
                    "en": "Confirm, then Undo within two minutes: the entry disappears from the books instead of leaving a reversal pair. “Reverse entry” after that still posts a compensating entry.",
                    "fa": "تأیید و سپس واگرد تا دو دقیقه: سند از دفاتر حذف می‌شود، نه اینکه سند برگشتی کنارش بماند. «برگرداندن سند» بعد از آن همچنان سند معکوس ثبت می‌کند.",
                    "es": "Confirma y deshaz en dos minutos: el asiento desaparece de los libros en vez de dejar un par de reversión. «Revertir asiento» después sigue registrando un asiento compensatorio.",
                    "ar": "أكِّد ثم تراجع خلال دقيقتين: يختفي القيد من الدفاتر بدلًا من ترك قيد عكسي. «عكس القيد» بعد ذلك ما زال يسجل قيدًا تعويضيًا.",
                },
            ),
            Highlight(
                key="insights",
                page="dashboard",
                page_by_role={"personal": "personal-dashboard"},
                roles=_BOOKS,
                title={
                    "en": "The assistant tells you what changed",
                    "fa": "دستیار می‌گوید چه چیزی تغییر کرده",
                    "es": "El asistente te dice qué ha cambiado",
                    "ar": "المساعد يخبرك بما تغيّر",
                },
                body={
                    "en": "A new “What changed” panel, the bell and a short briefing when you open the chat point out payroll moves, new employees, expense spikes, unusual payments, a statement that is due for upload — before you ask.",
                    "fa": "پنل جدید «چه چیزی تغییر کرده»، زنگ اعلان‌ها و یک خلاصهٔ کوتاه در ابتدای گفتگو، تغییر حقوق و دستمزد، کارمند جدید، جهش هزینه، پرداخت غیرمعمول و موعد بارگذاری صورتحساب را پیش از اینکه بپرسید نشان می‌دهند.",
                    "es": "Un nuevo panel «Qué ha cambiado», la campana y un breve resumen al abrir el chat señalan cambios de nómina, nuevos empleados, picos de gasto, pagos inusuales o un extracto pendiente, antes de que preguntes.",
                    "ar": "لوحة «ما الذي تغيّر» الجديدة والجرس وملخص قصير عند فتح المحادثة تشير إلى تغيّرات الرواتب والموظفين الجدد وقفزات المصروفات والدفعات غير المعتادة وكشف حساب مستحق الرفع — قبل أن تسأل.",
                },
            ),
            Highlight(
                key="whats-new",
                page="settings",
                title={
                    "en": "This tour, after every update",
                    "fa": "همین راهنما، بعد از هر به‌روزرسانی",
                    "es": "Este recorrido, tras cada actualización",
                    "ar": "هذه الجولة بعد كل تحديث",
                },
                body={
                    "en": "The first time you log in after an update you'll get a short walkthrough of what's new. You can reopen it any time from Settings.",
                    "fa": "اولین بار که پس از هر به‌روزرسانی وارد می‌شوید، خلاصهٔ کوتاهی از تغییرات می‌بینید. از تنظیمات هر زمان می‌توانید دوباره آن را باز کنید.",
                    "es": "La primera vez que inicies sesión tras una actualización verás un breve recorrido por las novedades. Puedes reabrirlo cuando quieras desde Ajustes.",
                    "ar": "في أول تسجيل دخول بعد أي تحديث ستحصل على جولة قصيرة بالجديد. يمكنك إعادة فتحها في أي وقت من الإعدادات.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.24",
        date="2026-09-24",
        highlights=(
            Highlight(
                key="per-currency-views",
                page="ledger",
                roles=_BOOKS,
                title={
                    "en": "Reports show one currency at a time",
                    "fa": "گزارش‌ها هر بار یک ارز را نشان می‌دهند",
                    "es": "Los informes muestran una moneda a la vez",
                    "ar": "التقارير تعرض عملة واحدة في كل مرة",
                },
                body={
                    "en": "The ledger, trial balance and dashboard show your reporting currency and list any other currency in the books as its own view. Amounts in different currencies are never added together.",
                    "fa": "دفتر کل، تراز آزمایشی و داشبورد ارز گزارشگری شما را نشان می‌دهند و هر ارز دیگری در دفاتر را به‌صورت نمای جداگانه فهرست می‌کنند. مبالغ ارزهای مختلف هرگز با هم جمع نمی‌شوند.",
                    "es": "El libro mayor, el balance de comprobación y el panel muestran tu moneda de informe y listan cualquier otra moneda de los libros como una vista propia. Los importes en distintas monedas nunca se suman.",
                    "ar": "يعرض دفتر الأستاذ وميزان المراجعة ولوحة المعلومات عملة التقارير الخاصة بك ويدرجون أي عملة أخرى في الدفاتر كعرض مستقل. لا تُجمع المبالغ بعملات مختلفة أبداً.",
                },
            ),
            Highlight(
                key="balance-sheet-check",
                page="manager",
                roles=_SME_BOOKS,
                title={
                    "en": "A balance sheet that balances",
                    "fa": "ترازنامه‌ای که تراز است",
                    "es": "Un balance que cuadra",
                    "ar": "ميزانية متوازنة",
                },
                body={
                    "en": "Balances keep their sign (an overdrawn cash account shows as negative), the period's unclosed profit appears under equity, and the statement shows assets against liabilities + equity with a balanced / not balanced check.",
                    "fa": "مانده‌ها علامت خود را نگه می‌دارند (حساب نقد منفی، منفی نشان داده می‌شود)، سود بسته‌نشدهٔ دوره زیر حقوق مالکانه می‌آید و صورت، دارایی‌ها را در برابر بدهی‌ها + حقوق مالکانه با نشانگر تراز / ناتراز نشان می‌دهد.",
                    "es": "Los saldos conservan su signo (una caja en descubierto aparece en negativo), el beneficio no cerrado del periodo aparece en el patrimonio y el estado muestra el activo frente al pasivo + patrimonio con un indicador de cuadra / no cuadra.",
                    "ar": "تحافظ الأرصدة على إشارتها (حساب النقد المكشوف يظهر بالسالب)، ويظهر ربح الفترة غير المُقفل ضمن حقوق الملكية، وتعرض القائمة الأصول مقابل الالتزامات + حقوق الملكية مع مؤشر متوازنة / غير متوازنة.",
                },
            ),
            Highlight(
                key="chat-periods-cash",
                page="ai-accountant",
                roles=_BOOKS,
                title={
                    "en": "Ask “how much did I spend this month?”",
                    "fa": "بپرسید «این ماه چقدر خرج کردم؟»",
                    "es": "Pregunta «¿cuánto gasté este mes?»",
                    "ar": "اسأل «كم أنفقت هذا الشهر؟»",
                },
                body={
                    "en": "The assistant now works out “this month”, “last month” and “this year” in your own calendar (Jalali for Iranian books), answers “how much cash do we have?” from every cash and bank account, and books a spend with no matching category to general expenses instead of asking which account.",
                    "fa": "دستیار اکنون «این ماه»، «ماه گذشته» و «امسال» را در تقویم خود شما (شمسی برای دفاتر ایرانی) حساب می‌کند، «چقدر پول داریم؟» را از همهٔ حساب‌های نقد و بانک پاسخ می‌دهد و خرجی که دسته‌بندی مشخصی ندارد را به جای پرسیدن، زیر هزینه‌های عمومی ثبت می‌کند.",
                    "es": "El asistente ahora resuelve «este mes», «el mes pasado» y «este año» en tu propio calendario (jalali para libros iraníes), responde «¿cuánto efectivo tenemos?» sumando todas las cuentas de caja y banco, y registra un gasto sin categoría clara en gastos generales en lugar de preguntar qué cuenta usar.",
                    "ar": "يحسب المساعد الآن «هذا الشهر» و«الشهر الماضي» و«هذا العام» وفق تقويمك (الهجري الشمسي للدفاتر الإيرانية)، ويجيب عن «كم نقداً لدينا؟» من كل حسابات النقد والبنك، ويسجّل المصروف الذي لا فئة له ضمن المصروفات العامة بدل أن يسأل عن الحساب.",
                },
            ),
            Highlight(
                key="stricter-checks",
                page="entities",
                roles=_SME_BOOKS,
                title={
                    "en": "Fewer slips get through",
                    "fa": "اشتباه‌های کمتری رد می‌شوند",
                    "es": "Se cuelan menos errores",
                    "ar": "أخطاء أقل تمرّ",
                },
                body={
                    "en": "A second party with the same name and type or a reused invoice number is refused, IBANs are checked, a day cannot hold more than 24 hours of time, petty cash cannot spend beyond its float, and uploading an Excel file you already imported shows a warning first. Edits now appear in the audit log too.",
                    "fa": "طرف حساب تکراری با همان نام و نوع یا شمارهٔ فاکتور تکراری رد می‌شود، شبا بررسی می‌شود، یک روز نمی‌تواند بیش از ۲۴ ساعت کار داشته باشد، تنخواه نمی‌تواند بیش از موجودی‌اش خرج کند و بارگذاری فایل اکسلی که قبلاً وارد کرده‌اید ابتدا هشدار می‌دهد. ویرایش‌ها هم اکنون در گزارش رخدادها دیده می‌شوند.",
                    "es": "Se rechaza un segundo tercero con el mismo nombre y tipo o un número de factura repetido, se verifican los IBAN, un día no puede tener más de 24 horas, la caja chica no puede gastar más de su fondo y subir un Excel ya importado avisa primero. Las ediciones también aparecen ahora en el registro de auditoría.",
                    "ar": "يُرفض طرف ثانٍ بنفس الاسم والنوع أو رقم فاتورة مكرر، ويُتحقق من أرقام الآيبان، ولا يمكن أن يتجاوز اليوم 24 ساعة عمل، ولا تنفق العهدة أكثر من رصيدها، ويُظهر رفع ملف إكسل سبق استيراده تحذيراً أولاً. تظهر التعديلات الآن في سجل التدقيق أيضاً.",
                },
            ),
            Highlight(
                key="invoice-credit",
                page="invoices",
                roles=_SME_BOOKS,
                title={
                    "en": "Over-payments shown as credit",
                    "fa": "پرداخت اضافه به‌صورت اعتبار نشان داده می‌شود",
                    "es": "Los pagos en exceso se muestran como saldo a favor",
                    "ar": "المدفوعات الزائدة تُعرض كرصيد دائن",
                },
                body={
                    "en": "When an invoice is paid above its amount, the invoice list shows the settled part as paid and the excess as credit on account, instead of a paid figure larger than the invoice.",
                    "fa": "وقتی فاکتوری بیش از مبلغش پرداخت می‌شود، فهرست فاکتورها بخش تسویه‌شده را به‌عنوان پرداخت‌شده و مازاد را به‌عنوان اعتبار نزد ما نشان می‌دهد، نه رقمی بزرگ‌تر از خود فاکتور.",
                    "es": "Cuando una factura se paga por encima de su importe, la lista muestra la parte liquidada como pagada y el exceso como saldo a favor, en lugar de un pagado mayor que la factura.",
                    "ar": "عندما تُسدَّد فاتورة بأكثر من قيمتها، تعرض القائمة الجزء المسدَّد كمدفوع والزائد كرصيد دائن، بدلاً من رقم مدفوع أكبر من الفاتورة.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.25",
        date="2026-09-25",
        highlights=(
            Highlight(
                key="payroll-statutory-rules",
                page="payroll",
                roles=_SME_BOOKS,
                title={
                    "en": "Payroll knows the 1405 rules",
                    "fa": "حقوق و دستمزد قوانین ۱۴۰۵ را می‌داند",
                    "es": "La nómina conoce las reglas de 1405",
                    "ar": "الرواتب تعرف قواعد 1405",
                },
                body={
                    "en": "Switch a pay profile to “Statutory rules”: housing, grocery, child and seniority allowances are added, insurance is 7 % / 23 % on the capped wage, and salary tax follows the monthly brackets. The employer's share is posted as a cost, and every run exports the insurance list and the salary-tax list as CSV. UK profiles get PAYE bands and National Insurance the same way.",
                    "fa": "پروفایل حقوق را روی «قوانین رسمی» بگذارید: حق مسکن، بن، حق اولاد و پایه سنوات اضافه می‌شود، بیمه ۷٪ / ۲۳٪ روی دستمزد مشمول تا سقف محاسبه می‌شود و مالیات حقوق طبق پلکان‌های ماهانه است. سهم کارفرما به‌عنوان هزینه ثبت می‌شود و هر لیست حقوق، لیست بیمه و لیست مالیات حقوق را به‌صورت CSV خروجی می‌دهد.",
                    "es": "Cambia un perfil de pago a «Reglas legales»: se añaden los complementos de vivienda, alimentación, hijos y antigüedad, el seguro es 7 % / 23 % sobre el salario con tope y el impuesto sigue los tramos mensuales. La cuota patronal se contabiliza como gasto y cada nómina exporta la lista de seguro y la de impuesto salarial en CSV. Los perfiles del Reino Unido obtienen PAYE y National Insurance del mismo modo.",
                    "ar": "حوّل ملف الراتب إلى «القواعد النظامية»: تُضاف بدلات السكن والمواد الغذائية والأبناء والأقدمية، ويُحسب التأمين 7٪ / 23٪ على الأجر حتى السقف، وتتبع ضريبة الراتب الشرائح الشهرية. تُسجَّل حصة صاحب العمل كمصروف، ويصدّر كل كشف رواتب قائمة التأمين وقائمة ضريبة الرواتب بصيغة CSV. ملفات المملكة المتحدة تحصل على شرائح PAYE والتأمين الوطني بالطريقة نفسها.",
                },
            ),
            Highlight(
                key="ai-invoices-cheques",
                page="ai-accountant",
                roles=_BOOKS,
                title={
                    "en": "Ask the assistant about invoices and cheques",
                    "fa": "از دستیار دربارهٔ فاکتورها و چک‌ها بپرسید",
                    "es": "Pregunta al asistente por facturas y cheques",
                    "ar": "اسأل المساعد عن الفواتير والشيكات",
                },
                body={
                    "en": "“Which invoices are overdue?”, “what cheques fall due this month?”, “record a payment on invoice 1042”, “settle the Bank Melli cheque” — the assistant reads your invoices, cheques and installments and proposes the entry for your Confirm.",
                    "fa": "«کدام فاکتورها سررسید گذشته‌اند؟»، «این ماه کدام چک‌ها سررسید می‌شوند؟»، «پرداخت فاکتور ۱۰۴۲ را ثبت کن»، «چک بانک ملی را پاس کن» — دستیار فاکتورها، چک‌ها و اقساط شما را می‌خواند و سند را برای تأیید شما پیشنهاد می‌دهد.",
                    "es": "«¿Qué facturas están vencidas?», «¿qué cheques vencen este mes?», «registra un pago de la factura 1042», «liquida el cheque del Bank Melli»: el asistente lee tus facturas, cheques y cuotas y propone el asiento para tu confirmación.",
                    "ar": "«ما الفواتير المتأخرة؟»، «ما الشيكات المستحقة هذا الشهر؟»، «سجّل دفعة على الفاتورة 1042»، «سدّد شيك بنك ملي» — يقرأ المساعد فواتيرك وشيكاتك وأقساطك ويقترح القيد لتأكيدك.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.25.1",
        date="2026-09-25",
        highlights=(
            Highlight(
                key="quotes",
                page="invoices",
                roles=_SME_BOOKS,
                title={
                    "en": "Quotes that turn into invoices",
                    "fa": "پیش‌فاکتورهایی که فاکتور می‌شوند",
                    "es": "Presupuestos que se convierten en facturas",
                    "ar": "عروض أسعار تتحول إلى فواتير",
                },
                body={
                    "en": "Fill in the invoice form and press “Save as quote”. The quote is not posted to the books; mark it sent, accepted or declined, download its PDF, and when the customer agrees press “Convert to invoice” — the sales invoice is created from the same lines and the quote is locked.",
                    "fa": "فرم فاکتور را پر کنید و «ذخیره به‌عنوان پیش‌فاکتور» را بزنید. پیش‌فاکتور در دفاتر ثبت نمی‌شود؛ آن را ارسال‌شده، پذیرفته یا ردشده علامت بزنید، PDF آن را بگیرید و وقتی مشتری موافقت کرد «تبدیل به فاکتور» را بزنید — فاکتور فروش با همان ردیف‌ها ساخته و پیش‌فاکتور قفل می‌شود.",
                    "es": "Rellena el formulario de factura y pulsa «Guardar como presupuesto». El presupuesto no se contabiliza; márcalo como enviado, aceptado o rechazado, descarga su PDF y, cuando el cliente acepte, pulsa «Convertir en factura»: la factura de venta se crea con las mismas líneas y el presupuesto queda bloqueado.",
                    "ar": "املأ نموذج الفاتورة واضغط «حفظ كعرض سعر». لا يُسجَّل العرض في الدفاتر؛ ضع عليه علامة مُرسل أو مقبول أو مرفوض، ونزّل ملف PDF، وعندما يوافق العميل اضغط «تحويل إلى فاتورة» — تُنشأ فاتورة المبيعات بالبنود نفسها ويُقفل العرض.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.25.2",
        date="2026-09-25",
        highlights=(
            Highlight(
                key="invoice-email-reminders",
                page="invoices",
                roles=_SME_BOOKS,
                title={
                    "en": "E-mail invoices and let reminders chase late payers",
                    "fa": "فاکتور را ایمیل کنید و یادآوری‌ها بدهکاران را پیگیری کنند",
                    "es": "Envía facturas por correo y deja que los recordatorios persigan a los morosos",
                    "ar": "أرسل الفواتير بالبريد ودع التذكيرات تلاحق المتأخرين",
                },
                body={
                    "en": "The new Email button sends the invoice PDF with your payment details to the customer. Under “Automatic reminders” the owner can switch on reminder e-mails for unpaid invoices (by default 3, 10 and 20 days after the due date). Every e-mail appears in the invoice history. Mail needs SMTP to be set up on the server.",
                    "fa": "دکمهٔ جدید «ایمیل» PDF فاکتور را همراه اطلاعات پرداخت برای مشتری می‌فرستد. در «یادآوری خودکار» مالک می‌تواند ایمیل یادآوری فاکتورهای پرداخت‌نشده را روشن کند (به‌طور پیش‌فرض ۳، ۱۰ و ۲۰ روز پس از سررسید). هر ایمیل در تاریخچهٔ فاکتور دیده می‌شود. ارسال ایمیل نیاز به تنظیم SMTP روی سرور دارد.",
                    "es": "El nuevo botón Correo envía el PDF de la factura con tus datos de pago al cliente. En «Recordatorios automáticos» el propietario puede activar correos para facturas impagadas (por defecto 3, 10 y 20 días tras el vencimiento). Cada correo aparece en el historial de la factura. Requiere SMTP configurado en el servidor.",
                    "ar": "زر «بريد» الجديد يرسل ملف PDF للفاتورة مع بيانات الدفع إلى العميل. ومن «تذكيرات تلقائية» يمكن للمالك تفعيل رسائل التذكير للفواتير غير المسددة (افتراضيًا بعد 3 و10 و20 يومًا من الاستحقاق). تظهر كل رسالة في سجل الفاتورة. يتطلب ذلك ضبط SMTP على الخادم.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.25.3",
        date="2026-09-25",
        highlights=(
            Highlight(
                key="recurring-invoices",
                page="invoices",
                roles=_SME_BOOKS,
                title={
                    "en": "Recurring invoices",
                    "fa": "فاکتورهای دوره‌ای",
                    "es": "Facturas recurrentes",
                    "ar": "الفواتير المتكررة",
                },
                body={
                    "en": "Fill in the invoice form and press “Make recurring from the form” to bill a customer every week, month, quarter or year. Monthly schedules can follow Persian months. Each invoice is issued and posted automatically and, if you tick the box, e-mailed to the customer.",
                    "fa": "فرم فاکتور را پر کنید و «ساخت فاکتور دوره‌ای از فرم» را بزنید تا مشتری هر هفته، ماه، فصل یا سال صورتحساب شود. برنامهٔ ماهانه می‌تواند بر اساس ماه‌های شمسی باشد. هر فاکتور خودکار صادر و ثبت می‌شود و اگر گزینه را بزنید، برای مشتری ایمیل می‌شود.",
                    "es": "Rellena el formulario de factura y pulsa «Hacer recurrente desde el formulario» para facturar a un cliente cada semana, mes, trimestre o año. Los calendarios mensuales pueden seguir los meses persas. Cada factura se emite y contabiliza sola y, si marcas la casilla, se envía al cliente por correo.",
                    "ar": "املأ نموذج الفاتورة واضغط «اجعلها متكررة من النموذج» لفوترة العميل كل أسبوع أو شهر أو ربع أو سنة. يمكن أن تتبع الجداول الشهرية الأشهر الفارسية. تصدر كل فاتورة وتُسجَّل تلقائيًا، وتُرسل إلى العميل بالبريد إذا فعّلت الخيار.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.25.4",
        date="2026-09-25",
        highlights=(
            Highlight(
                key="moadian-export",
                page="invoices",
                roles=_SME_BOOKS,
                locales=("ir",),
                title={
                    "en": "Get invoices ready for سامانه مودیان",
                    "fa": "آماده‌سازی صورتحساب‌ها برای سامانه مودیان",
                    "es": "Prepara las facturas para سامانه مودیان",
                    "ar": "تجهيز الفواتير لـ سامانه مودیان",
                },
                body={
                    "en": "Enter your tax memory id under Invoices → سامانه مودیان. Each sales invoice then shows what is missing (goods/service ID, buyer national id, postal code). “Export selected” downloads the invoices in the tax organisation's JSON with their 22-character tax numbers for your trusted provider. The bell warns three days before the 12-day deadline.",
                    "fa": "شناسه یکتای حافظه مالیاتی را در «فاکتورها ← سامانه مودیان» وارد کنید. سپس برای هر صورتحساب فروش نشان داده می‌شود چه چیزی کم است (شناسه کالا/خدمت، شناسه ملی خریدار، کد پستی). «خروجی موارد انتخاب‌شده» صورتحساب‌ها را با ساختار JSON سازمان امور مالیاتی و شمارهٔ منحصربه‌فرد ۲۲ کاراکتری برای شرکت معتمد دانلود می‌کند. زنگ اعلان سه روز پیش از پایان مهلت ۱۲ روزه هشدار می‌دهد.",
                    "es": "Introduce tu ID de memoria fiscal en Facturas → سامانه مودیان. Cada factura de venta muestra entonces lo que falta (ID de bien/servicio, ID nacional del comprador, código postal). «Exportar seleccionadas» descarga las facturas en el JSON de la administración tributaria con su número fiscal de 22 caracteres para tu proveedor autorizado. La campana avisa tres días antes del plazo de 12 días.",
                    "ar": "أدخل معرّف الذاكرة الضريبية في الفواتير ← سامانه مودیان. عندها تُظهر كل فاتورة مبيعات ما ينقصها (معرّف السلعة/الخدمة، الرقم الوطني للمشتري، الرمز البريدي). «تصدير المحدد» ينزّل الفواتير بصيغة JSON الخاصة بمصلحة الضرائب مع أرقامها الضريبية ذات 22 حرفًا لمزوّدك المعتمد. ينبّه الجرس قبل ثلاثة أيام من انتهاء مهلة الـ 12 يومًا.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.25.5",
        date="2026-09-25",
        highlights=(
            Highlight(
                key="two-factor",
                title={
                    "en": "Two-factor sign-in",
                    "fa": "ورود دومرحله‌ای",
                    "es": "Verificación en dos pasos",
                    "ar": "التحقق بخطوتين",
                },
                body={
                    "en": "Open the account menu (top right) → Two-factor sign-in. Scan the code with an authenticator app and sign-in will ask for a six-digit code after your password, so a leaked password alone is not enough. Keep the ten recovery codes somewhere safe. Recommended for owners.",
                    "fa": "منوی حساب (بالا) ← ورود دومرحله‌ای را باز کنید. کد را با یک برنامهٔ احراز هویت اسکن کنید؛ از این پس پس از رمز عبور یک کد شش‌رقمی خواسته می‌شود و لو رفتن رمز به‌تنهایی کافی نیست. ده کد بازیابی را جای امنی نگه دارید. برای مالکان توصیه می‌شود.",
                    "es": "Abre el menú de la cuenta (arriba) → Verificación en dos pasos. Escanea el código con una app de autenticación y, tras la contraseña, se pedirá un código de seis dígitos, así que una contraseña filtrada no basta. Guarda los diez códigos de recuperación en un lugar seguro. Recomendado para propietarios.",
                    "ar": "افتح قائمة الحساب (في الأعلى) ← التحقق بخطوتين. امسح الرمز بتطبيق مصادقة، وبعد كلمة المرور سيُطلب رمز من ستة أرقام، فلا تكفي كلمة مرور مسرّبة وحدها. احفظ رموز الاسترداد العشرة في مكان آمن. موصى به للمالكين.",
                },
            ),
            Highlight(
                key="api-key-scopes",
                page="settings",
                roles=("owner",),
                title={
                    "en": "Limit and expire integration keys",
                    "fa": "محدودیت و انقضای کلیدهای یکپارچه‌سازی",
                    "es": "Limita y caduca las claves de integración",
                    "ar": "تقييد مفاتيح التكامل وتحديد انتهائها",
                },
                body={
                    "en": "Settings → API keys: choose what a new key may do (read or push worklogs) and when it expires — 90 days, a year, two years or never. Existing keys keep working. You get a notification two weeks before a key expires.",
                    "fa": "تنظیمات ← کلیدهای API: برای کلید جدید تعیین کنید چه کاری مجاز است (خواندن یا ارسال کارکرد) و کی منقضی شود — ۹۰ روز، یک سال، دو سال یا هرگز. کلیدهای فعلی کار می‌کنند. دو هفته پیش از انقضای کلید اعلان دریافت می‌کنید.",
                    "es": "Ajustes → Claves API: elige qué puede hacer una clave nueva (leer o enviar registros de trabajo) y cuándo caduca: 90 días, un año, dos años o nunca. Las claves existentes siguen funcionando. Recibirás un aviso dos semanas antes de que caduque una clave.",
                    "ar": "الإعدادات ← مفاتيح API: اختر ما يُسمح للمفتاح الجديد به (قراءة سجلات العمل أو إرسالها) ومتى ينتهي — 90 يومًا أو سنة أو سنتان أو أبدًا. المفاتيح الحالية تستمر في العمل. يصلك إشعار قبل أسبوعين من انتهاء المفتاح.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.26",
        date="2026-09-26",
        highlights=(
            Highlight(
                key="ai-usage",
                page="settings",
                roles=("owner",),
                title={
                    "en": "See and cap your AI usage",
                    "fa": "مشاهده و سقف‌گذاری مصرف هوش مصنوعی",
                    "es": "Consulta y limita el uso de IA",
                    "ar": "اطّلع على استخدام الذكاء الاصطناعي وحدّده",
                },
                body={
                    "en": "Settings → AI usage shows how many tokens the AI used in the last 24 hours and 30 days — per user, per use (chat, reading documents, suggestions) and per model — with an estimated cost. You can cap how much each user may use in 24 hours; you are notified at 80% of the company allowance.",
                    "fa": "«تنظیمات ← مصرف هوش مصنوعی» نشان می‌دهد هوش مصنوعی در ۲۴ ساعت و ۳۰ روز گذشته چند توکن مصرف کرده — به تفکیک کاربر، کاربرد (گفت‌وگو، خواندن اسناد، پیشنهادها) و مدل — همراه با هزینهٔ تخمینی. می‌توانید سقف مصرف هر کاربر در ۲۴ ساعت را تعیین کنید؛ در ۸۰٪ سقف شرکت به شما اطلاع داده می‌شود.",
                    "es": "Ajustes → Uso de IA muestra cuántos tokens usó la IA en las últimas 24 horas y 30 días —por usuario, por uso (chat, lectura de documentos, sugerencias) y por modelo— con un coste estimado. Puedes limitar lo que cada usuario usa en 24 horas; te avisamos al 80 % del límite de la empresa.",
                    "ar": "الإعدادات ← استخدام الذكاء الاصطناعي تعرض عدد الرموز التي استخدمها الذكاء الاصطناعي خلال آخر 24 ساعة و30 يومًا — حسب المستخدم والاستخدام (المحادثة، قراءة المستندات، الاقتراحات) والنموذج — مع تكلفة تقديرية. يمكنك تحديد ما يستخدمه كل مستخدم خلال 24 ساعة، ويصلك تنبيه عند 80٪ من حد الشركة.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.26.1",
        date="2026-09-26",
        highlights=(
            Highlight(
                key="seasonal-tax-reports",
                page="invoices",
                roles=_SME_BOOKS,
                locales=("ir",),
                title={
                    "en": "Seasonal tax reports: TTMS and the VAT return",
                    "fa": "گزارش‌های فصلی: معاملات فصلی و اظهارنامهٔ ارزش افزوده",
                    "es": "Informes trimestrales: TTMS y declaración de IVA",
                    "ar": "التقارير الفصلية: TTMS وإقرار القيمة المضافة",
                },
                body={
                    "en": "Invoices → Seasonal tax reports: pick a Jalali season to see sales and purchases per counterparty for the quarterly transactions report (due 45 days after the season) and the VAT return figures (due 15 days after), checked against each other. Download the Excel file for TTMS; parties with missing national id, economic code, postal code, address or phone are listed. Sales accepted in سامانه مودیان are left out. You are reminded before each deadline.",
                    "fa": "«فاکتورها ← گزارش‌های فصلی مالیاتی»: یک فصل را انتخاب کنید تا فروش و خرید به تفکیک طرف معامله برای گزارش معاملات فصلی (تا ۴۵ روز پس از فصل) و ارقام اظهارنامهٔ ارزش افزوده (تا ۱۵ روز پس از فصل) را ببینید که با هم تطبیق داده می‌شوند. فایل اکسل را برای TTMS دانلود کنید؛ طرف‌هایی که شناسه ملی، کد اقتصادی، کد پستی، نشانی یا تلفن ندارند فهرست می‌شوند. فروش‌های پذیرفته‌شده در سامانه مودیان کنار گذاشته می‌شوند. پیش از هر مهلت یادآوری دریافت می‌کنید.",
                    "es": "Facturas → Informes fiscales trimestrales: elige un trimestre del calendario persa para ver ventas y compras por contraparte para el informe TTMS (45 días tras el trimestre) y las cifras de la declaración de IVA (15 días tras el trimestre), cotejadas entre sí. Descarga el Excel para TTMS; se listan las contrapartes sin identificación nacional, código económico, código postal, dirección o teléfono. Las ventas aceptadas en سامانه مودیان quedan fuera. Recibirás un aviso antes de cada plazo.",
                    "ar": "الفواتير ← التقارير الضريبية الفصلية: اختر فصلًا لترى المبيعات والمشتريات حسب الطرف المقابل لتقرير المعاملات الفصلي (خلال 45 يومًا من نهاية الفصل) وأرقام إقرار القيمة المضافة (خلال 15 يومًا)، مع مطابقتها. نزّل ملف Excel لـ TTMS؛ تُدرج الأطراف التي تنقصها الهوية الوطنية أو الرمز الاقتصادي أو الرمز البريدي أو العنوان أو الهاتف. المبيعات المقبولة في سامانه مودیان مستبعدة. يصلك تذكير قبل كل مهلة.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.26.2",
        date="2026-09-26",
        highlights=(
            Highlight(
                key="uk-mtd",
                page="invoices",
                roles=_SME_BOOKS,
                locales=("uk",),
                title={
                    "en": "Making Tax Digital: VAT boxes and income tax updates",
                    "fa": "MTD بریتانیا: خانه‌های VAT و به‌روزرسانی‌های مالیات بر درآمد",
                    "es": "Making Tax Digital: casillas de IVA y actualizaciones del impuesto sobre la renta",
                    "ar": "الضرائب الرقمية: خانات القيمة المضافة وتحديثات ضريبة الدخل",
                },
                body={
                    "en": "Invoices → Making Tax Digital (UK): the nine VAT return boxes for each VAT period from your invoices, with a CSV for your MTD software; and, for sole traders and landlords, the quarterly income tax update in HMRC's categories — cumulative from April, with the account mapping you can change, an Excel file and the update HMRC expects. Reminders arrive before each deadline.",
                    "fa": "«فاکتورها ← MTD بریتانیا»: نُه خانهٔ اظهارنامهٔ VAT برای هر دوره از روی فاکتورها با خروجی CSV؛ و برای خوداشتغالان و موجران، به‌روزرسانی فصلی مالیات بر درآمد در دسته‌بندی‌های HMRC — تجمعی از آوریل، با نگاشت قابل تغییر حساب‌ها، فایل اکسل و ساختار مورد انتظار HMRC. پیش از هر مهلت یادآوری دریافت می‌کنید.",
                    "es": "Facturas → Making Tax Digital (Reino Unido): las nueve casillas de la declaración de IVA de cada periodo a partir de tus facturas, con un CSV para tu software MTD; y, para autónomos y arrendadores, la actualización trimestral del impuesto sobre la renta en las categorías de HMRC, acumulada desde abril, con la asignación de cuentas editable, un Excel y la actualización que espera HMRC. Recibirás avisos antes de cada plazo.",
                    "ar": "الفواتير ← الضرائب الرقمية (المملكة المتحدة): الخانات التسع لإقرار القيمة المضافة لكل فترة من فواتيرك مع ملف CSV لبرنامج MTD؛ وللعاملين لحسابهم والمؤجرين، التحديث الفصلي لضريبة الدخل بفئات HMRC — تراكميًا من أبريل، مع ربط حسابات قابل للتعديل وملف Excel والتحديث الذي تتوقعه HMRC. تصلك تذكيرات قبل كل مهلة.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.26.3",
        date="2026-09-26",
        highlights=(
            Highlight(
                key="bank-sms",
                page="bank-statements",
                roles=("owner", "cfo", "accountant", "personal"),
                locales=("ir",),
                title={
                    "en": "Bank SMS straight into your books",
                    "fa": "پیامک بانکی مستقیم در دفاتر",
                    "es": "SMS del banco directos a tus libros",
                    "ar": "رسائل البنك مباشرة إلى دفاترك",
                },
                body={
                    "en": "Bank statements → Paste bank SMS: paste the messages your bank sends (many at once) and each becomes a row of that bank's monthly SMS statement, to review and approve like any statement. Repeats are skipped and a balance that doesn't follow the previous message is flagged. A phone automation can forward them with an API key.",
                    "fa": "«صورت‌حساب‌های بانکی ← چسباندن پیامک بانکی»: پیامک‌های بانک را (چند تا با هم) بچسبانید تا هر کدام یک سطر از صورت‌حساب پیامکی ماهانهٔ همان بانک شود و مثل هر صورت‌حساب دیگری بررسی و تأیید شود. پیامک تکراری نادیده گرفته می‌شود و مانده‌ای که با پیامک قبلی جور نباشد علامت می‌خورد. با یک کلید API گوشی هم می‌تواند آن‌ها را خودکار بفرستد.",
                    "es": "Extractos bancarios → Pegar SMS del banco: pega los mensajes que envía tu banco (varios a la vez) y cada uno se convierte en una fila del extracto mensual de SMS de ese banco, para revisar y aprobar como cualquier extracto. Los repetidos se omiten y se avisa si un saldo no cuadra con el mensaje anterior. Un atajo del teléfono puede reenviarlos con una clave API.",
                    "ar": "كشوف البنك ← لصق رسائل البنك: الصق الرسائل التي يرسلها بنكك (عدة رسائل معًا) لتصبح كل رسالة سطرًا في كشف الرسائل الشهري لذلك البنك، للمراجعة والاعتماد كأي كشف. تُتجاوز المكررة ويُنبَّه على الرصيد الذي لا يتبع الرسالة السابقة. يمكن لاختصار في الهاتف إعادة توجيهها بمفتاح API.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.27",
        date="2026-09-27",
        highlights=(
            Highlight(
                key="anomaly-insights",
                page="dashboard",
                roles=_SME_BOOKS,
                title={
                    "en": "The assistant now watches for unusual entries",
                    "fa": "دستیار اکنون سندهای غیرعادی را زیر نظر دارد",
                    "es": "El asistente vigila ahora los asientos inusuales",
                    "ar": "المساعد يراقب الآن القيود غير المعتادة",
                },
                body={
                    "en": "Insights on the dashboard and in the chat now flag possible duplicate supplier payments, payments split just under your approval limit, a large first payment to a new supplier, round-amount entries on a weekend, one expense account suddenly taking over your spending, and a run of reversals for one customer or supplier.",
                    "fa": "بینش‌های داشبورد و گفت‌وگو اکنون این موارد را نشان می‌دهند: پرداخت احتمالاً تکراری به تأمین‌کننده، پرداخت‌های تقسیم‌شده کمی زیر سقف تأیید، نخستین پرداخت بزرگ به تأمین‌کنندهٔ جدید، سندهای رُند در روز تعطیل، سهم ناگهانی یک حساب هزینه از کل هزینه‌ها، و برگشت‌های پرتعداد برای یک مشتری یا تأمین‌کننده.",
                    "es": "Las ideas del panel y del chat señalan ahora posibles pagos duplicados a proveedores, pagos divididos justo por debajo del límite de aprobación, un primer pago elevado a un proveedor nuevo, asientos redondos en fin de semana, una cuenta de gasto que de pronto acapara el gasto y una racha de anulaciones para un cliente o proveedor.",
                    "ar": "تُظهر الرؤى في لوحة التحكم والمحادثة الآن: دفعات مكررة محتملة للموردين، ودفعات مقسّمة أقل بقليل من حد الموافقة، وأول دفعة كبيرة لمورّد جديد، وقيودًا بمبالغ مدوّرة في عطلة نهاية الأسبوع، وحساب مصروفات يستحوذ فجأة على الإنفاق، وسلسلة انعكاسات لعميل أو مورّد.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.27.1",
        date="2026-09-27",
        highlights=(
            Highlight(
                key="cash-forecast",
                page="dashboard",
                roles=_SME_BOOKS,
                title={
                    "en": "A cash forecast that learns — and asks “what if?”",
                    "fa": "پیش‌بینی نقدینگی که یاد می‌گیرد — و می‌پرسد «اگر…؟»",
                    "es": "Un pronóstico de caja que aprende — y pregunta «¿y si…?»",
                    "ar": "توقع نقدي يتعلّم — ويسأل «ماذا لو؟»",
                },
                body={
                    "en": "The dashboard's 13-week forecast now expects each customer's invoices when that customer usually pays, and adds bills, pending cheques and installments, payroll and recurring flows to your usual week. Open “Forecast details and what-if” to see each week's items, or try a scenario — a cheque that bounces, an invoice that isn't paid, a customer paying a month late, a one-off purchase. You can also ask the AI: “what if the Mellat cheque bounces?”",
                    "fa": "پیش‌بینی ۱۳ هفته‌ای داشبورد اکنون فاکتورهای هر مشتری را همان زمانی انتظار دارد که آن مشتری معمولاً پرداخت می‌کند و قبض‌ها، چک‌ها و اقساط در جریان، حقوق و پرداخت‌های تکراری را به هفتهٔ معمول شما اضافه می‌کند. «جزئیات پیش‌بینی و اگر…» را باز کنید تا اقلام هر هفته را ببینید، یا یک سناریو امتحان کنید — برگشت خوردن یک چک، پرداخت نشدن یک فاکتور، یک ماه دیرتر پرداختن یک مشتری، یک خرید یک‌باره. از دستیار هم می‌توانید بپرسید: «اگه چک ملت برگشت بخوره چی؟»",
                    "es": "El pronóstico de 13 semanas del panel espera ahora las facturas de cada cliente cuando ese cliente suele pagar, y suma facturas de proveedores, cheques y cuotas pendientes, nóminas y flujos recurrentes a tu semana habitual. Abre «Detalle del pronóstico y ¿y si…?» para ver las partidas de cada semana o prueba un escenario: un cheque devuelto, una factura que no se cobra, un cliente que paga un mes tarde, una compra puntual. También puedes preguntar a la IA: «¿y si rebota el cheque del banco Mellat?»",
                    "ar": "يتوقع توقع الـ 13 أسبوعًا في لوحة التحكم الآن فواتير كل عميل في الموعد الذي يدفع فيه ذلك العميل عادةً، ويضيف فواتير الموردين والشيكات والأقساط المعلّقة والرواتب والتدفقات المتكررة إلى أسبوعك المعتاد. افتح «تفاصيل التوقع وماذا لو» لرؤية بنود كل أسبوع، أو جرّب سيناريو — شيك يرتد، فاتورة لا تُدفع، عميل يدفع متأخرًا شهرًا، شراء لمرة واحدة. ويمكنك أيضًا سؤال المساعد: «ماذا لو ارتد شيك بنك ملت؟»",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.27.2",
        date="2026-09-27",
        highlights=(
            Highlight(
                key="fixed-assets",
                page="fixed-assets",
                roles=_SME_BOOKS,
                title={
                    "en": "A fixed-asset register with month-end depreciation",
                    "fa": "فهرست دارایی‌های ثابت با استهلاک پایان ماه",
                    "es": "Un registro de activos fijos con amortización de fin de mes",
                    "ar": "سجل للأصول الثابتة مع استهلاك نهاية الشهر",
                },
                body={
                    "en": "Records → Fixed assets: add each asset with its cost, method and life (Iranian companies get the Article 149 table's common categories, starting the month after the asset comes into use). One click posts every month of depreciation that has ended, and selling or scrapping an asset works out the gain or loss for you.",
                    "fa": "«سوابق ← دارایی‌های ثابت»: هر دارایی را با بهای تمام‌شده، روش و عمر مفید ثبت کنید (برای شرکت‌های ایرانی گروه‌های رایج جدول استهلاکات ماده ۱۴۹، با شروع از ماه بعد از بهره‌برداری). با یک کلیک استهلاک همهٔ ماه‌های تمام‌شده ثبت می‌شود و هنگام فروش یا اسقاط، سود یا زیان آن خودکار محاسبه می‌شود.",
                    "es": "Registros → Activos fijos: añade cada activo con su coste, método y vida útil. Un clic contabiliza la amortización de todos los meses terminados, y al vender o dar de baja un activo se calcula la ganancia o la pérdida.",
                    "ar": "السجلات ← الأصول الثابتة: أضف كل أصل بتكلفته وطريقته وعمره الإنتاجي. بنقرة واحدة يُرحَّل استهلاك كل الأشهر المنتهية، وعند بيع أصل أو شطبه يُحسب الربح أو الخسارة تلقائيًا.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.27.3",
        date="2026-09-27",
        highlights=(
            Highlight(
                key="inventory-costing",
                page="inventory",
                roles=_SME_BOOKS,
                title={
                    "en": "Stock valued by FIFO or weighted average, with reorder alerts",
                    "fa": "ارزیابی موجودی با FIFO یا میانگین موزون، همراه با هشدار نقطهٔ سفارش",
                    "es": "Existencias valoradas por FIFO o precio medio, con avisos de reposición",
                    "ar": "تقييم المخزون بطريقة FIFO أو المتوسط المرجح، مع تنبيهات إعادة الطلب",
                },
                body={
                    "en": "Inventory now shows the value of your stock on any date under the costing method you choose (and what the other method would give), marks items at their reorder point — the dashboard tells you too — and takes barcodes. Recipes say what goes into a finished item, and a production run moves the components out and the finished item in at cost.",
                    "fa": "بخش موجودی اکنون ارزش کالاها را در هر تاریخ با روش ارزیابی انتخابی شما نشان می‌دهد (و رقم روش دیگر را هم برای مقایسه)، کالاهایی را که به نقطهٔ سفارش رسیده‌اند علامت می‌زند — داشبورد هم خبر می‌دهد — و بارکد می‌پذیرد. با فرمول ساخت مشخص می‌کنید هر کالای ساخته‌شده از چه اجزایی تشکیل شده و با هر تولید، اجزا خارج و کالای ساخته‌شده به بهای تمام‌شده وارد انبار می‌شود.",
                    "es": "Inventario muestra ahora el valor de tus existencias en cualquier fecha con el método que elijas (y lo que daría el otro), marca los artículos en su punto de pedido —también en el panel— y admite códigos de barras. Las recetas indican qué lleva un producto terminado, y una producción saca los componentes y entra el producto a coste.",
                    "ar": "يعرض المخزون الآن قيمة بضاعتك في أي تاريخ بطريقة التكلفة التي تختارها (وما ستعطيه الطريقة الأخرى)، ويعلّم الأصناف عند نقطة إعادة الطلب — وتنبّهك لوحة التحكم أيضًا — ويقبل الباركود. تحدد الوصفات ما يدخل في المنتج النهائي، وتُخرج عملية الإنتاج المكونات وتُدخل المنتج بالتكلفة.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.27.4",
        date="2026-09-27",
        highlights=(
            Highlight(
                key="chart-of-accounts",
                page="accounts",
                roles=_SME_BOOKS,
                title={
                    "en": "Manage your chart of accounts and opening balances",
                    "fa": "مدیریت کدینگ حساب‌ها و تراز افتتاحیه",
                    "es": "Gestiona tu plan de cuentas y los saldos de apertura",
                    "ar": "أدر دليل حساباتك وأرصدتك الافتتاحية",
                },
                body={
                    "en": "Setup → Chart of accounts: add accounts under the right parent (the code is suggested and the level follows — group, general, subsidiary, detail), rename them, deactivate the ones you no longer use (they keep their history but take no new entries), and enter your opening balances in one place.",
                    "fa": "«تنظیمات ← کدینگ حساب‌ها»: حساب‌ها را زیر حساب مادر درست اضافه کنید (کد پیشنهاد می‌شود و سطح — گروه، کل، معین، تفصیلی — از مادر می‌آید)، نامشان را تغییر دهید، حساب‌های بلااستفاده را غیرفعال کنید (سوابقشان می‌ماند ولی سند جدید نمی‌گیرند) و تراز افتتاحیه را یک‌جا وارد کنید.",
                    "es": "Configuración → Plan de cuentas: añade cuentas bajo la cuenta madre correcta (se sugiere el código y el nivel la sigue), cámbiales el nombre, desactiva las que no uses (conservan su historial pero no admiten asientos nuevos) e introduce los saldos de apertura en un solo lugar.",
                    "ar": "الإعداد ← دليل الحسابات: أضف الحسابات تحت الحساب الأب الصحيح (يُقترح الرمز ويتبعه المستوى)، وغيّر أسماءها، وأوقف ما لا تستخدمه (يبقى سجله لكنه لا يقبل قيودًا جديدة)، وأدخل الأرصدة الافتتاحية في مكان واحد.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.27.5",
        date="2026-09-27",
        highlights=(
            Highlight(
                key="installable-app",
                page="ai-accountant",
                roles=_BOOKS,
                title={
                    "en": "Install it on your phone — and photograph receipts into the chat",
                    "fa": "برنامه را روی گوشی نصب کنید — و از رسیدها مستقیم در گفت‌وگو عکس بگیرید",
                    "es": "Instálala en el móvil y fotografía recibos directamente en el chat",
                    "ar": "ثبّته على هاتفك — وصوّر الإيصالات مباشرة في المحادثة",
                },
                body={
                    "en": "Open the account menu and choose Install the app to put it on your home screen. On a phone the chat has a camera button: photograph a receipt and it is attached, shrunk to a sensible size. You can also share a photo or PDF from another app straight to Accounting.",
                    "fa": "از منوی حساب «نصب برنامه» را بزنید تا روی صفحهٔ اصلی گوشی قرار بگیرد. روی گوشی، گفت‌وگو دکمهٔ دوربین دارد: از رسید عکس بگیرید تا با حجمی مناسب پیوست شود. همچنین می‌توانید عکس یا PDF را از برنامه‌ای دیگر مستقیم با این برنامه به اشتراک بگذارید.",
                    "es": "Abre el menú de la cuenta y elige Instalar la app para tenerla en la pantalla de inicio. En el móvil el chat tiene un botón de cámara: fotografía un recibo y se adjunta, reducido a un tamaño razonable. También puedes compartir una foto o un PDF desde otra app directamente.",
                    "ar": "افتح قائمة الحساب واختر تثبيت التطبيق لوضعه على الشاشة الرئيسية. على الهاتف تحتوي المحادثة على زر كاميرا: صوّر الإيصال فيُرفق بحجم مناسب. ويمكنك أيضًا مشاركة صورة أو ملف PDF من تطبيق آخر مباشرة.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.27.6",
        date="2026-09-27",
        highlights=(
            Highlight(
                key="push-notifications",
                title={
                    "en": "Alerts on your phone",
                    "fa": "اعلان‌ها روی گوشی شما",
                    "es": "Avisos en tu móvil",
                    "ar": "التنبيهات على هاتفك",
                },
                body={
                    "en": "Account menu → Phone notifications: turn on. New alerts from the bell — an invoice overdue, a cheque due, an approval waiting, your own reminders — then arrive on this device even when the app is closed. Several at once come as one summary.",
                    "fa": "«منوی حساب ← اعلان روی گوشی: روشن کن». از آن پس اعلان‌های جدید زنگوله — فاکتور سررسیدگذشته، چک سررسیدشده، تأییدی که منتظر است، یادآورهای خودتان — حتی وقتی برنامه بسته است روی همین دستگاه می‌رسند. چند اعلان هم‌زمان در یک خلاصه می‌آیند.",
                    "es": "Menú de la cuenta → Avisos en el móvil: activar. Los avisos nuevos de la campana — una factura vencida, un cheque que vence, una aprobación pendiente, tus recordatorios — llegarán a este dispositivo aunque la app esté cerrada. Si llegan varios a la vez, verás un resumen.",
                    "ar": "قائمة الحساب ← إشعارات الهاتف: تشغيل. ستصل تنبيهات الجرس الجديدة — فاتورة متأخرة، شيك مستحق، موافقة بانتظارك، تذكيراتك — إلى هذا الجهاز حتى والتطبيق مغلق. وإن وصلت عدة تنبيهات معًا فستصلك في ملخص واحد.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.27.7",
        date="2026-09-27",
        highlights=(
            Highlight(
                key="journal-import",
                page="migration",
                roles=("owner", "accountant"),
                title={
                    "en": "Bring your past vouchers over from another system",
                    "fa": "اسناد سال‌های قبل را از نرم‌افزار قبلی بیاورید",
                    "es": "Trae tus asientos anteriores desde otro sistema",
                    "ar": "انقل قيودك السابقة من نظام آخر",
                },
                body={
                    "en": "Migration → Historical journals: export the journal from Hesabfa, Sepidar, Holoo, Xero or QuickBooks (or any spreadsheet) and upload it. The columns are recognised in English or Persian, Jalali dates too; you match any account the app doesn't know once, see which vouchers can't be posted and why, then post the rest. Importing the same file again never doubles them.",
                    "fa": "«مهاجرت ← اسناد سال‌های قبل»: دفتر روزنامه را از حسابفا، سپیدار، هلو، زیرو یا کوئیک‌بوکس (یا هر صفحه‌گسترده‌ای) خروجی بگیرید و بارگذاری کنید. ستون‌ها به فارسی یا انگلیسی شناخته می‌شوند و تاریخ شمسی هم خوانده می‌شود؛ حساب‌هایی را که برنامه نمی‌شناسد یک بار معادل‌سازی می‌کنید، می‌بینید کدام اسناد و چرا ثبت نمی‌شوند، و بقیه را ثبت می‌کنید. ورود دوبارهٔ همان فایل هیچ سندی را تکراری ثبت نمی‌کند.",
                    "es": "Migración → Asientos históricos: exporta el diario de Hesabfa, Sepidar, Holoo, Xero o QuickBooks (o cualquier hoja) y súbelo. Las columnas se reconocen en inglés o persa, también las fechas jalali; asignas una vez las cuentas que la app no conoce, ves qué asientos no se pueden contabilizar y por qué, y contabilizas el resto. Importar el mismo archivo otra vez nunca los duplica.",
                    "ar": "الترحيل ← القيود التاريخية: صدّر دفتر اليومية من حسابفا أو سبيدار أو هلو أو Xero أو QuickBooks (أو أي جدول) وارفعه. تُعرف الأعمدة بالإنجليزية أو الفارسية والتواريخ الشمسية أيضًا؛ تطابق مرة واحدة الحسابات التي لا يعرفها التطبيق، وترى القيود التي لا يمكن ترحيلها ولماذا، ثم ترحّل الباقي. لا يكرر استيراد الملف نفسه أي قيد.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="fx-rates",
                page="settings",
                roles=("owner",),
                title={
                    "en": "Exchange rates: your own, and daily shared ones",
                    "fa": "نرخ ارز: نرخ‌های خودتان، و نرخ‌های مشترک روزانه",
                    "es": "Tipos de cambio: los tuyos y los compartidos cada día",
                    "ar": "أسعار الصرف: أسعارك الخاصة، وأسعار مشتركة يومية",
                },
                body={
                    "en": "Settings → Currency & FX: a rate you enter now belongs to your company alone and, for that pair, replaces the shared rate in your books — no other company can see or change it. Rates marked “shared” arrive automatically each day where the server is set up for it: the euro, dollar, pound and more from the European Central Bank, and the rial market and gold from a provider the administrator chooses. A pair with no rate is worked out through the dollar, euro, pound or rial.",
                    "fa": "«تنظیمات ← ارز و نرخ تبدیل»: نرخی که از این پس وارد می‌کنید فقط مال شرکت شماست و برای همان جفت ارز در دفاتر شما جای نرخ مشترک را می‌گیرد — هیچ شرکت دیگری آن را نمی‌بیند یا تغییر نمی‌دهد. نرخ‌های «مشترک» هر روز خودکار می‌رسند، اگر سرور برایش تنظیم شده باشد: یورو، دلار، پوند و ارزهای دیگر از بانک مرکزی اروپا، و بازار آزاد ریال و طلا از سرویسی که مدیر سرور انتخاب می‌کند. جفت ارزی که نرخ ندارد از طریق دلار، یورو، پوند یا ریال حساب می‌شود.",
                    "es": "Ajustes → Moneda y cambio: un tipo que introduzcas ahora es solo de tu empresa y, para ese par, sustituye al compartido en tus libros; ninguna otra empresa puede verlo ni cambiarlo. Los tipos «compartidos» llegan solos cada día si el servidor está configurado: el euro, el dólar, la libra y más del Banco Central Europeo, y el mercado del rial y el oro de un proveedor que elige el administrador. Un par sin tipo se calcula a través del dólar, el euro, la libra o el rial.",
                    "ar": "الإعدادات ← العملة والصرف: السعر الذي تُدخله الآن خاص بشركتك وحدها، ويحلّ لذلك الزوج محل السعر المشترك في دفاترك — ولا تستطيع أي شركة أخرى رؤيته أو تغييره. تصل الأسعار «المشتركة» تلقائيًا كل يوم إن كان الخادم مهيّأً لذلك: اليورو والدولار والجنيه وغيرها من البنك المركزي الأوروبي، وسوق الريال والذهب من مزوّد يختاره المسؤول. أما الزوج الذي لا سعر له فيُحسب عبر الدولار أو اليورو أو الجنيه أو الريال.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.2",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="base-currency-values",
                page="ledger",
                roles=("owner", "cfo", "accountant", "viewer"),
                title={
                    "en": "Every currency in one report",
                    "fa": "همهٔ ارزها در یک گزارش",
                    "es": "Todas las monedas en un solo informe",
                    "ar": "كل العملات في تقرير واحد",
                },
                body={
                    "en": "Each entry now also keeps its value in your base currency, at the rate of its date (or the one you type on the voucher). Pick \"All currencies\" in the ledger or the manager reports to see dollars, euros and pounds added up properly; one currency on its own still works as before. An entry with no rate for its date waits and is converted as soon as one is added. Revaluation now changes only the base value of cash, receivables and payables — fixed assets stay at cost.",
                    "fa": "هر سند از این پس ارزش خود را به ارز پایهٔ شما هم نگه می‌دارد، با نرخ تاریخ خودش (یا نرخی که روی سند وارد می‌کنید). در دفتر کل یا گزارش‌های مدیریتی «همهٔ ارزها» را انتخاب کنید تا دلار و یورو و پوند درست با هم جمع شوند؛ نمایش تک‌ارزی مثل قبل کار می‌کند. سندی که برای تاریخش نرخ نیست منتظر می‌ماند و به محض ورود نرخ تبدیل می‌شود. تسعیر ارز حالا فقط ارزش پایهٔ وجه نقد، دریافتنی‌ها و پرداختنی‌ها را تغییر می‌دهد — دارایی ثابت به بهای تمام‌شده می‌ماند.",
                    "es": "Cada asiento guarda ahora también su valor en su moneda base, al tipo de su fecha (o al que escriba en el asiento). Elija «Todas las monedas» en el libro mayor o en los informes para ver dólares, euros y libras bien sumados; una sola moneda sigue funcionando como antes. Un asiento sin tipo para su fecha espera y se convierte en cuanto se añade uno. La revaluación cambia ahora solo el valor base de caja, cobros y pagos; el inmovilizado queda al coste.",
                    "ar": "يحفظ كل قيد الآن قيمته بعملتك الأساسية أيضًا، بسعر تاريخه (أو بالسعر الذي تكتبه على القيد). اختر «كل العملات» في دفتر الأستاذ أو تقارير الإدارة لترى الدولار واليورو والجنيه مجموعة بشكل صحيح؛ وعرض العملة الواحدة يعمل كما كان. القيد الذي لا سعر لتاريخه ينتظر ويُحوَّل فور إضافة سعر. وإعادة التقييم تغيّر الآن القيمة الأساسية فقط للنقد والذمم المدينة والدائنة — والأصول الثابتة تبقى بالتكلفة.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.3",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="realised-fx",
                page="invoices",
                roles=("owner", "cfo", "accountant"),
                title={
                    "en": "Exchange gains and losses when a foreign invoice is paid",
                    "fa": "سود و زیان تسعیر ارز هنگام تسویهٔ فاکتور ارزی",
                    "es": "Diferencias de cambio al cobrar o pagar una factura en divisa",
                    "ar": "أرباح وخسائر الصرف عند سداد فاتورة بعملة أجنبية",
                },
                body={
                    "en": "A dollar invoice booked at one rate and paid at another now clears the receivable (or payable) at the rate it was booked at, takes the money in at the day's rate, and posts the difference to Foreign exchange gains or losses — the payment message tells you how much. Part payments, credit notes and overpayments work the same way, and reversing a payment undoes its gain or loss too.",
                    "fa": "فاکتور دلاری که با یک نرخ ثبت و با نرخ دیگری تسویه شود، از این پس حساب دریافتنی (یا پرداختنی) را با همان نرخ ثبت می‌بندد، وجه را با نرخ روز می‌گیرد و تفاوت را در سود یا زیان تسعیر ارز ثبت می‌کند — پیام دریافت مبلغ آن را می‌گوید. پرداخت‌های جزئی، اعلامیه‌های بستانکار و اضافه‌پرداخت هم همین‌طورند و برگشت یک پرداخت، سود یا زیان آن را هم برمی‌گرداند.",
                    "es": "Una factura en dólares registrada a un tipo y cobrada a otro salda ahora el cobro (o pago) al tipo con que se registró, recibe el dinero al tipo del día y lleva la diferencia a diferencias de cambio positivas o negativas; el mensaje del cobro indica el importe. Los pagos parciales, las notas de crédito y los sobrepagos funcionan igual, y revertir un pago deshace también su diferencia.",
                    "ar": "الفاتورة بالدولار المسجّلة بسعر والمسددة بسعر آخر تُقفل الآن الذمم المدينة (أو الدائنة) بالسعر الذي سُجّلت به، وتستلم المال بسعر اليوم، وتسجّل الفرق في أرباح أو خسائر الصرف — وتُخبرك رسالة الدفعة بالمبلغ. وتعمل الدفعات الجزئية وإشعارات الدائن والدفعات الزائدة بالطريقة نفسها، وعكس الدفعة يُلغي ربحها أو خسارتها أيضًا.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.4",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="base-currency-everywhere",
                page="dashboard",
                page_by_role={"personal": "personal-dashboard"},
                roles=("owner", "cfo", "accountant", "viewer", "personal"),
                title={
                    "en": "One total for all your currencies",
                    "fa": "یک جمع برای همهٔ ارزها",
                    "es": "Un solo total para todas tus monedas",
                    "ar": "مجموع واحد لكل عملاتك",
                },
                body={
                    "en": "The dashboard, cash on hand, budgets, net worth, insights and the AI accountant's answers now add up every currency at the rate each entry was posted at, in your own currency — before, dollars were added to pounds or rials as plain numbers. A bank statement is still checked against entries in its own currency. If Settings → Currency & FX says some entries are marked IRR although your books are in another currency, one click relabels them.",
                    "fa": "داشبورد، موجودی نقد، بودجه، دارایی خالص، بینش‌ها و پاسخ‌های حسابدار هوشمند حالا همهٔ ارزها را با نرخی که هر سند با آن ثبت شده، به ارز خودتان جمع می‌زنند — پیش از این دلار با ریال یا پوند مثل عدد ساده جمع می‌شد. صورت‌حساب بانک همچنان با اسناد همان ارز مقایسه می‌شود. اگر در «تنظیمات ← ارز و نرخ تبدیل» آمده که اسنادی به ریال ثبت شده‌اند در حالی که دفاتر شما به ارز دیگری است، با یک کلیک ارزشان اصلاح می‌شود.",
                    "es": "El panel, la caja, los presupuestos, el patrimonio, las alertas y las respuestas del contable IA suman ahora todas las monedas al tipo con que se registró cada asiento, en tu propia moneda; antes se sumaban dólares y libras como simples números. Un extracto bancario se sigue comparando con los asientos de su moneda. Si Ajustes → Moneda y cambio indica asientos marcados IRR aunque tus libros estén en otra moneda, un clic los corrige.",
                    "ar": "لوحة المعلومات والنقد والميزانيات وصافي الثروة والتنبيهات وإجابات المحاسب الذكي تجمع الآن كل العملات بالسعر الذي سُجّل به كل قيد، بعملتك أنت — كان الدولار يُجمع مع الجنيه أو الريال كأرقام عادية. ويبقى كشف البنك يُقارن بقيود عملته. وإن ظهر في الإعدادات ← العملة والصرف أن قيودًا معلّمة بالريال بينما دفاترك بعملة أخرى، فنقرة واحدة تصحّحها.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.5",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="correction-memory",
                page="ai-accountant",
                roles=("owner", "cfo", "accountant", "personal"),
                title={
                    "en": "The assistant remembers your corrections",
                    "fa": "دستیار اصلاح‌های شما را به خاطر می‌سپارد",
                    "es": "El asistente recuerda tus correcciones",
                    "ar": "المساعد يتذكّر تصحيحاتك",
                },
                body={
                    "en": "Post a bank-statement row to another account than the one suggested, move an entry to a different account, or change its party — and the next statement row or entry with the same wording gets your choice. You can also just say \"always put Snapp under travel\". The chat page lists what it learned (What I've learned), and any of it can be removed.",
                    "fa": "ردیف صورت‌حساب بانک را به حسابی غیر از پیشنهادی ثبت کنید، سندی را به حساب دیگری ببرید یا طرف حسابش را عوض کنید — ردیف یا سند بعدی با همان عبارت انتخاب شما را می‌گیرد. می‌توانید فقط بگویید «همیشه اسنپ رو بزن تو ایاب و ذهاب». صفحهٔ گفتگو آنچه یاد گرفته را نشان می‌دهد («آنچه یاد گرفته‌ام») و هر کدام را می‌شود حذف کرد.",
                    "es": "Contabiliza una línea del extracto en otra cuenta que la sugerida, mueve un asiento a otra cuenta o cambia su tercero, y la próxima línea o asiento con el mismo texto recibirá tu elección. También puedes decir «pon siempre Snapp en desplazamientos». La página del chat muestra lo aprendido («Lo que he aprendido») y se puede borrar.",
                    "ar": "رحّل سطر كشف البنك إلى حساب غير المقترح، أو انقل قيدًا إلى حساب آخر أو غيّر طرفه — وسيحصل السطر أو القيد التالي بالعبارة نفسها على اختيارك. ويمكنك أن تقول ببساطة «ضع سناب دائمًا تحت التنقلات». تعرض صفحة المحادثة ما تعلّمه («ما تعلّمته») ويمكن حذف أي منه.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.6",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="voice-notes",
                page="ai-accountant",
                roles=("owner", "cfo", "accountant", "personal"),
                title={
                    "en": "Say it instead of typing it",
                    "fa": "به‌جای تایپ، بگویید",
                    "es": "Dilo en vez de escribirlo",
                    "ar": "قُلها بدل كتابتها",
                },
                body={
                    "en": "The AI chat has a microphone button: tap it, say \"paid 20 pounds for lunch from the card\" (or it in Persian), tap again. The text appears in the box for you to check, then send it as usual — nothing is saved until you confirm the card. The recording itself is never kept.",
                    "fa": "گفتگوی هوشمند دکمهٔ میکروفون دارد: بزنید، بگویید «پنجاه هزار تومن نون از کارت خریدم» و دوباره بزنید. متن در کادر می‌آید تا بررسی کنید و مثل همیشه بفرستید — تا کارت را تأیید نکنید چیزی ثبت نمی‌شود. خود صدا هرگز نگه داشته نمی‌شود.",
                    "es": "El chat con IA tiene un botón de micrófono: tócalo, di «pagué 20 libras de comida con la tarjeta» y vuelve a tocarlo. El texto aparece en el cuadro para que lo revises y lo envíes como siempre; nada se guarda hasta que confirmes la tarjeta. La grabación nunca se conserva.",
                    "ar": "في محادثة الذكاء الاصطناعي زر ميكروفون: اضغطه، وقل «دفعت 20 جنيهًا للغداء بالبطاقة»، ثم اضغطه ثانية. يظهر النص في المربع لتراجعه ثم ترسله كالمعتاد — لا يُحفظ شيء حتى تؤكد البطاقة. ولا يُحتفظ بالتسجيل نفسه أبدًا.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.7",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="messenger-bot",
                page="ai-accountant",
                roles=("owner", "cfo", "accountant", "personal"),
                title={
                    "en": "Tell the assistant from Telegram or Bale",
                    "fa": "دستیار در تلگرام و بله",
                    "es": "Habla con el asistente desde Telegram o Bale",
                    "ar": "المساعد في تيليغرام وبله",
                },
                body={
                    "en": "Account menu → Telegram / Bale: link your chat once, then send the bot what you spent or earned — a line of text or a voice note — and it answers like the chat here, with Confirm and Cancel buttons on anything it would record. Nothing is saved until you tap Confirm; /new starts a new conversation and /stop unlinks the chat. (Shown once the administrator has connected a bot.)",
                    "fa": "«منوی حساب ← تلگرام / بله»: یک بار گفتگوی خود را وصل کنید، سپس خرج یا دریافتی‌تان را — یک خط متن یا پیام صوتی — برای ربات بفرستید. مثل گفتگوی اینجا پاسخ می‌دهد و روی هر چیزی که ثبت می‌کند دکمهٔ «تأیید» و «لغو» می‌گذارد. تا «تأیید» نزنید چیزی ثبت نمی‌شود؛ /new گفتگوی تازه و /stop قطع اتصال. (وقتی مدیر سرور رباتی وصل کرده باشد نمایش داده می‌شود.)",
                    "es": "Menú de la cuenta → Telegram / Bale: vincula tu chat una vez y envía al bot lo que gastaste o cobraste, en una línea o una nota de voz; responde como el chat de aquí, con botones Confirmar y Cancelar en lo que registraría. Nada se guarda hasta que pulses Confirmar; /new empieza otra conversación y /stop desvincula el chat. (Aparece cuando el administrador ha conectado un bot.)",
                    "ar": "قائمة الحساب ← تيليغرام / بله: اربط محادثتك مرة واحدة، ثم أرسل للبوت ما أنفقته أو استلمته — سطرًا أو ملاحظة صوتية — فيجيب مثل المحادثة هنا، مع زري «تأكيد» و«إلغاء» على ما سيسجّله. لا يُحفظ شيء حتى تضغط «تأكيد»؛ /new لمحادثة جديدة و/stop لإلغاء الربط. (يظهر عندما يربط المسؤول بوتًا.)",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.8",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="jalali-reports",
                page="manager",
                page_by_role={"personal": "dashboard"},
                roles=("owner", "cfo", "accountant", "manager", "viewer", "personal"),
                locales=("ir",),
                title={
                    "en": "Reports in Jalali months",
                    "fa": "گزارش‌ها با ماه‌های شمسی",
                    "es": "Informes por meses del calendario persa",
                    "ar": "التقارير بالأشهر الشمسية",
                },
                body={
                    "en": "When the display calendar is Jalali, budgets, the dashboard's monthly charts, trend and cash-flow periods, seasons, insight comparisons and the payroll year all follow Jalali months — Mehr 1405 is 23 September to 22 October — instead of splitting each month in two. The budget month picker lists Jalali months. Budgets set before this keep their Gregorian months.",
                    "fa": "وقتی تقویم نمایش شمسی است، بودجه‌ها، نمودارهای ماهانهٔ داشبورد، دوره‌های روند و جریان نقد، فصل‌ها، مقایسه‌های بینش‌ها و سال حقوق و دستمزد همه با ماه‌های شمسی حساب می‌شوند — مهر ۱۴۰۵ از ۱ تا ۳۰ مهر — نه با ماه میلادی که هر ماه را دو تکه می‌کرد. انتخاب ماه بودجه هم ماه‌های شمسی را نشان می‌دهد. بودجه‌هایی که پیش‌تر تعریف شده‌اند ماه میلادی خود را نگه می‌دارند.",
                    "es": "Con el calendario persa (jalalí) como calendario de visualización, los presupuestos, los gráficos mensuales del panel, los periodos de tendencia y de flujo de caja, las estaciones, las comparaciones de las observaciones y el año de nómina siguen los meses jalalíes (Mehr 1405 va del 23 de septiembre al 22 de octubre) en lugar de partir cada mes en dos. El selector de mes del presupuesto muestra meses jalalíes; los presupuestos anteriores conservan sus meses gregorianos.",
                    "ar": "عندما يكون تقويم العرض هو التقويم الشمسي، تتبع الموازنات والرسوم الشهرية في لوحة المعلومات وفترات الاتجاه والتدفق النقدي والفصول ومقارنات الرؤى وسنة الرواتب الأشهرَ الشمسية — مهر ١٤٠٥ من ٢٣ سبتمبر إلى ٢٢ أكتوبر — بدل تقسيم كل شهر إلى نصفين. ويعرض منتقي شهر الموازنة الأشهر الشمسية، وتحتفظ الموازنات السابقة بأشهرها الميلادية.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.9",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="cheque-lifecycle",
                page="commitments",
                roles=("owner", "cfo", "accountant", "personal"),
                locales=("ir",),
                title={
                    "en": "Cheques from receipt to clearing",
                    "fa": "گردش کامل چک، از دریافت تا وصول",
                    "es": "Cheques de la recepción al cobro",
                    "ar": "الشيكات من الاستلام إلى التحصيل",
                },
                body={
                    "en": "Installments & cheques → Actions: deposit a cheque at the bank, mark it cleared or bounced, deposit a bounced one again, hand it back, or pass a customer's cheque on to a supplier. Each step is booked through notes receivable, cheques in collection and notes payable; a cheque recorded for an invoice pays it, and a bounce reopens it. Add the 16-digit Sayad id, and you are reminded to register (or confirm) a cheque in Sayad before it falls due. History shows each cheque's journey.",
                    "fa": "«اقساط و چک‌ها ← عملیات»: چک را به بانک بسپارید، وصول یا برگشت آن را ثبت کنید، چک برگشتی را دوباره بخوابانید، عودت دهید یا چک مشتری را به تأمین‌کننده خرج کنید. هر مرحله از طریق اسناد دریافتنی، اسناد در جریان وصول و اسناد پرداختنی ثبت می‌شود؛ چکی که بابت فاکتور ثبت شود آن را تسویه می‌کند و برگشت آن فاکتور را دوباره باز می‌کند. شناسه ۱۶ رقمی صیاد را وارد کنید تا پیش از سررسید برای ثبت (یا تأیید) چک در صیاد یادآوری شود. «گردش چک» مسیر هر چک را نشان می‌دهد.",
                    "es": "Cuotas y cheques → Acciones: deposita un cheque en el banco, márcalo cobrado o rechazado, deposita de nuevo uno rechazado, devuélvelo o endosa el cheque de un cliente a un proveedor. Cada paso se registra en efectos a cobrar, cheques en gestión de cobro y efectos a pagar; un cheque registrado para una factura la paga, y si se rechaza la factura se reabre. Añade el ID Sayad de 16 dígitos y se te recordará registrar (o confirmar) el cheque en Sayad antes del vencimiento. El historial muestra el recorrido de cada cheque.",
                    "ar": "الأقساط والشيكات ← إجراءات: أودع الشيك في البنك، سجّل تحصيله أو ارتجاعه، أودع المرتجع مجددًا، أعده، أو ظهّر شيك العميل لمورد. تُقيَّد كل خطوة عبر أوراق القبض والشيكات قيد التحصيل وأوراق الدفع؛ والشيك المسجل لفاتورة يسددها، وارتجاعه يعيد فتحها. أضف معرّف صياد المكوّن من ١٦ رقمًا ليُذكّرك النظام بتسجيل الشيك (أو تأكيده) في صياد قبل استحقاقه. ويعرض السجل مسار كل شيك.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.10",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="cheque-print",
                page="commitments",
                roles=("owner", "cfo", "accountant", "personal"),
                title={
                    "en": "Print a cheque you issue",
                    "fa": "چاپ چک صادره",
                    "es": "Imprime los cheques que emites",
                    "ar": "اطبع الشيكات التي تصدرها",
                },
                body={
                    "en": "Installments & cheques → Actions → Print cheque: a PDF the size of the leaf with the date (and, on Iranian cheques, the date in words), the payee and their national id, and the amount in words and figures. Print it at 100 % with the cheque in the manual feed. Leaves differ by bank, so first print the guide on plain paper under “Cheque printing”, hold it against a cheque and adjust the positions. Each print is kept in the cheque's history.",
                    "fa": "«اقساط و چک‌ها ← عملیات ← چاپ چک»: یک PDF به اندازه برگ چک با تاریخ و تاریخ به حروف، «در وجه» و کد/شناسه ملی گیرنده، و مبلغ به حروف و به عدد. با مقیاس ۱۰۰٪ و برگ چک در سینی دستی چاپ کنید. برگ چک بانک‌ها کمی فرق دارد؛ پس اول در بخش «چاپ چک» راهنما را روی کاغذ ساده چاپ کنید، روی یک برگ چک بگذارید و جای فیلدها را تنظیم کنید. هر چاپ در «گردش چک» ثبت می‌شود.",
                    "es": "Cuotas y cheques → Acciones → Imprimir cheque: un PDF del tamaño del cheque con la fecha, el beneficiario y su documento, y el importe en letras y en cifras. Imprímelo al 100 % con el cheque en la bandeja manual. Los cheques varían según el banco: imprime primero la guía en papel normal en «Impresión de cheques», colócala sobre un cheque y ajusta las posiciones. Cada impresión queda en el historial del cheque.",
                    "ar": "الأقساط والشيكات ← إجراءات ← طباعة الشيك: ملف PDF بحجم ورقة الشيك فيه التاريخ، والمستفيد ورقمه الوطني، والمبلغ كتابةً وبالأرقام. اطبعه بمقياس 100٪ والشيك في درج التغذية اليدوية. تختلف أوراق الشيكات بين البنوك، فاطبع الدليل أولًا على ورق عادي من «طباعة الشيكات»، وضعه على شيك واضبط المواضع. تُحفظ كل طباعة في سجل الشيك.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.11",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="uk-frs102-statements",
                page="manager",
                roles=("owner", "cfo", "accountant", "manager", "viewer"),
                locales=("uk",),
                title={
                    "en": "UK statements, complete",
                    "fa": "صورت‌های مالی بریتانیا، کامل",
                    "es": "Estados financieros del Reino Unido, completos",
                    "ar": "القوائم المالية البريطانية كاملة",
                },
                body={
                    "en": "The FRS 102 statements now come entirely from your books: every account is on the balance sheet (accrued income and prepayments were missing), sales returns reduce turnover, depreciation and amortisation are stated, a revaluation shows as other comprehensive income, the changes in equity list shares issued, dividends and transfers for both years and close on the balance sheet, and the cash flow shows dividends paid, directors' loans and a reconciliation of operating profit to cash generated from operations.",
                    "fa": "صورت‌های مالی FRS 102 اکنون کاملاً از دفاتر شما ساخته می‌شوند: همه حساب‌ها در ترازنامه هستند، برگشت از فروش از درآمد کم می‌شود، استهلاک نمایش داده می‌شود، تجدید ارزیابی در سود جامع دیگر می‌آید، تغییرات حقوق مالکانه صدور سهام، سود سهام و انتقال بین اندوخته‌ها را برای هر دو سال نشان می‌دهد و با ترازنامه می‌خواند، و صورت جریان نقد سود سهام پرداختی، وام مدیران و تطبیق سود عملیاتی با نقد حاصل از عملیات را دارد.",
                    "es": "Los estados FRS 102 salen ahora por completo de tu contabilidad: todas las cuentas están en el balance, las devoluciones reducen la cifra de negocios, se indica la amortización, una revalorización aparece como otro resultado integral, los cambios en el patrimonio muestran emisiones, dividendos y traspasos de ambos años y cuadran con el balance, y el flujo de caja muestra dividendos pagados, préstamos de administradores y la conciliación del resultado de explotación con la caja generada.",
                    "ar": "تُبنى قوائم FRS 102 الآن بالكامل من دفاترك: كل الحسابات في الميزانية، ومردودات المبيعات تخفض الإيرادات، ويُفصح عن الاستهلاك والإطفاء، وتظهر إعادة التقييم في الدخل الشامل الآخر، وتعرض التغيرات في حقوق الملكية إصدار الأسهم والتوزيعات والتحويلات لكلا العامين وتطابق الميزانية، وتعرض التدفقات النقدية التوزيعات المدفوعة وقروض المديرين وتسوية الربح التشغيلي مع النقد الناتج من العمليات.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.12",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="ai-approvals",
                page="settings",
                page_by_role={"cfo": "ai-accountant", "manager": "ai-accountant"},
                roles=("owner", "cfo", "manager"),
                title={
                    "en": "A second pair of eyes on large AI entries",
                    "fa": "تأیید نفر دوم برای سندهای بزرگ دستیار",
                    "es": "Una segunda aprobación para los asientos grandes de la IA",
                    "ar": "موافقة شخص ثانٍ على قيود المساعد الكبيرة",
                },
                body={
                    "en": "Settings → AI approvals: set an amount, and anything the assistant drafts at or above it waits for someone other than the person who asked — an owner, CFO or manager — to approve it in the AI chat (Waiting for approval) before it is recorded. The assistant also no longer drafts anything dated in a closed period, and it stops after a set number of steps in one message.",
                    "fa": "«تنظیمات ← تأیید سندهای دستیار»: یک مبلغ تعیین کنید؛ هر سندی که دستیار با این مبلغ یا بیشتر پیش‌نویس کند، تا کسی غیر از درخواست‌کننده — مالک، مدیر مالی یا مدیر — آن را در گفتگوی هوش مصنوعی («در انتظار تأیید») تأیید نکند ثبت نمی‌شود. دستیار دیگر برای دوره‌های بسته سند پیش‌نویس نمی‌کند و در یک پیام بیش از تعداد معینی مرحله انجام نمی‌دهد.",
                    "es": "Ajustes → Aprobaciones de la IA: fija un importe y todo lo que el asistente prepare por ese importe o más esperará a que otra persona —propietario, director financiero o gerente— lo apruebe en el chat de IA (Pendientes de aprobación) antes de registrarse. Además, el asistente ya no prepara nada con fecha en un periodo cerrado y se detiene tras un número fijo de pasos por mensaje.",
                    "ar": "الإعدادات ← موافقات المساعد: حدّد مبلغًا، وكل ما يعدّه المساعد بهذا المبلغ أو أكثر ينتظر موافقة شخص غير من طلبه — مالك أو مدير مالي أو مدير — في محادثة الذكاء الاصطناعي («بانتظار الموافقة») قبل تسجيله. ولم يعد المساعد يعدّ قيودًا بتاريخ في فترة مغلقة، ويتوقف بعد عدد محدد من الخطوات في الرسالة الواحدة.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.13",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="language-switch",
                title={
                    "en": "Changing language now sticks",
                    "fa": "تغییر زبان اکنون ماندگار است",
                    "es": "El cambio de idioma ahora se mantiene",
                    "ar": "تغيير اللغة يبقى الآن",
                },
                body={
                    "en": "The language menu at the top now saves your choice to your profile, so it stays after you sign in again, and the page you are on — tables and charts included — redraws in the new language straight away. The app also loads faster: each language is fetched only when you use it.",
                    "fa": "منوی زبان در بالای صفحه اکنون انتخاب شما را در نمایه‌تان ذخیره می‌کند، پس پس از ورود دوباره هم می‌ماند، و صفحه‌ای که در آن هستید — همراه با جدول‌ها و نمودارها — بی‌درنگ به زبان تازه نمایش داده می‌شود. برنامه هم سریع‌تر بارگذاری می‌شود: هر زبان فقط وقتی به کارش می‌برید دریافت می‌شود.",
                    "es": "El menú de idioma de la parte superior guarda ahora tu elección en tu perfil, así que se mantiene al volver a iniciar sesión, y la página abierta —tablas y gráficos incluidos— se vuelve a dibujar en el nuevo idioma al momento. La aplicación también carga más rápido: cada idioma se descarga solo cuando lo usas.",
                    "ar": "قائمة اللغة في الأعلى تحفظ الآن اختيارك في ملفك الشخصي، فيبقى بعد تسجيل الدخول مجددًا، ويُعاد رسم الصفحة المفتوحة — بما فيها الجداول والرسوم — باللغة الجديدة فورًا. كما يُحمَّل التطبيق أسرع: تُنزَّل كل لغة فقط عند استخدامها.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.14",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="dashboard-aging-order",
                page="dashboard",
                roles=("owner", "cfo", "accountant", "viewer"),
                title={
                    "en": "A faster dashboard, and receipts always clear their sales",
                    "fa": "داشبورد سریع‌تر، و دریافت‌ها همیشه فروش خود را تسویه می‌کنند",
                    "es": "Un panel más rápido, y los cobros siempre saldan sus ventas",
                    "ar": "لوحة أسرع، والمقبوضات تسوّي مبيعاتها دائمًا",
                },
                body={
                    "en": "The dashboard now adds up your books in the database, so it opens noticeably faster on a large ledger. The receivables and payables aging now takes entries in date order: a receipt entered before the sale it pays — an imported bank statement, say — used to be missed and left that sale showing as overdue.",
                    "fa": "داشبورد اکنون جمع‌های دفاتر را در پایگاه داده می‌گیرد و روی دفاتر بزرگ به‌طور محسوسی سریع‌تر باز می‌شود. سنی‌بندی دریافتنی‌ها و پرداختنی‌ها اکنون سندها را به ترتیب تاریخ می‌خواند: دریافتی که پیش از فروشِ مربوطش ثبت شده بود — مثلاً از صورت‌حساب بانکی واردشده — نادیده می‌ماند و آن فروش سررسیدگذشته نشان داده می‌شد.",
                    "es": "El panel suma ahora la contabilidad en la base de datos, así que se abre bastante más rápido con muchos asientos. La antigüedad de cobros y pagos toma ahora los asientos por fecha: un cobro registrado antes de la venta que paga —por ejemplo, de un extracto bancario importado— se pasaba por alto y la venta seguía apareciendo como vencida.",
                    "ar": "تجمع اللوحة الآن أرصدة دفاترك في قاعدة البيانات، فتُفتح أسرع بشكل ملحوظ مع الدفاتر الكبيرة. وتقرأ أعمار الذمم المدينة والدائنة الآن القيود بترتيب التاريخ: القبض المسجّل قبل البيع الذي يسدّده — من كشف بنكي مستورد مثلًا — كان يُغفل ويبقى ذلك البيع ظاهرًا كمتأخر.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.28.15",
        date="2026-09-28",
        highlights=(
            Highlight(
                key="statement-review-cards",
                page="ai-accountant",
                roles=("owner", "cfo", "accountant", "personal"),
                title={
                    "en": "Statement review drafts every fix again",
                    "fa": "بازبینی صورت‌حساب دوباره برای هر مغایرت سند پیشنهاد می‌دهد",
                    "es": "La revisión del extracto vuelve a proponer cada corrección",
                    "ar": "مراجعة الكشف تقترح كل تصحيح من جديد",
                },
                body={
                    "en": "When you asked the assistant to review a bank statement, it could refuse to draft the entry for a row the books were missing, saying the amount didn't match — the statement's reference number was being read as an amount. Each draft is now checked against the statement row itself.",
                    "fa": "وقتی از دستیار می‌خواستید صورت‌حساب بانکی را بازبینی کند، گاهی برای ردیفی که در دفاتر نبود سند پیشنهاد نمی‌داد و می‌گفت مبلغ نمی‌خواند — شناسه صورت‌حساب به‌جای مبلغ خوانده می‌شد. اکنون هر پیش‌نویس با خود ردیف صورت‌حساب سنجیده می‌شود.",
                    "es": "Al pedir al asistente que revisara un extracto bancario, a veces se negaba a preparar el asiento de una fila que faltaba en la contabilidad diciendo que el importe no cuadraba: leía la referencia del extracto como un importe. Ahora cada borrador se comprueba con la propia fila del extracto.",
                    "ar": "عند طلب مراجعة كشف بنكي، كان المساعد أحيانًا يرفض إعداد قيد لصف غير موجود في الدفاتر قائلًا إن المبلغ لا يطابق — إذ كان يقرأ رقم مرجع الكشف كمبلغ. الآن يُطابَق كل مسودة مع صف الكشف نفسه.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.29",
        date="2026-09-29",
        highlights=(
            Highlight(
                key="ai-review-queue",
                page="settings",
                roles=("owner",),
                title={
                    "en": "See how the assistant really does",
                    "fa": "ببینید دستیار واقعاً چطور کار می‌کند",
                    "es": "Mira cómo trabaja de verdad el asistente",
                    "ar": "شاهد كيف يعمل المساعد فعلًا",
                },
                body={
                    "en": "Settings → AI review: about one turn in ten of your team's conversations with the assistant (web chat and the Telegram/Bale bots) is kept for you — the question, the answer, the tools it used and the cards it made. Mark each one good or needs work, with a note, or download it as a test case. They stay inside your company, only you see them, and they go with the conversation or after 90 days. You can switch it off there.",
                    "fa": "«تنظیمات ← بازبینی دستیار»: حدود یک پیام از هر ده پیامِ گفتگوی همکارانتان با دستیار (گفتگوی وب و ربات‌های تلگرام/بله) برای شما نگه داشته می‌شود — پرسش، پاسخ، ابزارهایی که به کار برد و کارت‌هایی که ساخت. هرکدام را «خوب» یا «نیاز به اصلاح» با یادداشت علامت بزنید یا به‌عنوان نمونه آزمون دریافت کنید. درون شرکت شما می‌مانند، فقط شما می‌بینید، و با حذف گفتگو یا پس از ۹۰ روز پاک می‌شوند. همان‌جا می‌توانید خاموشش کنید.",
                    "es": "Ajustes → Revisión de la IA: se guarda para ti aproximadamente uno de cada diez turnos de las conversaciones de tu equipo con el asistente (chat web y bots de Telegram/Bale): la pregunta, la respuesta, las herramientas que usó y las tarjetas que creó. Márcalos como bien o mejorables, con una nota, o descárgalos como caso de prueba. Se quedan en tu empresa, solo tú los ves y se borran con la conversación o a los 90 días. Puedes desactivarlo allí.",
                    "ar": "الإعدادات ← مراجعة المساعد: يُحفظ لك نحو دور من كل عشرة من محادثات فريقك مع المساعد (المحادثة على الويب وروبوتات تيليجرام/بله) — السؤال والإجابة والأدوات التي استخدمها والبطاقات التي أنشأها. ضع على كل منها «جيد» أو «يحتاج تحسينًا» مع ملاحظة، أو نزّلها كحالة اختبار. تبقى داخل شركتك، ولا يراها غيرك، وتُحذف مع المحادثة أو بعد 90 يومًا. يمكنك إيقافها من هناك.",
                },
            ),
            Highlight(
                key="ai-review-notice",
                page="ai-accountant",
                roles=("cfo", "accountant"),
                title={
                    "en": "Some conversations are kept for review",
                    "fa": "برخی گفتگوها برای بازبینی نگه داشته می‌شوند",
                    "es": "Algunas conversaciones se guardan para revisión",
                    "ar": "تُحفظ بعض المحادثات للمراجعة",
                },
                body={
                    "en": "To improve the assistant, about one in ten of your messages to it — with its answer — is kept for your company's owner to review. It stays inside your company and goes when you delete the conversation or after 90 days.",
                    "fa": "برای بهتر شدن دستیار، حدود یکی از هر ده پیام شما به آن — همراه با پاسخش — برای بازبینی مالک شرکت نگه داشته می‌شود. درون شرکت شما می‌ماند و با حذف گفتگو یا پس از ۹۰ روز پاک می‌شود.",
                    "es": "Para mejorar el asistente, aproximadamente uno de cada diez de tus mensajes —con su respuesta— se guarda para que lo revise el propietario de tu empresa. Se queda en tu empresa y se borra cuando eliminas la conversación o a los 90 días.",
                    "ar": "لتحسين المساعد، تُحفظ نحو رسالة من كل عشر من رسائلك إليه — مع إجابته — ليراجعها مالك شركتك. تبقى داخل شركتك وتُحذف عند حذفك المحادثة أو بعد 90 يومًا.",
                },
            ),
        ),
    ),
    Release(
        version="2026.09.29.1",
        date="2026-09-29",
        highlights=(
            Highlight(
                key="budgets-edit-roll",
                page="dashboard",
                roles=("owner", "cfo", "accountant"),
                title={
                    "en": "Budgets you can edit and carry forward",
                    "fa": "بودجه‌هایی که ویرایش و به ماه بعد منتقل می‌شوند",
                    "es": "Presupuestos que puedes editar y trasladar",
                    "ar": "ميزانيات قابلة للتعديل والترحيل",
                },
                body={
                    "en": "On the dashboard's Budget vs actual, each line now has Edit and Delete, and Copy to next month… carries the month's budgets forward — as they are or changed by a percentage — without touching categories next month already has. It follows your company's calendar, Jalali months included.",
                    "fa": "در «بودجه در برابر واقعی» داشبورد، هر ردیف اکنون «ویرایش» و «حذف» دارد و «کپی به ماه بعد…» بودجه‌های ماه را — همان‌طور یا با درصدی تغییر — به ماه بعد می‌برد، بی‌آنکه به دسته‌هایی که ماه بعد از قبل دارد دست بزند. با تقویم شرکت شما، از جمله ماه‌های شمسی، کار می‌کند.",
                    "es": "En Presupuesto frente a real del panel, cada línea tiene ahora Editar y Eliminar, y Copiar al mes siguiente… traslada los presupuestos del mes —tal cual o cambiados en un porcentaje— sin tocar las categorías que el mes siguiente ya tiene. Sigue el calendario de tu empresa, meses jalalíes incluidos.",
                    "ar": "في «الميزانية مقابل الفعلي» على اللوحة، لكل بند الآن «تعديل» و«حذف»، و«نسخ إلى الشهر التالي…» ينقل ميزانيات الشهر — كما هي أو بتغيير نسبة — دون المساس بالبنود الموجودة في الشهر التالي. يتبع تقويم شركتك، بما في ذلك الأشهر الشمسية.",
                },
            ),
            Highlight(
                key="project-budgets",
                page="time",
                roles=("owner", "cfo", "accountant"),
                title={
                    "en": "A budget for each project",
                    "fa": "بودجه برای هر پروژه",
                    "es": "Un presupuesto por proyecto",
                    "ar": "ميزانية لكل مشروع",
                },
                body={
                    "en": "Time → Projects and budgets: give a project a budget in hours, fees, or both, and see how much is used — every hour logged, and the fees they come to at the rate billed (or today's rate for time not yet invoiced). The bell warns at 85 % and when a project goes over.",
                    "fa": "«زمان ← پروژه‌ها و بودجه»: برای پروژه بودجه ساعت، حق‌الزحمه یا هر دو بگذارید و ببینید چقدرش مصرف شده — همه ساعت‌های ثبت‌شده و حق‌الزحمه آن‌ها به نرخ صورت‌حساب (یا نرخ امروز برای زمانی که هنوز صورت‌حساب نشده). زنگ اعلان در ۸۵٪ و هنگام عبور از بودجه خبر می‌دهد.",
                    "es": "Tiempo → Proyectos y presupuestos: da a un proyecto un presupuesto en horas, honorarios o ambos y mira cuánto se ha usado: cada hora registrada y los honorarios a la tarifa facturada (o a la de hoy si aún no se ha facturado). La campana avisa al 85 % y cuando un proyecto se pasa.",
                    "ar": "الوقت ← المشاريع والميزانيات: خصّص للمشروع ميزانية بالساعات أو الأتعاب أو كليهما، وشاهد المستخدم منها — كل ساعة مسجلة والأتعاب بسعر الفوترة (أو بسعر اليوم لما لم يُفوتر بعد). ينبّهك الجرس عند 85٪ وعند تجاوز المشروع ميزانيته.",
                },
            ),
        ),
    ),
)


def version_tuple(version: str | None) -> tuple[int, ...]:
    if not version:
        return ()
    out = []
    for part in str(version).split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def whats_new_for(role: str | None, last_seen: str | None, *, include_all: bool = False,
                  locale: str | None = None) -> dict[str, Any]:
    """Releases the user hasn't seen, highlights filtered to their role.

    ``last_seen=None`` (a user from before this feature) sees only the
    current release, not the whole history. ``include_all`` returns every
    release regardless (the Settings "What's new" button).
    """
    seen_t = version_tuple(last_seen)
    releases = []
    for rel in sorted(RELEASES, key=lambda r: version_tuple(r.version), reverse=True):
        if not include_all:
            if last_seen is None and rel.version != CURRENT_RELEASE:
                continue
            if last_seen is not None and version_tuple(rel.version) <= seen_t:
                continue
        items = [h for h in (hl.for_role(role, locale) for hl in rel.highlights) if h]
        if items:
            releases.append({"version": rel.version, "date": rel.date, "highlights": items})
    return {
        "current_release": CURRENT_RELEASE,
        "last_seen_release": last_seen,
        "seen": not releases if not include_all else (version_tuple(last_seen) >= version_tuple(CURRENT_RELEASE)),
        "releases": releases,
    }
