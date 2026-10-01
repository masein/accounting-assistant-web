"""What a notification says, in each reader's language.

notification_service stores a ``text_key`` and its values on every row (the
English title/message stay too, for older rows and as the fallback); the
feed and web push render it for the person reading — "Invoice INV-7 overdue"
in English, «فاکتور INV-7 سررسید گذشته» in Persian.

A value is shown as it is, except:
* an enumeration (``ENUMS``: a payroll status, receivable/payable, …) said in
  the language;
* ``month`` — a period key ("1405-07", "2026-10") named in the language, and
  ``season`` — an Iranian season ("1405-2");
* a dict ``{"key": …, "params": …}`` — a phrase of its own (``PHRASES``), and
  a list — several, joined by " · ".
In Persian and Arabic every value is a first-strong isolate, so a name or a
date keeps its direction. tests/test_notification_text.py keeps every key
the service writes worded in all four languages with the same values.
"""
from __future__ import annotations

from typing import Any

LANGS = ("en", "fa", "es", "ar")
_RTL = ("fa", "ar")

_SAYAD_MSG = {"en": "{amount} due {date} — ", "fa": "{amount}، سررسید {date} — ", "es": "{amount}, vence el {date} — ",
              "ar": "{amount}، يستحق في {date} — "}
_TAX_IR_MSG = {
    "en": "Figures and the Excel file: Invoices → Seasonal tax reports. A Friday or holiday deadline moves to the next working day.",
    "fa": "ارقام و فایل اکسل: فاکتورها ← گزارش‌های مالیاتی فصلی. مهلتی که به جمعه یا تعطیل بیفتد به روز کاری بعد می‌رود.",
    "es": "Cifras y archivo Excel: Facturas → Informes fiscales trimestrales. Un plazo en viernes o festivo pasa al siguiente día hábil.",
    "ar": "الأرقام وملف Excel: الفواتير ← التقارير الضريبية الفصلية. الموعد الذي يقع يوم جمعة أو عطلة ينتقل إلى يوم العمل التالي.",
}
_MTD_VAT_MSG = {
    "en": "Boxes 1–9 and a CSV for your MTD software: Invoices → Making Tax Digital.",
    "fa": "خانه‌های ۱ تا ۹ و یک فایل CSV برای نرم‌افزار MTD: فاکتورها ← Making Tax Digital.",
    "es": "Las casillas 1–9 y un CSV para tu software MTD: Facturas → Making Tax Digital.",
    "ar": "الخانات 1–9 وملف CSV لبرنامج MTD: الفواتير ← Making Tax Digital.",
}
_MTD_ITSA_MSG = {
    "en": "Figures by HMRC category and the update file: Invoices → Making Tax Digital.",
    "fa": "ارقام به تفکیک دسته‌های HMRC و فایل به‌روزرسانی: فاکتورها ← Making Tax Digital.",
    "es": "Cifras por categoría de HMRC y el archivo de actualización: Facturas → Making Tax Digital.",
    "ar": "الأرقام حسب فئات HMRC وملف التحديث: الفواتير ← Making Tax Digital.",
}
_APIKEY_MSG = {
    "en": "Integrations using {prefix}… stop working on {date}. Create a new key under Settings → API keys and update the integration.",
    "fa": "یکپارچه‌سازی‌هایی که از {prefix}… استفاده می‌کنند در {date} از کار می‌افتند. در تنظیمات ← کلیدهای API کلید تازه بسازید و یکپارچه‌سازی را به‌روز کنید.",
    "es": "Las integraciones que usan {prefix}… dejarán de funcionar el {date}. Crea una clave nueva en Ajustes → Claves API y actualiza la integración.",
    "ar": "تتوقف التكاملات التي تستخدم {prefix}… في {date}. أنشئ مفتاحاً جديداً من الإعدادات ← مفاتيح API وحدّث التكامل.",
}
_AI_SEE = {"en": " See Settings → AI usage.", "fa": " تنظیمات ← مصرف هوش مصنوعی را ببینید.",
           "es": " Consulta Ajustes → Uso de IA.", "ar": " راجع الإعدادات ← استخدام الذكاء الاصطناعي."}


def _both(title: dict[str, str], message: dict[str, str]) -> dict[str, dict[str, str]]:
    return {"title": title, "message": message}


TEXT: dict[str, dict[str, dict[str, str]]] = {
    "invoice_overdue": _both(
        {"en": "Invoice {number} overdue", "fa": "فاکتور {number} سررسید گذشته", "es": "Factura {number} vencida",
         "ar": "الفاتورة {number} متأخرة"},
        {"en": "{side} — {days} day(s) past due ({date})", "fa": "{side} — {days} روز از سررسید گذشته ({date})",
         "es": "{side} — {days} día(s) de retraso ({date})", "ar": "{side} — متأخرة {days} يوماً ({date})"}),
    "invoice_due": _both(
        {"en": "Invoice {number} due {date}", "fa": "سررسید فاکتور {number}: {date}", "es": "La factura {number} vence el {date}",
         "ar": "تستحق الفاتورة {number} في {date}"},
        {"en": "{side}", "fa": "{side}", "es": "{side}", "ar": "{side}"}),
    "moadian_overdue": _both(
        {"en": "Invoice {number} not sent to سامانه مودیان", "fa": "فاکتور {number} به سامانه مودیان ارسال نشده است",
         "es": "La factura {number} no se envió a سامانه مودیان", "ar": "لم تُرسل الفاتورة {number} إلى سامانه مودیان"},
        {"en": "The {limit}-day deadline passed on {date} ({days} day(s) ago).",
         "fa": "مهلت {limit} روزه در {date} گذشت ({days} روز پیش).",
         "es": "El plazo de {limit} días venció el {date} (hace {days} día(s)).",
         "ar": "انتهت مهلة {limit} يوماً في {date} (قبل {days} يوماً)."}),
    "moadian_due": _both(
        {"en": "Send invoice {number} to سامانه مودیان", "fa": "فاکتور {number} را به سامانه مودیان بفرستید",
         "es": "Envía la factura {number} a سامانه مودیان", "ar": "أرسل الفاتورة {number} إلى سامانه مودیان"},
        {"en": "Deadline {date} ({days} day(s) left).", "fa": "مهلت {date} ({days} روز مانده).",
         "es": "Plazo: {date} (quedan {days} día(s)).", "ar": "الموعد {date} (متبقٍ {days} يوماً)."}),
    "apikey_expired": _both(
        {"en": "API key '{label}' has expired", "fa": "کلید API «{label}» منقضی شده است", "es": "La clave API «{label}» ha caducado",
         "ar": "انتهت صلاحية مفتاح API «{label}»"}, _APIKEY_MSG),
    "apikey_expiring": _both(
        {"en": "API key '{label}' expires in {days} day(s)", "fa": "کلید API «{label}» تا {days} روز دیگر منقضی می‌شود",
         "es": "La clave API «{label}» caduca en {days} día(s)", "ar": "تنتهي صلاحية مفتاح API «{label}» خلال {days} يوماً"},
        _APIKEY_MSG),
    "ai_budget_out": _both(
        {"en": "AI allowance used up ({pct}%)", "fa": "سهمیه هوش مصنوعی تمام شد ({pct}٪)", "es": "Cuota de IA agotada ({pct} %)",
         "ar": "نفدت حصة الذكاء الاصطناعي ({pct}٪)"},
        {lang: s + _AI_SEE[lang] for lang, s in {
            "en": "{used} of {budget} tokens in the last 24 hours. AI features are paused until older use drops out of the window.",
            "fa": "{used} از {budget} توکن در ۲۴ ساعت گذشته. ویژگی‌های هوش مصنوعی تا خارج شدن مصرف قدیمی‌تر متوقف است.",
            "es": "{used} de {budget} tokens en las últimas 24 horas. Las funciones de IA quedan en pausa hasta que el uso antiguo salga de la ventana.",
            "ar": "{used} من {budget} رمز في آخر 24 ساعة. ميزات الذكاء الاصطناعي متوقفة حتى يخرج الاستخدام الأقدم من النافذة."}.items()}),
    "ai_budget_near": _both(
        {"en": "AI allowance {pct}% used", "fa": "{pct}٪ از سهمیه هوش مصنوعی مصرف شده", "es": "Cuota de IA usada al {pct} %",
         "ar": "استُخدم {pct}٪ من حصة الذكاء الاصطناعي"},
        {lang: s + _AI_SEE[lang] for lang, s in {
            "en": "{used} of {budget} tokens in the last 24 hours. AI features pause when it reaches 100%.",
            "fa": "{used} از {budget} توکن در ۲۴ ساعت گذشته. در ۱۰۰٪ ویژگی‌های هوش مصنوعی متوقف می‌شوند.",
            "es": "{used} de {budget} tokens en las últimas 24 horas. Las funciones de IA se pausan al llegar al 100 %.",
            "ar": "{used} من {budget} رمز في آخر 24 ساعة. تتوقف ميزات الذكاء الاصطناعي عند 100٪."}.items()}),
    "bank_mail_waiting_one": _both(
        {"en": "1 bank statement arrived by e-mail", "fa": "یک صورتحساب بانکی با ایمیل رسید", "es": "Llegó 1 extracto bancario por correo",
         "ar": "وصل كشف حساب بنكي واحد بالبريد"},
        {"en": "It's on the Bank statements page, waiting for you to check and approve it.",
         "fa": "در صفحه صورتحساب‌های بانکی منتظر بررسی و تأیید شماست.",
         "es": "Está en la página de extractos bancarios, esperando que lo revises y apruebes.",
         "ar": "إنه في صفحة كشوف الحسابات، بانتظار مراجعتك واعتمادك."}),
    "bank_mail_waiting": _both(
        {"en": "{n} bank statements arrived by e-mail", "fa": "{n} صورتحساب بانکی با ایمیل رسید",
         "es": "Llegaron {n} extractos bancarios por correo", "ar": "وصل {n} كشف حساب بنكي بالبريد"},
        {"en": "They're on the Bank statements page, waiting for you to check and approve them.",
         "fa": "در صفحه صورتحساب‌های بانکی منتظر بررسی و تأیید شما هستند.",
         "es": "Están en la página de extractos bancarios, esperando que los revises y apruebes.",
         "ar": "إنها في صفحة كشوف الحسابات، بانتظار مراجعتك واعتمادك."}),
    "bank_mail_failing": _both(
        {"en": "The statements mailbox can't be read", "fa": "صندوق ایمیل صورتحساب‌ها خوانده نمی‌شود",
         "es": "No se puede leer el buzón de extractos", "ar": "تتعذّر قراءة صندوق بريد الكشوف"},
        {"en": "The last {n} checks failed: {error}. Check the server, username and password on the Bank statements page.",
         "fa": "{n} بررسی آخر ناموفق بود: {error}. سرور، نام کاربری و رمز را در صفحه صورتحساب‌های بانکی بررسی کنید.",
         "es": "Las últimas {n} comprobaciones fallaron: {error}. Revisa el servidor, el usuario y la contraseña en la página de extractos.",
         "ar": "فشلت آخر {n} محاولات: {error}. تحقّق من الخادم واسم المستخدم وكلمة المرور في صفحة كشوف الحسابات."}),
    "tax_ir_vat_today": _both(
        {"en": "VAT return for {season} due today", "fa": "مهلت اظهارنامه ارزش افزوده {season} امروز است",
         "es": "La declaración de IVA de {season} vence hoy", "ar": "يستحق إقرار ضريبة القيمة المضافة لـ {season} اليوم"}, _TAX_IR_MSG),
    "tax_ir_vat_in": _both(
        {"en": "VAT return for {season} due in {days} day(s)", "fa": "مهلت اظهارنامه ارزش افزوده {season} تا {days} روز دیگر",
         "es": "La declaración de IVA de {season} vence en {days} día(s)",
         "ar": "يستحق إقرار ضريبة القيمة المضافة لـ {season} خلال {days} يوماً"}, _TAX_IR_MSG),
    "tax_ir_ttms_today": _both(
        {"en": "Quarterly transactions report (TTMS) for {season} due today", "fa": "مهلت گزارش معاملات فصلی {season} امروز است",
         "es": "El informe trimestral de operaciones (TTMS) de {season} vence hoy",
         "ar": "يستحق تقرير المعاملات الفصلي (TTMS) لـ {season} اليوم"}, _TAX_IR_MSG),
    "tax_ir_ttms_in": _both(
        {"en": "Quarterly transactions report (TTMS) for {season} due in {days} day(s)",
         "fa": "مهلت گزارش معاملات فصلی {season} تا {days} روز دیگر",
         "es": "El informe trimestral de operaciones (TTMS) de {season} vence en {days} día(s)",
         "ar": "يستحق تقرير المعاملات الفصلي (TTMS) لـ {season} خلال {days} يوماً"}, _TAX_IR_MSG),
    "mtd_vat_today": _both(
        {"en": "VAT return for {start_month}–{end_month} due today", "fa": "مهلت اظهارنامه VAT برای {start_month} تا {end_month} امروز است",
         "es": "La declaración de IVA de {start_month}–{end_month} vence hoy",
         "ar": "يستحق إقرار ضريبة القيمة المضافة لـ {start_month}–{end_month} اليوم"}, _MTD_VAT_MSG),
    "mtd_vat_in": _both(
        {"en": "VAT return for {start_month}–{end_month} due in {days} day(s)",
         "fa": "مهلت اظهارنامه VAT برای {start_month} تا {end_month} تا {days} روز دیگر",
         "es": "La declaración de IVA de {start_month}–{end_month} vence en {days} día(s)",
         "ar": "يستحق إقرار ضريبة القيمة المضافة لـ {start_month}–{end_month} خلال {days} يوماً"}, _MTD_VAT_MSG),
    "mtd_itsa_today": _both(
        {"en": "MTD quarterly update ({taxyear} Q{quarter}) due today", "fa": "مهلت به‌روزرسانی فصلی MTD ({taxyear} فصل {quarter}) امروز است",
         "es": "La actualización trimestral MTD ({taxyear} T{quarter}) vence hoy",
         "ar": "يستحق التحديث الفصلي MTD ({taxyear} الربع {quarter}) اليوم"}, _MTD_ITSA_MSG),
    "mtd_itsa_in": _both(
        {"en": "MTD quarterly update ({taxyear} Q{quarter}) due in {days} day(s)",
         "fa": "مهلت به‌روزرسانی فصلی MTD ({taxyear} فصل {quarter}) تا {days} روز دیگر",
         "es": "La actualización trimestral MTD ({taxyear} T{quarter}) vence en {days} día(s)",
         "ar": "يستحق التحديث الفصلي MTD ({taxyear} الربع {quarter}) خلال {days} يوماً"}, _MTD_ITSA_MSG),
    "payday_passed": _both(
        {"en": "Payroll payday {date}", "fa": "روز پرداخت حقوق: {date}", "es": "Día de pago de la nómina: {date}",
         "ar": "يوم صرف الرواتب: {date}"},
        {"en": "Pay run {start}–{end} is {status} — pay date passed", "fa": "دوره حقوق {start} تا {end} {status} است — روز پرداخت گذشته",
         "es": "La nómina {start}–{end} está {status}: la fecha de pago ya pasó",
         "ar": "دورة الرواتب {start}–{end} {status} — مضى يوم الدفع"}),
    "payday_coming": _both(
        {"en": "Payroll payday {date}", "fa": "روز پرداخت حقوق: {date}", "es": "Día de pago de la nómina: {date}",
         "ar": "يوم صرف الرواتب: {date}"},
        {"en": "Pay run {start}–{end} is {status} — pay date coming up", "fa": "دوره حقوق {start} تا {end} {status} است — روز پرداخت نزدیک است",
         "es": "La nómina {start}–{end} está {status}: se acerca la fecha de pago",
         "ar": "دورة الرواتب {start}–{end} {status} — يقترب يوم الدفع"}),
    "ai_approval": _both(
        {"en": "Approval needed: {summary}", "fa": "نیاز به تأیید: {summary}", "es": "Se necesita aprobación: {summary}",
         "ar": "مطلوب اعتماد: {summary}"},
        {"en": "Asked by {who} in the AI chat. Open the AI chat to approve or reject it.",
         "fa": "{who} در گفتگوی هوش مصنوعی درخواست کرده است. برای تأیید یا رد، گفتگو را باز کنید.",
         "es": "Lo pidió {who} en el chat de IA. Abre el chat de IA para aprobarlo o rechazarlo.",
         "ar": "طلبه {who} في محادثة الذكاء الاصطناعي. افتح المحادثة لاعتماده أو رفضه."}),
    "ai_approval_amount": _both(
        {"en": "Approval needed: {summary}", "fa": "نیاز به تأیید: {summary}", "es": "Se necesita aprobación: {summary}",
         "ar": "مطلوب اعتماد: {summary}"},
        {"en": "Asked by {who} in the AI chat — {amount}. Open the AI chat to approve or reject it.",
         "fa": "{who} در گفتگوی هوش مصنوعی درخواست کرده است — {amount}. برای تأیید یا رد، گفتگو را باز کنید.",
         "es": "Lo pidió {who} en el chat de IA — {amount}. Abre el chat de IA para aprobarlo o rechazarlo.",
         "ar": "طلبه {who} في محادثة الذكاء الاصطناعي — {amount}. افتح المحادثة لاعتماده أو رفضه."}),
    "expenses_pending": _both(
        {"en": "{n} expense claim(s) awaiting approval", "fa": "{n} درخواست هزینه در انتظار تأیید",
         "es": "{n} solicitud(es) de gasto pendiente(s) de aprobación", "ar": "{n} مطالبة مصروفات بانتظار الاعتماد"},
        {"en": "Review and approve or reject the pending expense claims.", "fa": "درخواست‌های هزینه در انتظار را بررسی و تأیید یا رد کنید.",
         "es": "Revisa y aprueba o rechaza las solicitudes de gasto pendientes.", "ar": "راجع مطالبات المصروفات المعلقة واعتمدها أو ارفضها."}),
    "petty_pending": _both(
        {"en": "{n} petty cash expense(s) awaiting approval", "fa": "{n} هزینه تنخواه در انتظار تأیید",
         "es": "{n} gasto(s) de caja chica pendiente(s) de aprobación", "ar": "{n} مصروف عهدة نقدية بانتظار الاعتماد"},
        {"en": "Review the pending petty cash expenses.", "fa": "هزینه‌های تنخواه در انتظار را بررسی کنید.",
         "es": "Revisa los gastos de caja chica pendientes.", "ar": "راجع مصروفات العهدة النقدية المعلقة."}),
    "recurring_due": _both(
        {"en": "Recurring: {name} due {date}", "fa": "تکرارشونده: {name}، سررسید {date}", "es": "Recurrente: {name}, vence el {date}",
         "ar": "متكرر: {name}، يستحق في {date}"},
        {"en": "{direction}", "fa": "{direction}", "es": "{direction}", "ar": "{direction}"}),
    "recurring_due_amount": _both(
        {"en": "Recurring: {name} due {date}", "fa": "تکرارشونده: {name}، سررسید {date}", "es": "Recurrente: {name}, vence el {date}",
         "ar": "متكرر: {name}، يستحق في {date}"},
        {"en": "{direction} of {amount}", "fa": "{direction} به مبلغ {amount}", "es": "{direction} de {amount}",
         "ar": "{direction} بمبلغ {amount}"}),
    "reminder": _both(
        {"en": "{title}", "fa": "{title}", "es": "{title}", "ar": "{title}"},
        {"en": "{note} — due {date}", "fa": "{note} — سررسید {date}", "es": "{note} — vence el {date}", "ar": "{note} — يستحق في {date}"}),
    "reminder_bare": _both(
        {"en": "{title}", "fa": "{title}", "es": "{title}", "ar": "{title}"},
        {"en": "Due {date}", "fa": "سررسید {date}", "es": "Vence el {date}", "ar": "يستحق في {date}"}),
    "sayad_register": _both(
        {"en": "Register cheque in Sayad: {title}", "fa": "ثبت چک در سامانه صیاد: {title}", "es": "Registra el cheque en Sayad: {title}",
         "ar": "سجّل الشيك في نظام صياد: {title}"},
        {lang: _SAYAD_MSG[lang] + s for lang, s in {
            "en": "an issued cheque must be registered in the Sayad system (صیاد) to be honoured.",
            "fa": "چک صادره برای پرداخت شدن باید در سامانه صیاد ثبت شود.",
            "es": "un cheque emitido debe registrarse en el sistema Sayad (صیاد) para pagarse.",
            "ar": "يجب تسجيل الشيك الصادر في نظام صياد ليُصرف."}.items()}),
    "sayad_confirm": _both(
        {"en": "Confirm cheque in Sayad: {title}", "fa": "تأیید چک در سامانه صیاد: {title}", "es": "Confirma el cheque en Sayad: {title}",
         "ar": "أكّد الشيك في نظام صياد: {title}"},
        {lang: _SAYAD_MSG[lang] + s for lang, s in {
            "en": "confirm receiving it in the Sayad system (صیاد) through your bank's app.",
            "fa": "دریافت آن را در سامانه صیاد از طریق برنامه بانک خود تأیید کنید.",
            "es": "confirma que lo recibiste en el sistema Sayad (صیاد) desde la app de tu banco.",
            "ar": "أكّد استلامه في نظام صياد عبر تطبيق بنكك."}.items()}),
    "commitment": _both(
        {"en": "{noun}{seq}: {title}", "fa": "{noun}{seq}: {title}", "es": "{noun}{seq}: {title}", "ar": "{noun}{seq}: {title}"},
        {"en": "{amount} {verb} — {when}", "fa": "{amount} {verb} — {when}", "es": "{amount} {verb} — {when}",
         "ar": "{amount} {verb} — {when}"}),
    "budget_over": _both(
        {"en": "Budget exceeded: {category}", "fa": "بودجه رد شد: {category}", "es": "Presupuesto superado: {category}",
         "ar": "تم تجاوز الميزانية: {category}"},
        {"en": "{actual} of {limit} spent in {month} ({spent}%)", "fa": "{actual} از {limit} در {month} خرج شده ({spent}٪)",
         "es": "{actual} de {limit} gastado en {month} ({spent} %)", "ar": "أُنفق {actual} من {limit} في {month} ({spent}٪)"}),
    "budget_near": _both(
        {"en": "Budget at {pct}%: {category}", "fa": "بودجه در {pct}٪: {category}", "es": "Presupuesto al {pct} %: {category}",
         "ar": "الميزانية عند {pct}٪: {category}"},
        {"en": "{actual} of {limit} spent in {month} ({spent}%)", "fa": "{actual} از {limit} در {month} خرج شده ({spent}٪)",
         "es": "{actual} de {limit} gastado en {month} ({spent} %)", "ar": "أُنفق {actual} من {limit} في {month} ({spent}٪)"}),
    "project_over": _both(
        {"en": "Project over budget: {name}", "fa": "پروژه از بودجه گذشت: {name}", "es": "Proyecto por encima del presupuesto: {name}",
         "ar": "المشروع تجاوز الميزانية: {name}"},
        {"en": "{parts}", "fa": "{parts}", "es": "{parts}", "ar": "{parts}"}),
    "project_near": _both(
        {"en": "Project near its budget: {name}", "fa": "پروژه نزدیک بودجه‌اش است: {name}", "es": "Proyecto cerca de su presupuesto: {name}",
         "ar": "المشروع قريب من ميزانيته: {name}"},
        {"en": "{parts}", "fa": "{parts}", "es": "{parts}", "ar": "{parts}"}),
}

# phrases used inside a message ({"key": …, "params": …} values)
PHRASES: dict[str, dict[str, str]] = {
    "commitment_due": {"en": "due {date}", "fa": "سررسید {date}", "es": "vence el {date}", "ar": "يستحق في {date}"},
    "commitment_overdue": {"en": "{days} day(s) overdue ({date})", "fa": "{days} روز از سررسید گذشته ({date})",
                           "es": "{days} día(s) de retraso ({date})", "ar": "متأخر {days} يوماً ({date})"},
    "commitment_bounced_receive": {"en": "bounced — the customer owes it again ({date})", "fa": "برگشتی — مشتری دوباره بدهکار است ({date})",
                                   "es": "rechazado: el cliente vuelve a deberlo ({date})", "ar": "مرتجع — العميل مدين به مجدداً ({date})"},
    "commitment_bounced_pay": {"en": "bounced — still owed ({date})", "fa": "برگشتی — هنوز بدهکاریم ({date})",
                               "es": "rechazado: aún se debe ({date})", "ar": "مرتجع — لا يزال مستحقاً ({date})"},
    "project_hours": {"en": "{used} of {budget} hours ({pct}%)", "fa": "{used} از {budget} ساعت ({pct}٪)",
                      "es": "{used} de {budget} horas ({pct} %)", "ar": "{used} من {budget} ساعة ({pct}٪)"},
    "project_amount": {"en": "{used} of {budget} {currency} ({pct}%)", "fa": "{used} از {budget} {currency} ({pct}٪)",
                       "es": "{used} de {budget} {currency} ({pct} %)", "ar": "{used} من {budget} {currency} ({pct}٪)"},
}

ENUMS: dict[str, dict[str, dict[str, str]]] = {
    "side": {"en": {"receivable": "Receivable from the customer", "payable": "Payable to the supplier"},
             "fa": {"receivable": "دریافت از مشتری", "payable": "پرداخت به تأمین‌کننده"},
             "es": {"receivable": "Cobro al cliente", "payable": "Pago al proveedor"},
             "ar": {"receivable": "مستحق من العميل", "payable": "مستحق للمورّد"}},
    "status": {"en": {"draft": "draft", "posted": "posted", "paid": "paid", "voided": "voided"},
               "fa": {"draft": "پیش‌نویس", "posted": "ثبت‌شده", "paid": "پرداخت‌شده", "voided": "باطل‌شده"},
               "es": {"draft": "en borrador", "posted": "contabilizada", "paid": "pagada", "voided": "anulada"},
               "ar": {"draft": "مسودة", "posted": "مرحّلة", "paid": "مدفوعة", "voided": "ملغاة"}},
    "direction": {"en": {"payment": "Payment", "receipt": "Receipt"}, "fa": {"payment": "پرداخت", "receipt": "دریافت"},
                  "es": {"payment": "Pago", "receipt": "Cobro"}, "ar": {"payment": "دفعة", "receipt": "مقبوض"}},
    "noun": {"en": {"cheque": "Cheque", "installment": "Installment"}, "fa": {"cheque": "چک", "installment": "قسط"},
             "es": {"cheque": "Cheque", "installment": "Cuota"}, "ar": {"cheque": "شيك", "installment": "قسط"}},
    "verb": {"en": {"pay": "to pay", "receive": "to receive"}, "fa": {"pay": "پرداختنی", "receive": "دریافتنی"},
             "es": {"pay": "a pagar", "receive": "a cobrar"}, "ar": {"pay": "للدفع", "receive": "للتحصيل"}},
}
_SEASONS = {"en": ("Spring", "Summer", "Autumn", "Winter"), "fa": ("بهار", "تابستان", "پاییز", "زمستان"),
            "es": ("Primavera", "Verano", "Otoño", "Invierno"), "ar": ("الربيع", "الصيف", "الخريف", "الشتاء")}
_UNISOLATED = {"seq"}     # " 2/6" after a noun: part of the word it follows


def _fa_digits(text: str) -> str:
    return text.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _value(name: str, value: Any, lang: str) -> str:
    if isinstance(value, dict) and "key" in value:
        phrase = PHRASES[value["key"]][lang]
        return phrase.format(**{k: _value(k, v, lang) for k, v in (value.get("params") or {}).items()})
    if isinstance(value, list):
        return " · ".join(_value(name, v, lang) for v in value)
    if name in ENUMS:
        return ENUMS[name][lang].get(str(value), str(value))
    if name in ("month", "start_month", "end_month"):
        from app.services.calendar_periods import month_label
        return month_label(str(value), lang)
    if name == "season":
        year, n = str(value).split("-")
        return f"{_SEASONS[lang][int(n) - 1]} {_fa_digits(year) if lang == 'fa' else year}"
    return "" if value is None else str(value)


def render(key: str | None, params: dict[str, Any] | None, lang: str) -> tuple[str, str] | None:
    """(title, message) for ``lang``, or None when the row has no text key (an
    older row: show its stored English)."""
    if not key:
        return None
    lang = lang if lang in LANGS else "en"
    params = params or {}
    if key.startswith("insight:"):
        from app.services.insight_service import Insight
        loc = Insight(key="", kind=key.split(":", 1)[1], severity="info", page="", params=params).localize(lang)
        return loc["title"], loc["message"]
    text = TEXT.get(key)
    if text is None:
        return None
    vals = {}
    for k, v in params.items():
        said = _value(k, v, lang)
        vals[k] = f"⁨{said}⁩" if lang in _RTL and said and k not in _UNISOLATED else said
    try:
        return text["title"][lang].format(**vals), text["message"][lang].format(**vals)
    except (KeyError, IndexError, ValueError):
        return None
