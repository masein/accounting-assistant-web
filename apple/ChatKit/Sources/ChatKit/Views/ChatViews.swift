import SwiftUI

/// After sign-in: the conversations beside the open one (a sidebar on the Mac and iPad, a stack on the iPhone).
/// The chat screen's own switches (an object rather than `@State`: see LoginFields).
final class ChatScreenState: ObservableObject {
    @Published var confirmingSignOut = false
    @Published var columns = NavigationSplitViewVisibility.all
}

public struct ChatScreen: View {
    @ObservedObject var app: AppModel
    @ObservedObject var chat: ChatModel
    @StateObject private var ui = ChatScreenState()

    public init(app: AppModel, chat: ChatModel) {
        self.app = app
        self.chat = chat
    }

    private var t: L10n { app.l10n }

    public var body: some View {
        NavigationSplitView(columnVisibility: $ui.columns) {
            ThreadList(chat: chat, l10n: t)
                .navigationTitle(t[.conversations])
                .toolbar {
                    ToolbarItem {
                        Button { chat.newConversation() } label: { Label(t[.newConversation], systemImage: "square.and.pencil") }
                            .accessibilityIdentifier("new-conversation")
                    }
                    ToolbarItem {
                        Menu {
                            if let name = app.user?.username { Text(name) }
                            if let company = app.company?.name { Text(company) }
                            Divider()
                            Button(t[.signOut], role: .destructive) { ui.confirmingSignOut = true }
                        } label: { Label(app.user?.username ?? t[.signOut], systemImage: "person.crop.circle") }
                    }
                }
        } detail: {
            ConversationView(chat: chat, l10n: t)
                .navigationTitle(chat.threads.first { $0.id == chat.thread }?.title ?? t[.newConversation])
        }
        .tint(Theme.primary)
        .confirmationDialog(t(.signOutConfirm, app.company?.name ?? app.serverAddress), isPresented: $ui.confirmingSignOut) {
            Button(t[.signOut], role: .destructive) { Task { await app.signOut() } }
        }
        .task { await chat.refreshThreads() }
    }
}

struct ThreadList: View {
    @ObservedObject var chat: ChatModel
    let l10n: L10n

    var body: some View {
        List(selection: Binding(get: { chat.thread }, set: { id in Task { await chat.open(id) } })) {
            if chat.threads.isEmpty {
                Text(l10n[.noConversations]).foregroundStyle(.secondary)
            }
            ForEach(chat.threads) { thread in
                VStack(alignment: .leading, spacing: 2) {
                    Text(thread.title?.isEmpty == false ? thread.title! : l10n[.untitled]).lineLimit(1)
                    if let when = Self.when(thread.updatedAt, l10n) {
                        Text(when).font(.caption).foregroundStyle(.secondary)
                    }
                }
                .tag(Optional(thread.id))
                .accessibilityIdentifier("thread")
            }
        }
        .refreshable { await chat.refreshThreads() }
    }

    /// "5 minutes ago", "yesterday" — when the conversation last moved, in the user's language.
    static func when(_ iso: String?, _ l10n: L10n) -> String? {
        guard let iso else { return nil }
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = f.date(from: iso) ?? ISO8601DateFormatter().date(from: iso) else { return nil }
        let r = RelativeDateTimeFormatter()
        r.locale = l10n.locale
        r.unitsStyle = .full
        return r.localizedString(for: date, relativeTo: Date())
    }
}

public struct ConversationView: View {
    @ObservedObject var chat: ChatModel
    let l10n: L10n
    @FocusState private var composerFocused: Bool

    public init(chat: ChatModel, l10n: L10n) {
        self.chat = chat
        self.l10n = l10n
    }

    public var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        if chat.messages.isEmpty && !chat.loading {
                            EmptyChat(l10n: l10n)
                        }
                        ForEach(chat.messages) { message in
                            MessageRow(message: message, chat: chat, l10n: l10n).id(message.id)
                        }
                        if chat.sending {
                            HStack(spacing: 8) {
                                ProgressView().controlSize(.small)
                                Text(l10n[.thinking]).foregroundStyle(.secondary)
                            }
                            .id("thinking")
                            .accessibilityIdentifier("thinking")
                        }
                        if let error = chat.error {
                            HStack {
                                Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                                if chat.canRetry {
                                    Button(l10n[.retry]) { Task { await chat.retry() } }
                                }
                            }
                            .font(.callout)
                            .accessibilityIdentifier("chat-error")
                        }
                    }
                    .padding(16)
                    .frame(maxWidth: 820)
                    .frame(maxWidth: .infinity)
                }
                .onChange(of: chat.messages.count) { _, _ in
                    withAnimation { proxy.scrollTo(chat.sending ? "thinking" : chat.messages.last?.id, anchor: .bottom) }
                }
                .onChange(of: chat.sending) { _, sending in
                    if sending { withAnimation { proxy.scrollTo("thinking", anchor: .bottom) } }
                }
            }
            Divider()
            Composer(chat: chat, l10n: l10n, focused: $composerFocused)
        }
        .background(Theme.canvas)
        .environment(\.layoutDirection, l10n.isRTL ? .rightToLeft : .leftToRight)
    }
}

struct MessageRow: View {
    let message: ChatMessage
    @ObservedObject var chat: ChatModel
    let l10n: L10n

    var body: some View {
        if message.isUser {
            HStack {
                Spacer(minLength: 40)
                UserBubble(text: message.text ?? "")
            }
        } else {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(message.blocks ?? []) { block in
                    BlockView(block: block, l10n: l10n, working: isWorking(block),
                              onConfirm: { token in Task { await chat.confirm(token) } },
                              onCancel: { token in Task { await chat.cancel(token) } },
                              onUndo: { id in Task { await chat.undo(id) } })
                }
                if (message.blocks ?? []).isEmpty, let text = message.text {
                    AssistantBubble(text: text)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func isWorking(_ block: Block) -> Bool {
        switch block {
        case .proposal(_, let p): return chat.working.contains(p.token)
        case .posted(_, let p, _): return p.auditLogId.map { chat.working.contains($0) } ?? false
        default: return false
        }
    }
}

struct Composer: View {
    @ObservedObject var chat: ChatModel
    let l10n: L10n
    var focused: FocusState<Bool>.Binding

    var body: some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField(l10n[.placeholder], text: $chat.draft, axis: .vertical)
                .lineLimit(1...6)
                .textFieldStyle(.plain)
                .focused(focused)
                .padding(.horizontal, 12)
                .padding(.vertical, 9)
                .background(RoundedRectangle(cornerRadius: 18).fill(Theme.card))
                .overlay(RoundedRectangle(cornerRadius: 18).stroke(.quaternary))
                .onSubmit { Task { await chat.send() } }
                .accessibilityIdentifier("composer")
            Button {
                Task { await chat.send() }
            } label: {
                Image(systemName: "arrow.up.circle.fill").font(.system(size: 30))
            }
            .buttonStyle(.plain)
            .foregroundStyle(chat.canSend ? Theme.primary : Color.secondary)
            .disabled(!chat.canSend)
            .keyboardShortcut(.return, modifiers: .command)
            .accessibilityLabel(l10n[.send])
            .accessibilityIdentifier("send")
        }
        .padding(12)
        .background(.bar)
    }
}

struct EmptyChat: View {
    let l10n: L10n

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "text.bubble").font(.system(size: 36)).foregroundStyle(Theme.primary)
            Text(l10n[.emptyChatTitle]).font(.title3.bold())
            Text(l10n[.emptyChatHint]).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 60)
    }
}

/// The app: the sign-in screen, or the chat.
public struct RootView: View {
    @ObservedObject var app: AppModel

    public init(app: AppModel) { self.app = app }

    public var body: some View {
        Group {
            switch app.phase {
            case .starting:
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            case .signedOut, .twoFactor:
                LoginView(app: app)
            case .signedIn:
                if let chat = app.chat { ChatScreen(app: app, chat: chat) }
            }
        }
        .environment(\.layoutDirection, app.l10n.isRTL ? .rightToLeft : .leftToRight)
        .task { if app.phase == .starting { await app.restore() } }
    }
}
