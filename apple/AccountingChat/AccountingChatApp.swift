import ChatKit
import SwiftUI

/// The chat app for iPhone, iPad and Mac: the same assistant as the web chat, nothing else (apple/README.md).
/// Everything it does is in the ChatKit package; this is only its entry point.
@main
struct AccountingChatApp: App {
    @StateObject private var app: AppModel = {
        let app = AppModel()
        #if DEBUG
        if Automation.isOn { app.startFresh(server: Automation.env["AC_SERVER"] ?? "") }
        #endif
        return app
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
