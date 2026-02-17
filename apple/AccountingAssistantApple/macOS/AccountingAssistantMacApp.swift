import SwiftUI

@main
struct AccountingAssistantMacApp: App {
    var body: some Scene {
        WindowGroup("Accounting Assistant") {
            AppleChatRootView()
                .frame(minWidth: 900, minHeight: 680)
        }
    }
}
