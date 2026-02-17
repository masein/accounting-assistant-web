import SwiftUI

@main
struct AccountingAssistantIOSApp: App {
    var body: some Scene {
        WindowGroup {
            ZStack {
                Color.black.ignoresSafeArea()
                AppleChatRootView()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
    }
}
