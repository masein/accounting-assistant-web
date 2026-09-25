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

CURRENT_RELEASE = "2026.09.25.1"


@dataclass(frozen=True)
class Highlight:
    key: str
    title: dict[str, str]
    body: dict[str, str]
    page: str | None = None                          # SPA page "Show me" opens
    page_by_role: dict[str, str] = field(default_factory=dict)
    roles: tuple[str, ...] | None = None              # None = everyone

    def for_role(self, role: str | None) -> dict[str, Any] | None:
        r = (role or "owner").lower()
        if self.roles is not None and r not in self.roles:
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


def whats_new_for(role: str | None, last_seen: str | None, *, include_all: bool = False) -> dict[str, Any]:
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
        items = [h for h in (hl.for_role(role) for hl in rel.highlights) if h]
        if items:
            releases.append({"version": rel.version, "date": rel.date, "highlights": items})
    return {
        "current_release": CURRENT_RELEASE,
        "last_seen_release": last_seen,
        "seen": not releases if not include_all else (version_tuple(last_seen) >= version_tuple(CURRENT_RELEASE)),
        "releases": releases,
    }
