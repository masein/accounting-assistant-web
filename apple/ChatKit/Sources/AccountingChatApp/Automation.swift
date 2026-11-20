#if DEBUG
import ChatKit
import SwiftUI
#if os(macOS)
import AppKit
#endif

/// A scripted run for end-to-end checks and screenshots (debug builds only; apple/README.md "Screenshots").
/// Set in the launch environment:
///   AC_SERVER, AC_USER, AC_PASS   sign in to that server as that user
///   AC_SEND                       messages to send, one after another, separated by "|"
///   AC_CONFIRM=1 / AC_CANCEL=1    confirm / cancel the first card the assistant proposes
///   AC_SNAPSHOT_DIR               on the Mac: after each step the window is saved there as NN-step.png
///                                 (the app draws its own window: no screen-recording permission needed)
@MainActor
enum Automation {
    static var env: [String: String] { ProcessInfo.processInfo.environment }
    static var isOn: Bool { env["AC_SERVER"] != nil }

    static func run(_ app: AppModel) async {
        guard isOn else { return }
        var step = 0
        func shot(_ name: String) async {
            step += 1
            try? await Task.sleep(for: .milliseconds(900))            // let the screen settle
            snapshot(String(format: "%02d-%@", step, name))
        }
        await shot("start")
        await app.signIn(username: env["AC_USER"] ?? "", password: env["AC_PASS"] ?? "")
        await shot("signed-in")
        guard let chat = app.chat else { return }
        await chat.refreshThreads()
        for message in (env["AC_SEND"] ?? "").split(separator: "|").map(String.init) where !message.isEmpty {
            chat.draft = message
            await shot("typed")
            await chat.send()
            await shot("answered")
        }
        if env["AC_CONFIRM"] == "1", let token = firstCard(in: chat) {
            await chat.confirm(token)
            await shot("confirmed")
        }
        if env["AC_CANCEL"] == "1", let token = firstCard(in: chat) {
            await chat.cancel(token)
            await shot("cancelled")
        }
        print("AUTOMATION DONE \(step) steps")
    }

    private static func firstCard(in chat: ChatModel) -> String? {
        for message in chat.messages {
            for block in message.blocks ?? [] {
                if case .proposal(_, let p) = block, p.isPending { return p.token }
            }
        }
        return nil
    }

    private static func snapshot(_ name: String) {
        #if os(macOS)
        guard let dir = env["AC_SNAPSHOT_DIR"], let view = NSApp.windows.first(where: \.isVisible)?.contentView,
              let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else { return }
        let url = URL(fileURLWithPath: dir).appendingPathComponent("\(name).png")
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        try? png.write(to: url)
        print("SNAPSHOT \(url.path)")
        #endif
    }
}
#endif
