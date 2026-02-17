import Foundation
import SwiftUI

enum AppConfig {
    static let defaultBackendURL = "http://localhost:8000"
    static let legacyBackendURL = "http://192.168.200.240:1234"
    static let userDefaultsBackendKey = "assistant.backend.url"
}

enum CurrencyFormatter {
    static func format(_ value: Int) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.maximumFractionDigits = 0
        return formatter.string(from: NSNumber(value: value)) ?? "\(value)"
    }

    static func compact(_ value: Double) -> String {
        let absValue = abs(value)
        switch absValue {
        case 1_000_000_000...:
            return String(format: "%.1fB", value / 1_000_000_000)
        case 1_000_000...:
            return String(format: "%.1fM", value / 1_000_000)
        case 1_000...:
            return String(format: "%.1fK", value / 1_000)
        default:
            return String(format: "%.0f", value)
        }
    }
}

extension Color {
    init(hex: UInt, alpha: Double = 1.0) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255.0,
            green: Double((hex >> 8) & 0xFF) / 255.0,
            blue: Double(hex & 0xFF) / 255.0,
            opacity: alpha
        )
    }
}
