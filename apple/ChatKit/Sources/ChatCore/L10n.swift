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

    public enum Key: String, CaseIterable, Sendable {
        case appName, server, username, password, signIn, signingIn, twoFactorTitle, twoFactorHint, code, verify
        case conversations, newConversation, noConversations, signOut, signOutConfirm, cancelAction
        case placeholder, send, thinking, confirm, cancel, posted, cancelled, expired, needsApproval
        case debit, credit, emptyChatTitle, emptyChatHint, retry, untitled, undo, undone, asOf
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
        ],
    ]
}
