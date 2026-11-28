import SwiftUI

/// The web app's colours (app/static/css/app.css: --primary #006d77, --accent #f4a261).
public enum Theme {
    public static let primary = Color(red: 0x00 / 255, green: 0x6d / 255, blue: 0x77 / 255)
    public static let accent = Color(red: 0xf4 / 255, green: 0xa2 / 255, blue: 0x61 / 255)
    static let userBubble = primary
    #if os(iOS)
    static let assistantBubble = Color(uiColor: .secondarySystemBackground)
    static let card = Color(uiColor: .systemBackground)
    static let canvas = Color(uiColor: .systemGroupedBackground)
    #else
    static let assistantBubble = Color(nsColor: .controlBackgroundColor)
    static let card = Color(nsColor: .textBackgroundColor)
    static let canvas = Color(nsColor: .windowBackgroundColor)
    #endif
}

extension String {
    /// Persian or Arabic letters: the text reads right to left whatever the app's language.
    var isRTLScript: Bool {
        unicodeScalars.contains { (0x0600...0x06FF).contains($0.value) || (0xFB50...0xFDFF).contains($0.value) }
    }
}

enum Format {
    static func money(_ m: Money, l10n: L10n) -> String {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.locale = l10n.locale
        f.currencyCode = m.currency ?? "GBP"
        let places = ["IRR", "IRT", "JPY", "KRW"].contains(m.currency ?? "") ? 0 : 2
        f.minimumFractionDigits = places
        f.maximumFractionDigits = places
        return f.string(from: m.value as NSDecimalNumber) ?? "\(m.value) \(m.currency ?? "")"
    }

    static func amount(_ d: Decimal, l10n: L10n) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.locale = l10n.locale
        f.maximumFractionDigits = 2
        return f.string(from: d as NSDecimalNumber) ?? "\(d)"
    }

    /// The currency's places: none for rials, three for Omani rials.
    static func places(_ currency: String?) -> Int {
        switch (currency ?? "").uppercased() {
        case "IRR", "IRT", "JPY", "KRW", "IQD": return 0
        case "OMR", "BHD", "KWD", "JOD", "TND", "LYD": return 3
        default: return 2
        }
    }

    /// A bare amount in its currency's places (the currency named once above, as a table does).
    static func amount(_ d: Decimal, l10n: L10n, currency: String?) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.locale = l10n.locale
        f.maximumFractionDigits = currency == nil ? 2 : places(currency)
        return f.string(from: d as NSDecimalNumber) ?? "\(d)"
    }

    /// "+1,200" or "−300": what a holding gained or lost.
    static func signed(_ d: Decimal, l10n: L10n, currency: String?) -> String {
        (d > 0 ? "+" : d < 0 ? "−" : "") + amount(d < 0 ? -d : d, l10n: l10n, currency: currency)
    }

    /// "Iranian rial", «ریال ایران»: a currency's name in the reader's language, else its code.
    static func currencyName(_ code: String, l10n: L10n) -> String {
        l10n.locale.localizedString(forCurrencyCode: code) ?? code
    }

    static func percent(_ fraction: Double, l10n: L10n) -> String {
        let f = NumberFormatter()
        f.numberStyle = .percent
        f.locale = l10n.locale
        f.maximumFractionDigits = 0
        return f.string(from: NSNumber(value: fraction)) ?? "\(Int(fraction * 100))%"
    }
}
