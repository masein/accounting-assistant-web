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
    # ── payroll ──
    "Employee entity not found.": {"fa": "کارمند پیدا نشد.", "es": "No se encontró al empleado.", "ar": "الموظف غير موجود."},
    "No active pay profiles to run.": {"fa": "هیچ پروفایل حقوق فعالی برای اجرا نیست.",
                                       "es": "No hay perfiles de pago activos para ejecutar.", "ar": "لا توجد ملفات رواتب نشطة للتشغيل."},
    "No employees with anything to pay in this period.": {"fa": "در این دوره کارمندی با مبلغ قابل پرداخت نیست.",
                                                          "es": "Ningún empleado tiene nada que cobrar en este periodo.",
                                                          "ar": "لا يوجد موظف له مستحقات في هذه الفترة."},
    "No payslip for this employee in this run.": {"fa": "در این دوره حقوق، فیشی برای این کارمند نیست.",
                                                  "es": "No hay recibo de nómina de este empleado en esta nómina.",
                                                  "ar": "لا توجد قسيمة راتب لهذا الموظف في هذه الدورة."},
    "No statutory payroll rule set covers today for this locale; ask the platform admin to add one.": {
        "fa": "هیچ مجموعه قواعد حقوق قانونی برای امروز در این کشور تعریف نشده است؛ از مدیر سامانه بخواهید یکی اضافه کند.",
        "es": "Ninguna normativa de nómina cubre hoy en este país; pide al administrador de la plataforma que añada una.",
        "ar": "لا توجد مجموعة قواعد رواتب نظامية تغطي اليوم في هذا البلد؛ اطلب من مدير المنصة إضافة واحدة."},
    "Nothing to pay for this run.": {"fa": "در این دوره حقوق چیزی برای پرداخت نیست.", "es": "No hay nada que pagar en esta nómina.",
                                     "ar": "لا يوجد ما يُدفع في هذه الدورة."},
    "Nothing to post for this run.": {"fa": "در این دوره حقوق چیزی برای ثبت نیست.", "es": "No hay nada que contabilizar en esta nómina.",
                                      "ar": "لا يوجد ما يُرحّل في هذه الدورة."},
    "Pay profiles are only for employee entities.": {"fa": "پروفایل حقوق فقط برای کارمندان است.",
                                                     "es": "Los perfiles de pago son solo para empleados.", "ar": "ملفات الرواتب للموظفين فقط."},
    "Pay run not found.": {"fa": "دوره حقوق پیدا نشد.", "es": "Nómina no encontrada.", "ar": "دورة الرواتب غير موجودة."},
    "Payroll entry must be balanced and non-zero.": {"fa": "سند حقوق باید تراز و غیرصفر باشد.",
                                                     "es": "El asiento de nómina debe cuadrar y no ser cero.",
                                                     "ar": "يجب أن يكون قيد الرواتب متوازناً وغير صفري."},
    "Rule set not found.": {"fa": "مجموعه قواعد پیدا نشد.", "es": "Normativa no encontrada.", "ar": "مجموعة القواعد غير موجودة."},
    "Run is already voided.": {"fa": "این دوره حقوق قبلاً باطل شده است.", "es": "Esta nómina ya está anulada.", "ar": "هذه الدورة ملغاة بالفعل."},
    "Run is paid; reverse the bank settlement first.": {"fa": "این دوره پرداخت شده است؛ ابتدا تسویه بانکی را برگردانید.",
                                                        "es": "Esta nómina está pagada; revierte primero el pago bancario.",
                                                        "ar": "هذه الدورة مدفوعة؛ اعكس التسوية البنكية أولاً."},
    "effective_to is before effective_from.": {"fa": "پایان اعتبار پیش از شروع آن است.", "es": "La fecha de fin es anterior a la de inicio.",
                                               "ar": "نهاية السريان قبل بدايته."},
    "locale must be 'ir' or 'uk'.": {"fa": "کشور باید ایران یا بریتانیا باشد.", "es": "El país debe ser Irán o Reino Unido.",
                                     "ar": "يجب أن يكون البلد إيران أو المملكة المتحدة."},
    "pay_type must be 'salaried' or 'hourly'.": {"fa": "نوع پرداخت باید ماهانه یا ساعتی باشد.",
                                                 "es": "El tipo de pago debe ser asalariado o por horas.",
                                                 "ar": "يجب أن يكون نوع الأجر شهرياً أو بالساعة."},
    "period_end is before period_start.": {"fa": "پایان دوره پیش از شروع آن است.", "es": "El fin del periodo es anterior a su inicio.",
                                           "ar": "نهاية الفترة قبل بدايتها."},
    "tax_mode must be 'flat' or 'statutory'.": {"fa": "روش مالیات باید نرخ ثابت یا قانونی باشد.",
                                                "es": "El modo de impuesto debe ser tipo fijo o legal.",
                                                "ar": "يجب أن تكون طريقة الضريبة نسبة ثابتة أو نظامية."},
    # ── time ──
    "A project must belong to a client entity.": {"fa": "پروژه باید به یک مشتری تعلق داشته باشد.",
                                                  "es": "Un proyecto debe pertenecer a un cliente.", "ar": "يجب أن يتبع المشروع عميلاً."},
    "Billable rates apply to an employee or contractor (supplier).": {
        "fa": "نرخ قابل صورتحساب فقط برای کارمند یا پیمانکار (تأمین‌کننده) است.",
        "es": "Las tarifas facturables son para un empleado o un contratista (proveedor).",
        "ar": "أسعار الفوترة تخص موظفاً أو متعاقداً (مورّداً)."},
    "Client not found.": {"fa": "مشتری پیدا نشد.", "es": "Cliente no encontrado.", "ar": "العميل غير موجود."},
    "Invoice not found.": {"fa": "فاکتور پیدا نشد.", "es": "Factura no encontrada.", "ar": "الفاتورة غير موجودة."},
    "Invoiced time is locked — void its invoice first.": {"fa": "زمانِ صورتحساب‌شده قفل است؛ ابتدا فاکتور آن را باطل کنید.",
                                                          "es": "El tiempo facturado está bloqueado: anula primero su factura.",
                                                          "ar": "الوقت المفوتر مقفل — ألغِ فاتورته أولاً."},
    "No employee record linked to your account.": {"fa": "هیچ پرونده کارمندی به حساب شما متصل نیست.",
                                                   "es": "Tu cuenta no está vinculada a ningún empleado.",
                                                   "ar": "لا يوجد سجل موظف مرتبط بحسابك."},
    "Pending entry not found.": {"fa": "ورودی در انتظار پیدا نشد.", "es": "Registro pendiente no encontrado.", "ar": "الإدخال المعلق غير موجود."},
    "Project not found.": {"fa": "پروژه پیدا نشد.", "es": "Proyecto no encontrado.", "ar": "المشروع غير موجود."},
    "Resolve to an employee or contractor (supplier).": {"fa": "یک کارمند یا پیمانکار (تأمین‌کننده) انتخاب کنید.",
                                                         "es": "Elige un empleado o un contratista (proveedor).",
                                                         "ar": "اختر موظفاً أو متعاقداً (مورّداً)."},
    "This time entry is in a pay run and locked. Void the pay run to edit it.": {
        "fa": "این ثبت زمان در یک دوره حقوق است و قفل شده است. برای ویرایش، دوره حقوق را باطل کنید.",
        "es": "Este registro de tiempo está en una nómina y bloqueado. Anula la nómina para editarlo.",
        "ar": "إدخال الوقت هذا ضمن دورة رواتب ومقفل. ألغِ الدورة لتعديله."},
    "This time entry is in a pay run — void the pay run first.": {
        "fa": "این ثبت زمان در یک دوره حقوق است؛ ابتدا دوره حقوق را باطل کنید.",
        "es": "Este registro de tiempo está en una nómina: anula primero la nómina.",
        "ar": "إدخال الوقت هذا ضمن دورة رواتب — ألغِ الدورة أولاً."},
    "This time entry is invoiced and locked. Void its invoice to edit it.": {
        "fa": "این ثبت زمان صورتحساب شده و قفل است. برای ویرایش، فاکتور آن را باطل کنید.",
        "es": "Este registro de tiempo está facturado y bloqueado. Anula su factura para editarlo.",
        "ar": "إدخال الوقت هذا مفوتر ومقفل. ألغِ فاتورته لتعديله."},
    "Time entry not found.": {"fa": "ثبت زمان پیدا نشد.", "es": "Registro de tiempo no encontrado.", "ar": "إدخال الوقت غير موجود."},
    "Time is logged for an employee or contractor (supplier).": {
        "fa": "زمان برای کارمند یا پیمانکار (تأمین‌کننده) ثبت می‌شود.",
        "es": "El tiempo se registra para un empleado o un contratista (proveedor).",
        "ar": "يُسجّل الوقت لموظف أو متعاقد (مورّد)."},
    "You can only log your own time.": {"fa": "فقط می‌توانید زمانِ خودتان را ثبت کنید.", "es": "Solo puedes registrar tu propio tiempo.",
                                        "ar": "يمكنك تسجيل وقتك فقط."},
    # ── users, companies, settings ──
    "API key not found": {"fa": "کلید API پیدا نشد", "es": "Clave API no encontrada", "ar": "مفتاح API غير موجود"},
    "Can only link to an employee entity": {"fa": "فقط می‌توان به یک کارمند متصل کرد", "es": "Solo se puede vincular a un empleado",
                                            "ar": "يمكن الربط بموظف فقط"},
    "Company not found": {"fa": "شرکت پیدا نشد", "es": "Empresa no encontrada", "ar": "الشركة غير موجودة"},
    "Default admin user cannot be deleted": {"fa": "کاربر مدیر پیش‌فرض را نمی‌توان حذف کرد",
                                             "es": "No se puede eliminar al administrador predeterminado", "ar": "لا يمكن حذف المسؤول الافتراضي"},
    "Entity not found in this company": {"fa": "این طرف حساب در این شرکت نیست", "es": "Tercero no encontrado en esta empresa",
                                         "ar": "الطرف غير موجود في هذه الشركة"},
    "No company in context.": {"fa": "شرکتی انتخاب نشده است.", "es": "No hay ninguna empresa seleccionada.", "ar": "لا توجد شركة محددة."},
    "No company in this session": {"fa": "در این نشست شرکتی انتخاب نشده است", "es": "No hay ninguna empresa en esta sesión",
                                   "ar": "لا توجد شركة في هذه الجلسة"},
    "The company must keep at least one active owner": {"fa": "شرکت باید دست‌کم یک مالک فعال داشته باشد",
                                                        "es": "La empresa debe conservar al menos un propietario activo",
                                                        "ar": "يجب أن تحتفظ الشركة بمالك نشط واحد على الأقل"},
    "The company must keep at least one owner": {"fa": "شرکت باید دست‌کم یک مالک داشته باشد",
                                                 "es": "La empresa debe conservar al menos un propietario",
                                                 "ar": "يجب أن تحتفظ الشركة بمالك واحد على الأقل"},
    "Turn off your own two-factor sign-in under Settings → Security.": {
        "fa": "ورود دومرحله‌ای خودتان را از تنظیمات ← امنیت خاموش کنید.",
        "es": "Desactiva tu propia verificación en dos pasos en Ajustes → Seguridad.",
        "ar": "أوقف التحقق بخطوتين الخاص بك من الإعدادات ← الأمان."},
    "Two-factor sign-in is not on for this user.": {"fa": "ورود دومرحله‌ای برای این کاربر فعال نیست.",
                                                    "es": "La verificación en dos pasos no está activada para este usuario.",
                                                    "ar": "التحقق بخطوتين غير مفعّل لهذا المستخدم."},
    "Username already exists": {"fa": "این نام کاربری قبلاً ثبت شده است", "es": "Ese nombre de usuario ya existe",
                                "ar": "اسم المستخدم موجود بالفعل"},
    "Username is required": {"fa": "نام کاربری لازم است", "es": "Se necesita un nombre de usuario", "ar": "اسم المستخدم مطلوب"},
    "You cannot change your own role": {"fa": "نمی‌توانید نقش خودتان را تغییر دهید", "es": "No puedes cambiar tu propio rol",
                                        "ar": "لا يمكنك تغيير دورك"},
    "You cannot deactivate yourself": {"fa": "نمی‌توانید خودتان را غیرفعال کنید", "es": "No puedes desactivarte a ti mismo",
                                       "ar": "لا يمكنك تعطيل حسابك"},
    "You cannot delete yourself": {"fa": "نمی‌توانید خودتان را حذف کنید", "es": "No puedes eliminarte a ti mismo", "ar": "لا يمكنك حذف حسابك"},
    "closed_period must be an ISO date (YYYY-MM-DD) or empty.": {
        "fa": "تاریخ بستن دوره باید به شکل \u2066YYYY-MM-DD\u2069 یا خالی باشد.",
        "es": "La fecha de cierre debe tener el formato AAAA-MM-DD o estar vacía.",
        "ar": "يجب أن يكون تاريخ إقفال الفترة بصيغة \u2066YYYY-MM-DD\u2069 أو فارغاً."},
    "shares must be >= 0": {"fa": "تعداد سهام نمی‌تواند منفی باشد", "es": "Las acciones no pueden ser negativas",
                            "ar": "لا يمكن أن يكون عدد الأسهم سالباً"},
    # ── quotes ──
    "A converted quote is part of the invoice's history; it can't be deleted.": {
        "fa": "پیش‌فاکتورِ تبدیل‌شده بخشی از سابقه فاکتور است و حذف نمی‌شود.",
        "es": "Un presupuesto convertido forma parte del historial de la factura; no se puede eliminar.",
        "ar": "عرض السعر المحوّل جزء من سجل الفاتورة؛ لا يمكن حذفه."},
    "A declined quote can't be invoiced; set it back to draft first.": {
        "fa": "پیش‌فاکتورِ ردشده را نمی‌توان فاکتور کرد؛ ابتدا آن را به پیش‌نویس برگردانید.",
        "es": "Un presupuesto rechazado no se puede facturar; vuelve a ponerlo en borrador primero.",
        "ar": "لا يمكن فوترة عرض سعر مرفوض؛ أعده إلى مسودة أولاً."},
    "A new quote is 'draft' or 'sent'.": {"fa": "پیش‌فاکتور جدید یا پیش‌نویس است یا ارسال‌شده.",
                                          "es": "Un presupuesto nuevo está en borrador o enviado.", "ar": "عرض السعر الجديد إما مسودة أو مُرسل."},
    "A quote needs a positive amount or at least one priced line.": {
        "fa": "پیش‌فاکتور به مبلغ مثبت یا دست‌کم یک ردیف قیمت‌دار نیاز دارد.",
        "es": "Un presupuesto necesita un importe positivo o al menos una línea con precio.",
        "ar": "يحتاج عرض السعر إلى مبلغ موجب أو بند مسعّر واحد على الأقل."},
    "Customer entity not found.": {"fa": "مشتری پیدا نشد.", "es": "Cliente no encontrado.", "ar": "العميل غير موجود."},
    "Invoice amount is too large.": {"fa": "مبلغ فاکتور بیش از حد بزرگ است.", "es": "El importe de la factura es demasiado grande.",
                                     "ar": "مبلغ الفاتورة كبير جداً."},
    "Quote amount is too large.": {"fa": "مبلغ پیش‌فاکتور بیش از حد بزرگ است.", "es": "El importe del presupuesto es demasiado grande.",
                                   "ar": "مبلغ عرض السعر كبير جداً."},
    "Quote not found.": {"fa": "پیش‌فاکتور پیدا نشد.", "es": "Presupuesto no encontrado.", "ar": "عرض السعر غير موجود."},
    "Quote number is empty.": {"fa": "شماره پیش‌فاکتور خالی است.", "es": "Falta el número del presupuesto.", "ar": "رقم عرض السعر فارغ."},
    "The invoice status must be 'issued' or 'draft'.": {"fa": "وضعیت فاکتور باید صادرشده یا پیش‌نویس باشد.",
                                                        "es": "El estado de la factura debe ser emitida o borrador.",
                                                        "ar": "يجب أن تكون حالة الفاتورة صادرة أو مسودة."},
    "This quote was already converted.": {"fa": "این پیش‌فاکتور قبلاً تبدیل شده است.", "es": "Este presupuesto ya se convirtió.",
                                          "ar": "تم تحويل عرض السعر هذا بالفعل."},
    "This quote was converted to an invoice and is locked.": {"fa": "این پیش‌فاکتور به فاکتور تبدیل شده و قفل است.",
                                                              "es": "Este presupuesto se convirtió en factura y está bloqueado.",
                                                              "ar": "تحوّل عرض السعر هذا إلى فاتورة وأصبح مقفلاً."},
    "due_date is before the invoice's issue date.": {"fa": "سررسید پیش از تاریخ صدور فاکتور است.",
                                                     "es": "El vencimiento es anterior a la fecha de emisión de la factura.",
                                                     "ar": "تاريخ الاستحقاق قبل تاريخ إصدار الفاتورة."},
    "valid_until is before the issue date.": {"fa": "تاریخ اعتبار پیش از تاریخ صدور است.",
                                              "es": "La validez termina antes de la fecha de emisión.",
                                              "ar": "تاريخ انتهاء الصلاحية قبل تاريخ الإصدار."},
}

# an invoice's status / kind as a reader says it
_INVOICE_STATUS = {
    "fa": {"draft": "پیش‌نویس", "issued": "صادرشده", "partially_paid": "پرداخت جزئی", "paid": "پرداخت‌شده", "void": "باطل"},
    "es": {"draft": "borrador", "issued": "emitida", "partially_paid": "pagada en parte", "paid": "pagada", "void": "anulada"},
    "ar": {"draft": "مسودة", "issued": "صادرة", "partially_paid": "مدفوعة جزئياً", "paid": "مدفوعة", "void": "ملغاة"},
}
_INVOICE_KIND = {"fa": {"Sales": "فروش", "Purchase": "خرید"}, "es": {"Sales": "venta", "Purchase": "compra"},
                 "ar": {"Sales": "مبيعات", "Purchase": "مشتريات"}}

_PAY_RUN_STATUS = {
    "fa": {"draft": "پیش‌نویس", "posted": "ثبت‌شده", "paid": "پرداخت‌شده", "voided": "باطل‌شده"},
    "es": {"draft": "en borrador", "posted": "contabilizada", "paid": "pagada", "voided": "anulada"},
    "ar": {"draft": "مسودة", "posted": "مرحّلة", "paid": "مدفوعة", "voided": "ملغاة"},
}
_QUOTE_STATUS = {
    "fa": {"draft": "پیش‌نویس", "sent": "ارسال‌شده", "accepted": "پذیرفته‌شده", "declined": "ردشده", "converted": "تبدیل‌شده"},
    "es": {"draft": "en borrador", "sent": "enviado", "accepted": "aceptado", "declined": "rechazado", "converted": "convertido"},
    "ar": {"draft": "مسودة", "sent": "مُرسل", "accepted": "مقبول", "declined": "مرفوض", "converted": "محوّل"},
}
_LOCALE = {"fa": {"ir": "ایران", "uk": "بریتانیا"}, "es": {"ir": "Irán", "uk": "Reino Unido"},
           "ar": {"ir": "إيران", "uk": "المملكة المتحدة"}}


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
    # payroll, time, users, quotes
    _Pattern(r"^Run already (?P<status>\w+); cannot post again\.$",
             {"fa": "این دوره حقوق قبلاً {status} است؛ دوباره ثبت نمی‌شود.", "es": "La nómina ya está {status}; no se puede volver a contabilizar.",
              "ar": "الدورة {status} بالفعل؛ لا يمكن ترحيلها مرة أخرى."}, values={"status": _PAY_RUN_STATUS}),
    _Pattern(r"^Run is (?P<status>\w+); post it before paying\.$",
             {"fa": "این دوره حقوق {status} است؛ پیش از پرداخت آن را ثبت کنید.", "es": "La nómina está {status}; contabilízala antes de pagarla.",
              "ar": "الدورة {status}؛ رحّلها قبل الدفع."}, values={"status": _PAY_RUN_STATUS}),
    _Pattern(r"^A (?P<locale>\w+) rule set for (?P<year>\d+) already exists\.$",
             {"fa": "مجموعه قواعد {locale} برای سال {year} قبلاً وجود دارد.", "es": "Ya existe una normativa de {locale} para {year}.",
              "ar": "توجد بالفعل مجموعة قواعد {locale} لسنة {year}."}, values={"locale": _LOCALE}),
    _Pattern(r"^No pay profile for: (?P<who>.+)$",
             {"fa": "برای این افراد پروفایل حقوق نیست: {who}", "es": "Sin perfil de pago para: {who}", "ar": "لا يوجد ملف راتب لـ: {who}"}),
    _Pattern(r"^Already in a pay run for an overlapping period: (?P<runs>.+)\. Void that run, or set allow_overlap for a deliberate off-cycle run\.$",
             {"fa": "در دوره حقوقی با بازه هم‌پوشان قرار دارد: {runs}. آن دوره را باطل کنید یا برای پرداخت خارج از نوبت، هم‌پوشانی را مجاز کنید.",
              "es": "Ya está en una nómina de un periodo que se solapa: {runs}. Anula esa nómina o permite el solapamiento para un pago extraordinario.",
              "ar": "مدرج بالفعل في دورة رواتب لفترة متداخلة: {runs}. ألغِ تلك الدورة أو اسمح بالتداخل لدفعة استثنائية."}),
    _Pattern(r"^No statutory payroll rule set covers (?P<date>\S+) for this locale; add one under Payroll → Statutory rules\.$",
             {"fa": "هیچ مجموعه قواعد حقوق قانونی تاریخ {date} را در این کشور پوشش نمی‌دهد؛ از «حقوق و دستمزد ← قواعد قانونی» یکی اضافه کنید.",
              "es": "Ninguna normativa de nómina cubre el {date} en este país; añade una en Nómina → Normativa legal.",
              "ar": "لا توجد مجموعة قواعد رواتب نظامية تغطي {date} في هذا البلد؛ أضف واحدة من الرواتب ← القواعد النظامية."}),
    _Pattern(r"^(?P<name>.+): no payable tracked hours in the period\.$",
             {"fa": "{name}: در این دوره ساعت قابل پرداختی ثبت نشده است.", "es": "{name}: no hay horas pagables registradas en el periodo.",
              "ar": "{name}: لا ساعات مسجلة قابلة للدفع في هذه الفترة."}),
    _Pattern(r"^That would be (?P<total>[\d.]+) hours on (?P<date>\S+) \((?P<already>[\d.]+) already logged\); a day has 24\.$",
             {"fa": "با این ثبت، {date} به {total} ساعت می‌رسد ({already} ساعت از قبل ثبت شده است)؛ یک روز ۲۴ ساعت است.",
              "es": "Serían {total} horas el {date} ({already} ya registradas); un día tiene 24.",
              "ar": "سيصبح المجموع {total} ساعة في {date} ({already} مسجلة بالفعل)؛ اليوم 24 ساعة."}),
    _Pattern(r"^A (?P<status>\w+) quote can't be edited; set it back to draft first\.$",
             {"fa": "پیش‌فاکتور {status} قابل ویرایش نیست؛ ابتدا آن را به پیش‌نویس برگردانید.",
              "es": "Un presupuesto {status} no se puede editar; vuelve a ponerlo en borrador primero.",
              "ar": "لا يمكن تعديل عرض سعر {status}؛ أعده إلى مسودة أولاً."}, values={"status": _QUOTE_STATUS}),
    _Pattern(r"^Quote number '(?P<number>[^']*)' already exists\.$",
             {"fa": "شماره پیش‌فاکتور «{number}» قبلاً ثبت شده است.", "es": "Ya existe un presupuesto con el número «{number}».",
              "ar": "رقم عرض السعر «{number}» موجود بالفعل."}),
    _Pattern(r"^Invalid role: (?P<role>.+)$",
             {"fa": "نقش نامعتبر: {role}", "es": "Rol no válido: {role}", "ar": "دور غير صالح: {role}"}),
    _Pattern(r"^Reset failed: (?P<reason>.+)$",
             {"fa": "بازنشانی انجام نشد: {reason}", "es": "No se pudo restablecer: {reason}", "ar": "فشلت إعادة التعيين: {reason}"},
             nested=("reason",)),
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
