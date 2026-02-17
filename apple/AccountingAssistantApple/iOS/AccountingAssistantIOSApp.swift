import SwiftUI

@main
struct AccountingAssistantIOSApp: App {
    var body: some Scene {
        WindowGroup {
            GeometryReader { proxy in
                AppleChatRootView()
                    .frame(width: proxy.size.width, height: proxy.size.height, alignment: .top)
                    .background(Color.clear)
            }
            .ignoresSafeArea()
        }
    }
}
