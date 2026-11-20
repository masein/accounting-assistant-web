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
}
