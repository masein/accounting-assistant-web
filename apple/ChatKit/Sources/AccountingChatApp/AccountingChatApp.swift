import ChatKit
import SwiftUI

/// The chat app for iPhone, iPad and Mac: the same assistant as the web chat, nothing else (apple/README.md).
/// Everything it does is in ChatKit; this is only its entry point (the Xcode project and apple/build-mac.sh both
/// build it).
@main
struct AccountingChatApp: App {
    @StateObject private var app: AppModel = {
        #if DEBUG
        if Automation.isOn {
            // a scripted run keeps its session in memory: it never touches the Keychain
            let app = AppModel(tokens: { _ in MemoryTokenStore() })
            app.startFresh(server: Automation.env["AC_SERVER"] ?? "")
            return app
        }
        #endif
        return AppModel()
    }()

    var body: some Scene {
        WindowGroup {
            RootView(app: app)
                #if os(macOS)
                .frame(minWidth: 720, minHeight: 520)
                #endif
                #if DEBUG
                .task { await Automation.run(app) }
                #endif
        }
        #if os(macOS)
        .defaultSize(width: 1000, height: 720)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Conversation") { app.chat?.newConversation() }
                    .keyboardShortcut("n")
                    .disabled(app.chat == nil)
            }
        }
        #endif
    }
}
