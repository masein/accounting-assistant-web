import PhotosUI
import QuickLook
import SwiftUI
import UniformTypeIdentifiers

/// After sign-in: the conversations beside the open one (a sidebar on the Mac and iPad, a stack on the iPhone).
/// The chat screen's own switches (an object rather than `@State`: see LoginFields).
final class ChatScreenState: ObservableObject {
    @Published var confirmingSignOut = false
    @Published var showingApprovals = false
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
                        Button { ui.showingApprovals = true } label: { Label(t[.approvals], systemImage: "checkmark.seal") }
                            .accessibilityIdentifier("approvals")
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
        .sheet(isPresented: $ui.showingApprovals) {
            ApprovalsView(chat: chat, l10n: t, onClose: { ui.showingApprovals = false })
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

/// The conversation's own switches: whether it has settled at its latest message (older pages load only after, as
/// the reader scrolls up to them).
final class ConversationState: ObservableObject {
    var settled = false
}

public struct ConversationView: View {
    @ObservedObject var chat: ChatModel
    let l10n: L10n
    @StateObject private var prompts = CardPrompts()
    @StateObject private var state = ConversationState()
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
                        if chat.moreBefore {
                            EarlierRow(loading: chat.loadingEarlier, l10n: l10n) { earlier(proxy, asked: true) }
                                .id("earlier")
                                .onAppear { earlier(proxy, asked: false) }
                        }
                        if chat.messages.isEmpty && !chat.loading {
                            EmptyChat(l10n: l10n)
                        }
                        if chat.loading && chat.messages.isEmpty {
                            ProgressView().frame(maxWidth: .infinity).padding(.vertical, 40)
                        }
                        ForEach(chat.messages) { message in
                            MessageRow(message: message, chat: chat, l10n: l10n,
                                       actions: .of(chat, prompts: prompts)).id(message.id)
                        }
                        if chat.sending {
                            HStack(spacing: 8) {
                                ProgressView().controlSize(.small)
                                Text(chat.status ?? l10n[.thinking]).foregroundStyle(.secondary)
                                    .environment(\.layoutDirection, (chat.status ?? "").isRTLScript ? .rightToLeft : .leftToRight)
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
                .defaultScrollAnchor(.bottom)
                // the latest message changed (sent, answered, another conversation): to the bottom. An older page
                // put above leaves the reader where they were.
                .onChange(of: chat.messages.last?.id) { _, last in
                    withAnimation { proxy.scrollTo(chat.sending ? "thinking" : last, anchor: .bottom) }
                    state.settled = false
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { state.settled = true }
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
        .cardPrompts(prompts, chat: chat, l10n: l10n)
    }

    /// The page before: asked for (the button), or reached by scrolling up once the conversation has settled.
    private func earlier(_ proxy: ScrollViewProxy, asked: Bool) {
        guard asked || state.settled, !chat.loadingEarlier else { return }
        let anchor = chat.messages.first?.id
        Task {
            await chat.loadEarlier()
            if let anchor { proxy.scrollTo(anchor, anchor: .top) }
        }
    }
}

struct EarlierRow: View {
    let loading: Bool
    let l10n: L10n
    let onTap: () -> Void

    var body: some View {
        HStack {
            Spacer()
            if loading {
                ProgressView().controlSize(.small)
            } else {
                Button(l10n[.earlier], action: onTap).buttonStyle(.bordered).controlSize(.small)
            }
            Spacer()
        }
        .accessibilityIdentifier("earlier")
    }
}

extension View {
    /// The prompts a card opens: the reason for a rejection, and a document's preview.
    func cardPrompts(_ prompts: CardPrompts, chat: ChatModel, l10n: L10n) -> some View {
        modifier(CardPromptsModifier(prompts: prompts, chat: chat, l10n: l10n))
    }
}

struct CardPromptsModifier: ViewModifier {
    @ObservedObject var prompts: CardPrompts
    let chat: ChatModel
    let l10n: L10n

    func body(content: Content) -> some View {
        content
            .alert(l10n[.rejectTitle], isPresented: Binding(get: { prompts.rejecting != nil },
                                                            set: { if !$0 { prompts.rejecting = nil } })) {
                TextField(l10n[.rejectReason], text: $prompts.note)
                Button(l10n[.reject], role: .destructive) {
                    if let token = prompts.rejecting {
                        let note = prompts.note
                        Task { await chat.reject(token, note: note) }
                    }
                    prompts.rejecting = nil
                }
                Button(l10n[.cancelAction], role: .cancel) { prompts.rejecting = nil }
            }
            .quickLookPreview($prompts.preview)
    }
}

struct MessageRow: View {
    let message: ChatMessage
    @ObservedObject var chat: ChatModel
    let l10n: L10n
    let actions: CardActions

    var body: some View {
        if message.isUser {
            HStack {
                Spacer(minLength: 40)
                UserBubble(text: message.text ?? "", files: message.files ?? [])
            }
        } else {
            let blocks = message.blocks ?? []
            let cards = Set(blocks.compactMap { $0.card?.token })
            VStack(alignment: .leading, spacing: 8) {
                ForEach(blocks) { block in
                    BlockView(block: block, chat: chat, l10n: l10n, actions: actions,
                              underCard: { if case .posted(_, let p, _) = block { return p.token.map(cards.contains) ?? false }; return false }())
                }
                if blocks.isEmpty, let text = message.text {
                    AssistantBubble(text: text)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// The composer's switches: which picker is open, the photo picked.
final class ComposerState: ObservableObject {
    @Published var showingPhotos = false
    @Published var showingFiles = false
    @Published var photo: PhotosPickerItem?
}

struct Composer: View {
    @ObservedObject var chat: ChatModel
    let l10n: L10n
    var focused: FocusState<Bool>.Binding
    @StateObject private var ui = ComposerState()
    @StateObject private var recorder = VoiceRecorder()

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let notice = chat.notice {
                Label(l10n.notice(notice), systemImage: "info.circle")
                    .font(.footnote).foregroundStyle(.orange)
                    .accessibilityIdentifier("notice")
            }
            if !chat.attachments.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(chat.attachments) { file in
                            Button { chat.detach(file.id) } label: {
                                HStack(spacing: 6) {
                                    Image(systemName: file.mime.hasPrefix("image/") ? "photo" : "doc")
                                    Text(file.name).lineLimit(1).truncationMode(.middle).frame(maxWidth: 180)
                                    Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                                }
                                .font(.caption)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(Capsule().fill(Theme.card))
                                .overlay(Capsule().stroke(.quaternary))
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("\(l10n[.removeAttachment]) \(file.name)")
                            .accessibilityIdentifier("attachment")
                        }
                    }
                }
            }
            if chat.uploading {
                HStack(spacing: 8) {
                    ProgressView(value: chat.uploadProgress ?? 0).frame(maxWidth: 120)
                    Text("\(l10n[.uploading]) \(Format.percent(chat.uploadProgress ?? 0, l10n: l10n))")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                .accessibilityIdentifier("uploading")
            }
            if recorder.recording {
                Label(l10n[.listening], systemImage: "waveform").font(.footnote).foregroundStyle(.red)
            } else if chat.transcribing {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(l10n[.transcribing]).font(.footnote).foregroundStyle(.secondary)
                }
            }
            HStack(alignment: .bottom, spacing: 10) {
                Menu {
                    Button { ui.showingPhotos = true } label: { Label(l10n[.attachPhoto], systemImage: "photo") }
                    Button { ui.showingFiles = true } label: { Label(l10n[.attachFile], systemImage: "doc") }
                } label: {
                    Image(systemName: "paperclip").font(.system(size: 20)).frame(width: 30, height: 34)
                }
                .menuIndicator(.hidden)
                #if os(macOS)
                .menuStyle(.borderlessButton)
                #endif
                .fixedSize()
                .disabled(chat.uploading)
                .accessibilityLabel(l10n[.attach])
                .accessibilityIdentifier("attach")
                .photosPicker(isPresented: $ui.showingPhotos, selection: $ui.photo, matching: .images)
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
                Image(systemName: recorder.recording ? "mic.fill" : "mic")
                    .font(.system(size: 20))
                    .frame(width: 30, height: 34)
                    .foregroundStyle(recorder.recording ? Color.red : (chat.transcribing ? Color.secondary : Theme.primary))
                    .contentShape(Rectangle())
                    .gesture(DragGesture(minimumDistance: 0)
                        .onChanged { _ in
                            guard !recorder.pressing, !chat.transcribing else { return }
                            chat.say(nil)
                            recorder.press(onDenied: { chat.microphoneOff() })
                        }
                        .onEnded { _ in
                            switch recorder.release() {
                            case .heard(let data): Task { await chat.heard(data) }
                            case .tooShort: chat.tooShort()
                            case .nothing: break
                            }
                        })
                    .accessibilityLabel(l10n[.holdToTalk])
                    .accessibilityIdentifier("mic")
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
        }
        .padding(12)
        .background(.bar)
        .fileImporter(isPresented: $ui.showingFiles, allowedContentTypes: Attachments.pickerTypes) { result in
            guard case .success(let url) = result else { return }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            guard let data = try? Data(contentsOf: url) else { chat.say(.uploadFailed); return }
            let name = url.lastPathComponent
            Task { await chat.attach(data, name: name, mime: Attachments.mime(forFileName: name)) }
        }
        .onChange(of: ui.photo) { _, item in
            guard let item else { return }
            ui.photo = nil
            Task {
                guard let data = try? await item.loadTransferable(type: Data.self) else { chat.say(.uploadFailed); return }
                let type = item.supportedContentTypes.first { $0.conforms(to: .image) } ?? .jpeg
                let mime = type.preferredMIMEType ?? "image/jpeg"
                await chat.attach(data, name: "photo." + (type.preferredFilenameExtension ?? "jpg"), mime: mime)
            }
        }
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

/// The vouchers waiting for a second person: Approve or Reject (or, for one's own, Withdraw).
struct ApprovalsView: View {
    @ObservedObject var chat: ChatModel
    let l10n: L10n
    let onClose: () -> Void
    @StateObject private var prompts = CardPrompts()

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if chat.approvals.isEmpty {
                        if chat.loadingApprovals {
                            ProgressView().frame(maxWidth: .infinity).padding(.vertical, 40)
                        } else {
                            Label(l10n[.noApprovals], systemImage: "checkmark.seal")
                                .foregroundStyle(.secondary).frame(maxWidth: .infinity).padding(.vertical, 40)
                        }
                    }
                    ForEach(chat.approvals) { block in
                        BlockView(block: block, chat: chat, l10n: l10n, actions: .of(chat, prompts: prompts),
                                  underCard: true)
                    }
                    if let error = chat.error {
                        Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red).font(.callout)
                    }
                }
                .padding(16)
            }
            .background(Theme.canvas)
            .navigationTitle(l10n[.approvals])
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button(l10n[.done], action: onClose) }
            }
            .refreshable { await chat.loadApprovals() }
        }
        .environment(\.layoutDirection, l10n.isRTL ? .rightToLeft : .leftToRight)
        .cardPrompts(prompts, chat: chat, l10n: l10n)
        #if os(macOS)
        .frame(minWidth: 560, minHeight: 520)
        #endif
        .task { await chat.loadApprovals() }
    }
}

/// The server refused this version of the app (426): say so, in the user's language, and ask again on demand.
struct UpdateView: View {
    @ObservedObject var app: AppModel

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "arrow.down.app").font(.system(size: 44)).foregroundStyle(Theme.primary)
                .accessibilityHidden(true)
            Text(app.l10n[.updateTitle]).font(.title2.bold())
            Text(app.l10n[.updateRequired]).multilineTextAlignment(.center).foregroundStyle(.secondary)
                .accessibilityIdentifier("update-required")
            Button(app.l10n[.retry]) { Task { await app.checkAgain() } }
                .buttonStyle(.borderedProminent)
                .tint(Theme.primary)
        }
        .frame(maxWidth: 380)
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.canvas)
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
            case .updateRequired:
                UpdateView(app: app)
            }
        }
        .environment(\.layoutDirection, app.l10n.isRTL ? .rightToLeft : .leftToRight)
        .task { if app.phase == .starting { await app.restore() } }
    }
}
