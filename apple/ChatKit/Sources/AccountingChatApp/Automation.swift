#if DEBUG
import ChatKit
import SwiftUI
#if os(macOS)
import AppKit
#endif

/// A scripted run for end-to-end checks and screenshots (debug builds only; apple/README.md "Screenshots").
/// Set in the launch environment:
///   AC_SERVER, AC_USER, AC_PASS   sign in to that server as that user
///   AC_OPEN=1                     open the latest conversation (and its page before, if there is one)
///   AC_SEND                       messages to send, one after another, separated by "|"
///   AC_CONFIRM=1 / AC_CANCEL=1    confirm / cancel the first card the assistant proposes
///   AC_SUPPORT=1, or messages     open Support (the badge first, then the conversation), and send these to the
///                                 desk, separated by "|"
///   AC_SUPPORT_FILE               a screenshot or a PDF to attach to the first of them
///   AC_SUPPORT_WAIT               seconds to wait, Support open, for the desk's reply (shot when it shows)
///   AC_SNAPSHOT_DIR               on the Mac: after each step the window is saved there as NN-step.png
///                                 (the app draws its own window — or its sheet — no screen-recording permission)
@MainActor
enum Automation {
    static var env: [String: String] { ProcessInfo.processInfo.environment }
    static var isOn: Bool { env["AC_SERVER"] != nil }

    static func run(_ app: AppModel) async {
        guard isOn else { return }
        var step = 0
        func shot(_ name: String, toolbar: Bool = false) async {
            step += 1
            try? await Task.sleep(for: .milliseconds(900))            // let the screen settle
            snapshot(String(format: "%02d-%@", step, name), toolbar: toolbar)
        }
        await shot("start")
        await app.signIn(username: env["AC_USER"] ?? "", password: env["AC_PASS"] ?? "")
        await shot("signed-in")
        guard let chat = app.chat else { return }
        await chat.refreshThreads()
        if env["AC_OPEN"] == "1", let latest = chat.threads.first {
            await chat.open(latest.id)
            await shot("opened")
            if chat.moreBefore {
                await chat.loadEarlier()
                await shot("earlier")
            }
        }
        for message in (env["AC_SEND"] ?? "").split(separator: "|").map(String.init) where !message.isEmpty {
            chat.draft = message
            await shot("typed")
            let sent = Task { await chat.send() }
            await shot("answering")                                   // the step the server says it is on
            await sent.value
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
        if let wanted = env["AC_SUPPORT"], !wanted.isEmpty, let support = app.support {
            await support.refresh()
            await shot("support-badge", toolbar: true)                // the headset, its unread replies counted
            support.language = app.l10n.language
            support.presented = true
            await shot("support-open")
            if let path = env["AC_SUPPORT_FILE"], let data = FileManager.default.contents(atPath: path) {
                let name = (path as NSString).lastPathComponent
                await support.attach(data, name: name, mime: Attachments.mime(forFileName: name))
                await shot("support-attached")
            }
            for message in wanted.split(separator: "|").map(String.init) where wanted != "1" && !message.isEmpty {
                support.draft = message
                await support.send()
                await shot("support-sent")
            }
            if let wait = Double(env["AC_SUPPORT_WAIT"] ?? ""), wait > 0 {
                let mine = support.messages.count
                let until = Date().addingTimeInterval(wait)
                while Date() < until, !(support.messages.count > mine && support.messages.last?.fromDesk == true) {
                    try? await Task.sleep(for: .seconds(1))
                }
                await shot("support-reply")
            }
            print("SUPPORT \(support.messages.count) messages, unread \(support.unread), online \(support.online)")
        }
        print("AUTOMATION DONE \(step) steps")
        fflush(stdout)
    }

    private static func firstCard(in chat: ChatModel) -> String? {
        for message in chat.messages {
            for block in message.blocks ?? [] {
                if case .proposal(_, let p) = block, p.isPending { return p.token }
            }
        }
        return nil
    }

    /// The window (its sheet, when one shows); `toolbar`: with its title bar and toolbar too.
    private static func snapshot(_ name: String, toolbar: Bool = false) {
        #if os(macOS)
        let main = NSApp.windows.first { $0.isVisible && $0.sheetParent == nil }
        let content = (main?.attachedSheet ?? main)?.contentView
        guard let dir = env["AC_SNAPSHOT_DIR"], let view = toolbar ? (content?.superview ?? content) : content,
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
