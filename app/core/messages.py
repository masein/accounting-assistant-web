"""API error messages in the reader's language.

Endpoints raise ``HTTPException(detail="Invoice not found")`` in English, and
the page shows ``detail`` as it comes — so a Persian user read "Invalid
username or password". The exception handler in app/main.py passes every
string ``detail`` through ``localize_detail`` with the request's language:
the ``X-UI-Language`` header the page sends with every call (it knows the
language before sign-in too), else ``Accept-Language``, else English.

``EXACT`` maps a whole English message to its translations; ``PATTERNS``
matches a message built at run time ("Account not found: 6112") and fills
the translation with its parts. A message with no entry stays English, so
untranslated text is never lost. tests/test_api_messages.py keeps every entry
matched to a message the code really raises, in all three languages.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

LANGS = ("fa", "es", "ar")
_RTL = ("fa", "ar")


def _iso(value: str, lang: str) -> str:
    """A value inside a right-to-left sentence keeps its own direction."""
    return f"⁨{value}⁩" if lang in _RTL else value


EXACT: dict[str, dict[str, str]] = {
    # ── shared ──
    "Request validation failed": {"fa": "درخواست نامعتبر است", "es": "La solicitud no es válida", "ar": "الطلب غير صالح"},
    "Internal server error": {"fa": "خطای داخلی سرور", "es": "Error interno del servidor", "ar": "خطأ داخلي في الخادم"},
    "Too many requests. Try again later.": {"fa": "درخواست‌ها بیش از حد بوده است. بعداً دوباره امتحان کنید.",
                                            "es": "Demasiadas solicitudes. Inténtalo más tarde.",
                                            "ar": "طلبات كثيرة جداً. حاول لاحقاً."},
    # ── sign-in, accounts, two-factor ──
    "Invalid username or password": {"fa": "نام کاربری یا رمز عبور نادرست است", "es": "Usuario o contraseña incorrectos",
                                     "ar": "اسم المستخدم أو كلمة المرور غير صحيحة"},
    "A valid email address is required.": {"fa": "یک نشانی ایمیل معتبر لازم است.", "es": "Se necesita una dirección de correo válida.",
                                           "ar": "يلزم عنوان بريد إلكتروني صالح."},
    "Choose a password that is not the default and not your username.": {
        "fa": "رمز عبوری انتخاب کنید که نه رمز پیش‌فرض باشد و نه نام کاربری‌تان.",
        "es": "Elige una contraseña que no sea la predeterminada ni tu nombre de usuario.",
        "ar": "اختر كلمة مرور ليست الافتراضية ولا اسم المستخدم."},
    "Confirm your email address before signing in. Check your inbox for the link.": {
        "fa": "پیش از ورود، نشانی ایمیل خود را تأیید کنید. پیوند تأیید را در صندوق ورودی ببینید.",
        "es": "Confirma tu correo antes de iniciar sesión. Busca el enlace en tu bandeja de entrada.",
        "ar": "أكّد بريدك الإلكتروني قبل تسجيل الدخول. ابحث عن الرابط في صندوق الوارد."},
    "Current password is incorrect.": {"fa": "رمز عبور فعلی نادرست است.", "es": "La contraseña actual no es correcta.",
                                       "ar": "كلمة المرور الحالية غير صحيحة."},
    "Password is incorrect.": {"fa": "رمز عبور نادرست است.", "es": "La contraseña no es correcta.", "ar": "كلمة المرور غير صحيحة."},
    "Self-signup is disabled on this server.": {"fa": "ثبت‌نام در این سرور غیرفعال است.",
                                                "es": "El registro está desactivado en este servidor.",
                                                "ar": "التسجيل الذاتي معطّل على هذا الخادم."},
    "Sign-in timed out. Enter your password again.": {"fa": "مهلت ورود تمام شد. رمز عبور را دوباره وارد کنید.",
                                                      "es": "El inicio de sesión caducó. Vuelve a introducir tu contraseña.",
                                                      "ar": "انتهت مهلة تسجيل الدخول. أدخل كلمة المرور مرة أخرى."},
    "Start the setup again — scan a new code first.": {"fa": "راه‌اندازی را از نو شروع کنید؛ ابتدا یک کد تازه اسکن کنید.",
                                                       "es": "Vuelve a empezar la configuración: primero escanea un código nuevo.",
                                                       "ar": "ابدأ الإعداد من جديد — امسح رمزاً جديداً أولاً."},
    "That code is not right.": {"fa": "این کد درست نیست.", "es": "Ese código no es correcto.", "ar": "هذا الرمز غير صحيح."},
    "That code is not right. Check the time on your phone and try again.": {
        "fa": "این کد درست نیست. ساعت گوشی را بررسی کنید و دوباره امتحان کنید.",
        "es": "Ese código no es correcto. Revisa la hora de tu teléfono e inténtalo de nuevo.",
        "ar": "هذا الرمز غير صحيح. تحقّق من الوقت في هاتفك وحاول مرة أخرى."},
    "That code was already used. Wait for the next one.": {"fa": "این کد قبلاً استفاده شده است. منتظر کد بعدی بمانید.",
                                                           "es": "Ese código ya se usó. Espera al siguiente.",
                                                           "ar": "استُخدم هذا الرمز من قبل. انتظر الرمز التالي."},
    "The new password must differ from the current one.": {"fa": "رمز عبور جدید باید با رمز فعلی فرق داشته باشد.",
                                                           "es": "La nueva contraseña debe ser distinta de la actual.",
                                                           "ar": "يجب أن تختلف كلمة المرور الجديدة عن الحالية."},
    "This company account is suspended": {"fa": "حساب این شرکت معلق شده است", "es": "La cuenta de esta empresa está suspendida",
                                          "ar": "حساب هذه الشركة معلّق"},
    "Too many attempts. Try again later.": {"fa": "تلاش‌ها بیش از حد بوده است. بعداً دوباره امتحان کنید.",
                                            "es": "Demasiados intentos. Inténtalo más tarde.", "ar": "محاولات كثيرة جداً. حاول لاحقاً."},
    "Too many login attempts. Try again later.": {"fa": "تلاش‌های ورود بیش از حد بوده است. بعداً دوباره امتحان کنید.",
                                                  "es": "Demasiados intentos de inicio de sesión. Inténtalo más tarde.",
                                                  "ar": "محاولات دخول كثيرة جداً. حاول لاحقاً."},
    "Too many sign-up attempts. Try again later.": {"fa": "تلاش‌های ثبت‌نام بیش از حد بوده است. بعداً دوباره امتحان کنید.",
                                                    "es": "Demasiados intentos de registro. Inténtalo más tarde.",
                                                    "ar": "محاولات تسجيل كثيرة جداً. حاول لاحقاً."},
    "Too many wrong codes. Try again later.": {"fa": "کدهای نادرست بیش از حد. بعداً دوباره امتحان کنید.",
                                               "es": "Demasiados códigos incorrectos. Inténtalo más tarde.",
                                               "ar": "رموز خاطئة كثيرة جداً. حاول لاحقاً."},
    "Two-factor sign-in is already on.": {"fa": "ورود دومرحله‌ای از قبل فعال است.",
                                          "es": "La verificación en dos pasos ya está activada.", "ar": "التحقق بخطوتين مفعّل بالفعل."},
    "Two-factor sign-in is already on. Turn it off first to move it to a new phone.": {
        "fa": "ورود دومرحله‌ای از قبل فعال است. برای انتقال به گوشی جدید، اول آن را خاموش کنید.",
        "es": "La verificación en dos pasos ya está activada. Desactívala primero para pasarla a un teléfono nuevo.",
        "ar": "التحقق بخطوتين مفعّل بالفعل. أوقفه أولاً لنقله إلى هاتف جديد."},
    "Two-factor sign-in is not on.": {"fa": "ورود دومرحله‌ای فعال نیست.", "es": "La verificación en dos pasos no está activada.",
                                      "ar": "التحقق بخطوتين غير مفعّل."},
    "Unsupported language": {"fa": "این زبان پشتیبانی نمی‌شود", "es": "Idioma no admitido", "ar": "لغة غير مدعومة"},
    "User not found": {"fa": "کاربر پیدا نشد", "es": "Usuario no encontrado", "ar": "المستخدم غير موجود"},
    # ── journals and attachments ──
    "Transaction not found": {"fa": "سند پیدا نشد", "es": "Asiento no encontrado", "ar": "القيد غير موجود"},
    "Attachment file is missing": {"fa": "فایل پیوست موجود نیست", "es": "Falta el archivo adjunto", "ar": "ملف المرفق مفقود"},
    "Attachment is already linked to a transaction": {"fa": "این پیوست قبلاً به سندی متصل شده است",
                                                      "es": "El adjunto ya está vinculado a un asiento", "ar": "المرفق مرتبط بقيد بالفعل"},
    "Attachment is empty.": {"fa": "پیوست خالی است.", "es": "El adjunto está vacío.", "ar": "المرفق فارغ."},
    "Attachment not found": {"fa": "پیوست پیدا نشد", "es": "Adjunto no encontrado", "ar": "المرفق غير موجود"},
    "Attachment too large. Max size is 8 MB.": {"fa": "پیوست بیش از حد بزرگ است. حداکثر حجم ۸ مگابایت است.",
                                                "es": "El adjunto es demasiado grande. El máximo es 8 MB.",
                                                "ar": "المرفق كبير جداً. الحد الأقصى 8 ميغابايت."},
    "Bank not found for fee calculation.": {"fa": "بانک برای محاسبه کارمزد پیدا نشد.",
                                            "es": "No se encontró el banco para calcular la comisión.",
                                            "ar": "لم يُعثر على البنك لحساب الرسوم."},
    "File too large (max 20MB)": {"fa": "فایل بیش از حد بزرگ است (حداکثر ۲۰ مگابایت)", "es": "Archivo demasiado grande (máx. 20 MB)",
                                  "ar": "الملف كبير جداً (الحد 20 ميغابايت)"},
    "Only .xlsx/.xls files are supported": {"fa": "فقط فایل‌های ⁦.xlsx/.xls⁩ پشتیبانی می‌شوند",
                                            "es": "Solo se admiten archivos .xlsx/.xls", "ar": "لا تُقبل إلا ملفات ⁦.xlsx/.xls⁩"},
    "Unsupported file type. Use JPG, PNG, WEBP, PDF, CSV, TSV, XLS, or XLSX.": {
        "fa": "نوع فایل پشتیبانی نمی‌شود. از JPG، PNG، WEBP، PDF، CSV، TSV، XLS یا XLSX استفاده کنید.",
        "es": "Tipo de archivo no admitido. Usa JPG, PNG, WEBP, PDF, CSV, TSV, XLS o XLSX.",
        "ar": "نوع الملف غير مدعوم. استخدم JPG أو PNG أو WEBP أو PDF أو CSV أو TSV أو XLS أو XLSX."},
    "Upload expired or not found. Please re-upload the file.": {
        "fa": "بارگذاری منقضی شده یا پیدا نشد. فایل را دوباره بارگذاری کنید.",
        "es": "La subida caducó o no se encontró. Vuelve a subir el archivo.",
        "ar": "انتهت صلاحية الرفع أو لم يُعثر عليه. ارفع الملف مرة أخرى."},
    "bank_id must reference an existing bank entity.": {"fa": "بانک انتخاب‌شده وجود ندارد.", "es": "El banco elegido no existe.",
                                                        "ar": "البنك المحدد غير موجود."},
    "bank_name or bank_id is required.": {"fa": "یک بانک انتخاب کنید.", "es": "Elige un banco.", "ar": "اختر بنكاً."},
    # ── invoices ──
    "Invoice not found": {"fa": "فاکتور پیدا نشد", "es": "Factura no encontrada", "ar": "الفاتورة غير موجودة"},
    "A cheque paid this invoice. Mark it bounced or returned under Installments & cheques first.": {
        "fa": "این فاکتور با چک پرداخت شده است. ابتدا در «اقساط و چک‌ها» چک را برگشتی یا عودت‌شده علامت بزنید.",
        "es": "Un cheque pagó esta factura. Márcalo primero como rechazado o devuelto en Cuotas y cheques.",
        "ar": "دُفعت هذه الفاتورة بشيك. علّمه أولاً كمرتجع أو معاد في الأقساط والشيكات."},
    "This payment is a cheque. Mark the cheque bounced or returned under Installments & cheques "
    "— that reopens the invoice and moves the cheque out of the books.": {
        "fa": "این پرداخت یک چک است. در «اقساط و چک‌ها» چک را برگشتی یا عودت‌شده علامت بزنید؛ فاکتور دوباره باز و چک از دفاتر خارج می‌شود.",
        "es": "Este pago es un cheque. Márcalo como rechazado o devuelto en Cuotas y cheques: la factura se reabre y el cheque sale de los libros.",
        "ar": "هذه الدفعة شيك. علّمه كمرتجع أو معاد في الأقساط والشيكات — فتُعاد فتح الفاتورة ويخرج الشيك من الدفاتر."},
    "Entry has zero amount.": {"fa": "مبلغ سند صفر است.", "es": "El asiento tiene importe cero.", "ar": "مبلغ القيد صفر."},
    "File is empty.": {"fa": "فایل خالی است.", "es": "El archivo está vacío.", "ar": "الملف فارغ."},
    "File too large. Max size is 10 MB.": {"fa": "فایل بیش از حد بزرگ است. حداکثر حجم ۱۰ مگابایت است.",
                                           "es": "Archivo demasiado grande. El máximo es 10 MB.",
                                           "ar": "الملف كبير جداً. الحد الأقصى 10 ميغابايت."},
    "Invalid invoice status.": {"fa": "وضعیت فاکتور نامعتبر است.", "es": "Estado de factura no válido.", "ar": "حالة الفاتورة غير صالحة."},
    "Invoice amount is too large for current database schema.": {"fa": "مبلغ فاکتور بیش از حد بزرگ است.",
                                                                 "es": "El importe de la factura es demasiado grande.",
                                                                 "ar": "مبلغ الفاتورة كبير جداً."},
    "Invoice kind must be 'sales' or 'purchase'.": {"fa": "نوع فاکتور باید فروش یا خرید باشد.",
                                                    "es": "El tipo de factura debe ser venta o compra.",
                                                    "ar": "يجب أن يكون نوع الفاتورة مبيعات أو مشتريات."},
    "Invoice number is empty.": {"fa": "شماره فاکتور خالی است.", "es": "Falta el número de factura.", "ar": "رقم الفاتورة فارغ."},
    "Payment amount must be greater than zero.": {"fa": "مبلغ پرداخت باید بیشتر از صفر باشد.",
                                                  "es": "El importe del pago debe ser mayor que cero.",
                                                  "ar": "يجب أن يكون مبلغ الدفع أكبر من صفر."},
    "Payment not found on this invoice": {"fa": "این پرداخت در این فاکتور پیدا نشد", "es": "Pago no encontrado en esta factura",
                                          "ar": "الدفعة غير موجودة في هذه الفاتورة"},
    "Selected bank entity not found.": {"fa": "بانک انتخاب‌شده پیدا نشد.", "es": "No se encontró el banco elegido.",
                                        "ar": "لم يُعثر على البنك المحدد."},
    "Unsupported file type. Use JPG, PNG, WEBP, or PDF.": {
        "fa": "نوع فایل پشتیبانی نمی‌شود. از JPG، PNG، WEBP یا PDF استفاده کنید.",
        "es": "Tipo de archivo no admitido. Usa JPG, PNG, WEBP o PDF.",
        "ar": "نوع الملف غير مدعوم. استخدم JPG أو PNG أو WEBP أو PDF."},
}

# an invoice's status / kind as a reader says it
_INVOICE_STATUS = {
    "fa": {"draft": "پیش‌نویس", "issued": "صادرشده", "partially_paid": "پرداخت جزئی", "paid": "پرداخت‌شده", "void": "باطل"},
    "es": {"draft": "borrador", "issued": "emitida", "partially_paid": "pagada en parte", "paid": "pagada", "void": "anulada"},
    "ar": {"draft": "مسودة", "issued": "صادرة", "partially_paid": "مدفوعة جزئياً", "paid": "مدفوعة", "void": "ملغاة"},
}
_INVOICE_KIND = {"fa": {"Sales": "فروش", "Purchase": "خرید"}, "es": {"Sales": "venta", "Purchase": "compra"},
                 "ar": {"Sales": "مبيعات", "Purchase": "مشتريات"}}


class _Pattern:
    def __init__(self, regex: str, text: dict[str, str], *, values: Mapping[str, dict] | None = None,
                 nested: tuple[str, ...] = ()):
        self.regex = re.compile(regex, re.S)
        self.text = text
        self.values = values or {}      # group → {lang: {raw: said}}
        self.nested = nested            # groups that are messages themselves

    def render(self, m: re.Match, lang: str) -> str:
        parts = {}
        for name, raw in m.groupdict().items():
            if name in self.nested:
                said = localize_detail(raw, lang)
            else:
                said = self.values.get(name, {}).get(lang, {}).get(raw, raw)
            parts[name] = _iso(said, lang)
        return self.text[lang].format(**parts)


PATTERNS: list[_Pattern] = [
    _Pattern(r"^Transaction not found: (?P<id>\S+)$",
             {"fa": "سند پیدا نشد: {id}", "es": "Asiento no encontrado: {id}", "ar": "القيد غير موجود: {id}"}),
    _Pattern(r"^Account not found: (?P<code>.+)$",
             {"fa": "حساب پیدا نشد: {code}", "es": "Cuenta no encontrada: {code}", "ar": "الحساب غير موجود: {code}"}),
    _Pattern(r"^Entity not found: (?P<id>\S+)$",
             {"fa": "طرف حساب پیدا نشد: {id}", "es": "Tercero no encontrado: {id}", "ar": "الطرف غير موجود: {id}"}),
    _Pattern(r"^Attachment already linked: (?P<id>\S+)$",
             {"fa": "پیوست قبلاً متصل شده است: {id}", "es": "El adjunto ya está vinculado: {id}", "ar": "المرفق مرتبط بالفعل: {id}"}),
    _Pattern(r"^Payment method not found: (?P<name>.+)$",
             {"fa": "روش پرداخت پیدا نشد: {name}", "es": "Método de pago no encontrado: {name}", "ar": "طريقة الدفع غير موجودة: {name}"}),
    _Pattern(r"^No fee rule mapped for (?P<method>.+) via (?P<bank>.+)\.$",
             {"fa": "برای {method} از طریق {bank} قاعده کارمزدی تعریف نشده است.",
              "es": "No hay regla de comisión para {method} con {bank}.",
              "ar": "لا توجد قاعدة رسوم لـ {method} عبر {bank}."}),
    _Pattern(r"^Debits \((?P<d>[^)]*)\) must equal credits \((?P<c>[^)]*)\)$",
             {"fa": "جمع بدهکار ({d}) باید با جمع بستانکار ({c}) برابر باشد",
              "es": "El debe ({d}) debe ser igual al haber ({c})", "ar": "يجب أن يساوي المدين ({d}) الدائن ({c})"}),
    _Pattern(r"^Transaction dated (?P<date>\S+): debits \((?P<d>[^)]*)\) must equal credits \((?P<c>[^)]*)\)$",
             {"fa": "سند {date}: جمع بدهکار ({d}) باید با جمع بستانکار ({c}) برابر باشد",
              "es": "Asiento del {date}: el debe ({d}) debe ser igual al haber ({c})",
              "ar": "القيد المؤرخ {date}: يجب أن يساوي المدين ({d}) الدائن ({c})"}),
    _Pattern(r"^Account codes not found in chart: (?P<codes>.+?)\. Please create them first or adjust the mapping\.$",
             {"fa": "این کدهای حساب در سرفصل‌ها نیستند: {codes}. ابتدا آن‌ها را بسازید یا تطبیق را اصلاح کنید.",
              "es": "Estos códigos no están en el plan de cuentas: {codes}. Créalos primero o ajusta la asignación.",
              "ar": "رموز الحسابات هذه ليست في الدليل: {codes}. أنشئها أولاً أو عدّل المطابقة."}),
    _Pattern(r"^Cannot pay a (?P<status>\w+) invoice\.$",
             {"fa": "فاکتور {status} را نمی‌توان پرداخت کرد.", "es": "No se puede pagar una factura {status}.",
              "ar": "لا يمكن دفع فاتورة {status}."}, values={"status": _INVOICE_STATUS}),
    _Pattern(r"^Only draft invoices can be deleted; this one is (?P<status>\w+)\. Void it instead\.$",
             {"fa": "فقط فاکتور پیش‌نویس حذف می‌شود؛ این فاکتور {status} است. به‌جای حذف، آن را باطل کنید.",
              "es": "Solo se pueden eliminar facturas en borrador; esta está {status}. Anúlala en su lugar.",
              "ar": "لا تُحذف إلا الفواتير المسودة؛ هذه {status}. ألغها بدلاً من ذلك."}, values={"status": _INVOICE_STATUS}),
    _Pattern(r"^Payment currency must match the invoice \((?P<cur>[^)]*)\)\.$",
             {"fa": "واحد پول پرداخت باید با فاکتور ({cur}) یکی باشد.", "es": "La moneda del pago debe coincidir con la factura ({cur}).",
              "ar": "يجب أن تطابق عملة الدفع عملة الفاتورة ({cur})."}),
    _Pattern(r"^Credit note currency must match the invoice \((?P<cur>[^)]*)\)\.$",
             {"fa": "واحد پول یادداشت بستانکاری باید با فاکتور ({cur}) یکی باشد.",
              "es": "La moneda de la nota de crédito debe coincidir con la factura ({cur}).",
              "ar": "يجب أن تطابق عملة الإشعار الدائن عملة الفاتورة ({cur})."}),
    _Pattern(r"^Credit note \((?P<amount>[^)]*)\) exceeds the open balance \((?P<balance>[^)]*)\)\.$",
             {"fa": "یادداشت بستانکاری ({amount}) از مانده باز ({balance}) بیشتر است.",
              "es": "La nota de crédito ({amount}) supera el saldo pendiente ({balance}).",
              "ar": "الإشعار الدائن ({amount}) يتجاوز الرصيد المفتوح ({balance})."}),
    _Pattern(r"^Entry not balanced: DR (?P<d>\S+) != CR (?P<c>\S+)\.$",
             {"fa": "سند تراز نیست: بدهکار {d} ≠ بستانکار {c}.", "es": "El asiento no cuadra: debe {d} ≠ haber {c}.",
              "ar": "القيد غير متوازن: مدين {d} ≠ دائن {c}."}),
    _Pattern(r"^(?P<kind>Sales|Purchase) invoice number '(?P<number>[^']*)' already exists \(id (?P<id>[^)]*)\)\.$",
             {"fa": "شماره فاکتور {kind} «{number}» قبلاً ثبت شده است (شناسه {id}).",
              "es": "Ya existe una factura de {kind} con el número «{number}» (id {id}).",
              "ar": "رقم فاتورة {kind} «{number}» موجود بالفعل (المعرّف {id})."}, values={"kind": _INVOICE_KIND}),
    _Pattern(r"^Could not post the payment — (?P<reason>.+)$",
             {"fa": "پرداخت ثبت نشد — {reason}", "es": "No se pudo registrar el pago — {reason}", "ar": "تعذّر ترحيل الدفعة — {reason}"},
             nested=("reason",)),
    _Pattern(r"^Could not post the credit note — (?P<reason>.+)$",
             {"fa": "یادداشت بستانکاری ثبت نشد — {reason}", "es": "No se pudo registrar la nota de crédito — {reason}",
              "ar": "تعذّر ترحيل الإشعار الدائن — {reason}"}, nested=("reason",)),
    _Pattern(r"^OCR failed: (?P<reason>.+)$",
             {"fa": "خواندن تصویر انجام نشد: {reason}", "es": "Falló el reconocimiento del texto: {reason}",
              "ar": "فشلت قراءة الصورة: {reason}"}, nested=("reason",)),
]


def localize_detail(detail, lang: str):
    """``detail`` in ``lang`` when there is a translation; anything else as it was."""
    if not isinstance(detail, str) or lang not in LANGS:
        return detail
    hit = EXACT.get(detail)
    if hit:
        return hit[lang]
    for p in PATTERNS:
        m = p.regex.match(detail)
        if m:
            return p.render(m, lang)
    return detail


def request_language(headers: Mapping[str, str]) -> str:
    """The page's language (X-UI-Language), else the browser's best supported one, else English."""
    ui = (headers.get("x-ui-language") or "").strip().lower()
    if ui in LANGS or ui == "en":
        return ui
    for part in (headers.get("accept-language") or "").split(","):
        tag = part.split(";")[0].strip().lower().split("-")[0]
        if tag in LANGS or tag == "en":
            return tag
    return "en"
