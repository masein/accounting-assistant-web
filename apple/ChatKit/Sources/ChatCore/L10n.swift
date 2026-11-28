import Foundation

/// The app's words, in the web app's four languages. The language is the user's own (their setting on the server,
/// as the web and the bot use it), else the device's; Persian and Arabic lay out right to left.
public struct L10n: Equatable, Sendable {
    public let language: String

    public static let supported = ["en", "fa", "ar", "es"]

    public init(_ language: String?) {
        let code = String((language ?? "en").lowercased().prefix(2))
        self.language = Self.supported.contains(code) ? code : "en"
    }

    public var isRTL: Bool { language == "fa" || language == "ar" }
    public var locale: Locale { Locale(identifier: language) }

    public subscript(_ key: Key) -> String { Self.table[language]?[key] ?? Self.table["en"]![key]! }

    public func callAsFunction(_ key: Key, _ args: CVarArg...) -> String {
        args.isEmpty ? self[key] : String(format: self[key], locale: locale, arguments: args)
    }

    /// Where a card stands, in words: never the server's own word ("waiting_for_approval") on a card.
    public func phase(_ phase: Proposal.Phase) -> String {
        switch phase {
        case .draft: return self[.stateDraft]
        case .waiting: return self[.stateWaiting]
        case .posted: return self[.posted]
        case .undone: return self[.undone]
        case .cancelled: return self[.cancelled]
        case .rejected: return self[.stateRejected]
        case .expired: return self[.expired]
        case .replaced: return self[.stateReplaced]
        case .closed: return self[.stateClosed]
        }
    }

    /// A notice under the conversation, in words.
    public func notice(_ notice: ChatNotice) -> String {
        switch notice {
        case .uploadFailed: return self[.uploadFailed]
        case .fileFailed: return self[.fileFailed]
        case .voiceFailed: return self[.voiceFailed]
        case .voiceTooShort: return self[.voiceTooShort]
        case .microphoneOff: return self[.micDenied]
        }
    }

    public enum Key: String, CaseIterable, Sendable {
        case appName, server, username, password, signIn, signingIn, twoFactorTitle, twoFactorHint, code, verify
        case conversations, newConversation, noConversations, signOut, signOutConfirm, cancelAction
        case placeholder, send, thinking, confirm, cancel, posted, cancelled, expired, needsApproval
        case debit, credit, emptyChatTitle, emptyChatHint, retry, untitled, undo, undone, asOf
        // where a card stands
        case stateDraft, stateWaiting, stateRejected, stateReplaced, stateClosed
        // approvals
        case approve, reject, withdraw, askedBy, yourRequest, rejectTitle, rejectReason, approvals, noApprovals
        // paging, tables, files
        case earlier, budgetOf, due, fileOpen, fileShare, fileFailed, downloading
        // attachments and voice
        case attach, attachPhoto, attachFile, uploading, uploadFailed, removeAttachment
        case holdToTalk, listening, transcribing, voiceFailed, voiceTooShort, micDenied
        // an app older than the server's minimum
        case updateTitle, updateRequired, done
    }

    static let table: [String: [Key: String]] = [
        "en": [
            .appName: "Accounting Assistant", .server: "Server", .username: "Username", .password: "Password",
            .signIn: "Sign in", .signingIn: "Signing in…", .twoFactorTitle: "Two-factor sign-in",
            .twoFactorHint: "Enter the 6-digit code from your authenticator app, or a recovery code.",
            .code: "Code", .verify: "Verify", .conversations: "Conversations", .newConversation: "New conversation",
            .noConversations: "No conversations yet", .signOut: "Sign out",
            .signOutConfirm: "Sign out of %@ on this device?", .cancelAction: "Cancel",
            .placeholder: "Tell me what you spent or earned…", .send: "Send", .thinking: "Thinking…",
            .confirm: "Confirm", .cancel: "Cancel", .posted: "Recorded", .cancelled: "Cancelled — nothing was saved",
            .expired: "This card has expired — ask again", .needsApproval: "Needs approval",
            .debit: "Debit", .credit: "Credit", .emptyChatTitle: "Ask your accountant",
            .emptyChatHint: "For example: \"paid 20 for lunch from the card\", or \"how much cash do we have?\"",
            .retry: "Try again", .untitled: "Conversation",
            .undo: "Undo", .undone: "Undone — the books are as before", .asOf: "as of %@",
            .stateDraft: "Not recorded yet", .stateWaiting: "Waiting for a second person's approval",
            .stateRejected: "Rejected", .stateReplaced: "Changed — the new card follows",
            .stateClosed: "No longer open",
            .approve: "Approve", .reject: "Reject", .withdraw: "Withdraw",
            .askedBy: "Waiting for your approval · asked by %@", .yourRequest: "Waiting for approval · you asked",
            .rejectTitle: "Reject this voucher?", .rejectReason: "Reason (optional)", .approvals: "Approvals",
            .noApprovals: "Nothing waits for approval",
            .earlier: "Earlier messages", .budgetOf: "%@ of %@", .due: "due %@",
            .fileOpen: "Open", .fileShare: "Share",
            .fileFailed: "The document didn't download. Check the connection and try again.",
            .downloading: "Downloading…",
            .attach: "Attach", .attachPhoto: "Choose a photo", .attachFile: "Choose a file (PDF, Excel, CSV)",
            .uploading: "Uploading…", .uploadFailed: "The file didn't upload. Check the connection and try again.",
            .removeAttachment: "Remove",
            .holdToTalk: "Hold to talk", .listening: "Listening… let go to stop",
            .transcribing: "Writing down what you said…",
            .voiceFailed: "Couldn't make out the voice note. Try again, or type it.",
            .voiceTooShort: "Hold the microphone down while you speak.",
            .micDenied: "The microphone is off for this app. Turn it on in Settings to record.",
            .updateTitle: "Update the app", .done: "Done",
            .updateRequired: "This version of the app is too old. Update it to keep going.",
        ],
        "fa": [
            .appName: "دستیار حسابداری", .server: "سرور", .username: "نام کاربری", .password: "گذرواژه",
            .signIn: "ورود", .signingIn: "در حال ورود…", .twoFactorTitle: "ورود دومرحله‌ای",
            .twoFactorHint: "کد ۶ رقمی برنامهٔ احراز هویت یا یک کد بازیابی را وارد کنید.",
            .code: "کد", .verify: "تأیید", .conversations: "گفتگوها", .newConversation: "گفتگوی جدید",
            .noConversations: "هنوز گفتگویی نیست", .signOut: "خروج",
            .signOutConfirm: "از %@ روی این دستگاه خارج می‌شوید؟", .cancelAction: "انصراف",
            .placeholder: "بگویید چه خرج یا دریافتی داشتید…", .send: "ارسال", .thinking: "در حال فکر کردن…",
            .confirm: "تأیید", .cancel: "انصراف", .posted: "ثبت شد", .cancelled: "لغو شد — چیزی ثبت نشد",
            .expired: "این کارت منقضی شده — دوباره بپرسید", .needsApproval: "نیاز به تأیید",
            .debit: "بدهکار", .credit: "بستانکار", .emptyChatTitle: "از حسابدارتان بپرسید",
            .emptyChatHint: "مثلاً: «۵۰ هزار تومان نون از کارت» یا «چقدر پول نقد داریم؟»",
            .retry: "دوباره", .untitled: "گفتگو",
            .undo: "برگرداندن", .undone: "برگردانده شد — دفاتر مثل قبل است", .asOf: "تا %@",
            .stateDraft: "هنوز ثبت نشده", .stateWaiting: "در انتظار تأیید دوم",
            .stateRejected: "رد شد", .stateReplaced: "تغییر کرد؛ سند جدید پایین‌تر است.",
            .stateClosed: "دیگر باز نیست",
            .approve: "تأیید", .reject: "رد", .withdraw: "لغو درخواست",
            .askedBy: "منتظر تأیید شما، به درخواست %@", .yourRequest: "منتظر تأیید؛ درخواست خودتان",
            .rejectTitle: "این سند رد شود؟", .rejectReason: "دلیل (اختیاری)", .approvals: "تأییدها",
            .noApprovals: "چیزی منتظر تأیید نیست",
            .earlier: "پیام‌های قبلی", .budgetOf: "%@ از %@", .due: "سررسید %@",
            .fileOpen: "باز کردن", .fileShare: "اشتراک‌گذاری",
            .fileFailed: "فایل دانلود نشد. اتصال اینترنت را بررسی کنید و دوباره امتحان کنید.",
            .downloading: "در حال دانلود…",
            .attach: "پیوست", .attachPhoto: "انتخاب عکس", .attachFile: "انتخاب فایل (PDF، اکسل، CSV)",
            .uploading: "در حال بارگذاری…",
            .uploadFailed: "فایل بارگذاری نشد. اتصال اینترنت را بررسی کنید و دوباره امتحان کنید.",
            .removeAttachment: "حذف",
            .holdToTalk: "برای ضبط نگه دارید", .listening: "در حال ضبط… برای پایان، رها کنید",
            .transcribing: "در حال تبدیل صدا به متن…",
            .voiceFailed: "پیام صوتی واضح نبود. دوباره بگویید یا بنویسید.",
            .voiceTooShort: "تا وقتی حرف می‌زنید، دکمهٔ میکروفون را نگه دارید.",
            .micDenied: "میکروفون برای این برنامه خاموش است. برای ضبط، آن را در تنظیمات روشن کنید.",
            .updateTitle: "برنامه را به‌روز کنید", .done: "بستن",
            .updateRequired: "این نسخهٔ برنامه قدیمی است. برای ادامه، برنامه را به‌روزرسانی کنید.",
        ],
        "ar": [
            .appName: "مساعد المحاسبة", .server: "الخادم", .username: "اسم المستخدم", .password: "كلمة المرور",
            .signIn: "تسجيل الدخول", .signingIn: "جارٍ تسجيل الدخول…", .twoFactorTitle: "الدخول بخطوتين",
            .twoFactorHint: "أدخل الرمز المكوّن من 6 أرقام من تطبيق المصادقة، أو رمز استرداد.",
            .code: "الرمز", .verify: "تحقق", .conversations: "المحادثات", .newConversation: "محادثة جديدة",
            .noConversations: "لا توجد محادثات بعد", .signOut: "تسجيل الخروج",
            .signOutConfirm: "تسجيل الخروج من %@ على هذا الجهاز؟", .cancelAction: "إلغاء",
            .placeholder: "أخبرني بما أنفقت أو كسبت…", .send: "إرسال", .thinking: "يفكّر…",
            .confirm: "تأكيد", .cancel: "إلغاء", .posted: "تم التسجيل", .cancelled: "أُلغي — لم يُحفظ شيء",
            .expired: "انتهت صلاحية هذه البطاقة — اسأل مجددًا", .needsApproval: "يحتاج إلى موافقة",
            .debit: "مدين", .credit: "دائن", .emptyChatTitle: "اسأل محاسبك",
            .emptyChatHint: "مثلًا: «دفعت 20 للغداء بالبطاقة» أو «كم لدينا من النقد؟»",
            .retry: "أعد المحاولة", .untitled: "محادثة",
            .undo: "تراجع", .undone: "تم التراجع — الدفاتر كما كانت", .asOf: "حتى %@",
            .stateDraft: "لم يُسجَّل بعد", .stateWaiting: "بانتظار موافقة ثانية",
            .stateRejected: "مرفوض", .stateReplaced: "تم التعديل؛ القيد الجديد بعده.",
            .stateClosed: "لم يعد مفتوحًا",
            .approve: "موافقة", .reject: "رفض", .withdraw: "سحب",
            .askedBy: "بانتظار موافقتك · طلبه %@", .yourRequest: "بانتظار الموافقة · طلبتَه أنت",
            .rejectTitle: "رفض هذا القيد؟", .rejectReason: "السبب (اختياري)", .approvals: "الموافقات",
            .noApprovals: "لا شيء بانتظار الموافقة",
            .earlier: "رسائل سابقة", .budgetOf: "%@ من %@", .due: "يستحق في %@",
            .fileOpen: "فتح", .fileShare: "مشاركة",
            .fileFailed: "لم يُنزَّل المستند. تحقق من الاتصال وحاول مرة أخرى.",
            .downloading: "جارٍ التنزيل…",
            .attach: "إرفاق", .attachPhoto: "اختر صورة", .attachFile: "اختر ملفًا (PDF، Excel، CSV)",
            .uploading: "جارٍ الرفع…", .uploadFailed: "لم يُرفع الملف. تحقق من الاتصال وحاول مرة أخرى.",
            .removeAttachment: "إزالة",
            .holdToTalk: "اضغط مطولًا للتحدث", .listening: "أستمع… اترك للإيقاف",
            .transcribing: "أكتب ما قلته…",
            .voiceFailed: "تعذّر فهم الرسالة الصوتية. حاول مرة أخرى أو اكتبها.",
            .voiceTooShort: "أبقِ الميكروفون مضغوطًا أثناء الكلام.",
            .micDenied: "الميكروفون مغلق لهذا التطبيق. شغّله من الإعدادات للتسجيل.",
            .updateTitle: "حدّث التطبيق", .done: "تم",
            .updateRequired: "هذه النسخة من التطبيق قديمة. حدّثها للمتابعة.",
        ],
        "es": [
            .appName: "Asistente contable", .server: "Servidor", .username: "Usuario", .password: "Contraseña",
            .signIn: "Entrar", .signingIn: "Entrando…", .twoFactorTitle: "Inicio de sesión en dos pasos",
            .twoFactorHint: "Escribe el código de 6 cifras de tu app de autenticación, o un código de recuperación.",
            .code: "Código", .verify: "Verificar", .conversations: "Conversaciones",
            .newConversation: "Nueva conversación", .noConversations: "Aún no hay conversaciones",
            .signOut: "Cerrar sesión", .signOutConfirm: "¿Cerrar la sesión de %@ en este dispositivo?",
            .cancelAction: "Cancelar", .placeholder: "Cuéntame qué gastaste o cobraste…", .send: "Enviar",
            .thinking: "Pensando…", .confirm: "Confirmar", .cancel: "Cancelar", .posted: "Registrado",
            .cancelled: "Cancelado: no se guardó nada", .expired: "Esta tarjeta caducó: vuelve a preguntar",
            .needsApproval: "Necesita aprobación", .debit: "Debe", .credit: "Haber",
            .emptyChatTitle: "Pregúntale a tu contable",
            .emptyChatHint: "Por ejemplo: «pagué 20 por el almuerzo con la tarjeta» o «¿cuánto efectivo tenemos?»",
            .retry: "Reintentar", .untitled: "Conversación",
            .undo: "Deshacer", .undone: "Deshecho: los libros quedan como antes", .asOf: "al %@",
            .stateDraft: "Aún sin registrar", .stateWaiting: "Esperando una segunda aprobación",
            .stateRejected: "Rechazado", .stateReplaced: "Cambiado. El nuevo asiento va a continuación.",
            .stateClosed: "Ya no está abierto",
            .approve: "Aprobar", .reject: "Rechazar", .withdraw: "Retirar",
            .askedBy: "Espera tu aprobación · lo pidió %@", .yourRequest: "Espera aprobación · lo pediste tú",
            .rejectTitle: "¿Rechazar este asiento?", .rejectReason: "Motivo (opcional)", .approvals: "Aprobaciones",
            .noApprovals: "Nada espera aprobación",
            .earlier: "Mensajes anteriores", .budgetOf: "%@ de %@", .due: "vence el %@",
            .fileOpen: "Abrir", .fileShare: "Compartir",
            .fileFailed: "El documento no se descargó. Revisa la conexión y vuelve a intentarlo.",
            .downloading: "Descargando…",
            .attach: "Adjuntar", .attachPhoto: "Elegir una foto", .attachFile: "Elegir un archivo (PDF, Excel, CSV)",
            .uploading: "Subiendo…", .uploadFailed: "El archivo no se subió. Revisa la conexión y vuelve a intentarlo.",
            .removeAttachment: "Quitar",
            .holdToTalk: "Mantén pulsado para hablar", .listening: "Escuchando… suelta para terminar",
            .transcribing: "Escribiendo lo que dijiste…",
            .voiceFailed: "No se entendió la nota de voz. Inténtalo de nuevo o escríbelo.",
            .voiceTooShort: "Mantén pulsado el micrófono mientras hablas.",
            .micDenied: "El micrófono está desactivado para esta app. Actívalo en Ajustes para grabar.",
            .updateTitle: "Actualiza la app", .done: "Listo",
            .updateRequired: "Esta versión de la app es demasiado antigua. Actualízala para seguir.",
        ],
    ]
}
