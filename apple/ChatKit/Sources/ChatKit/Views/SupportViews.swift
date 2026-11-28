import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

/// The toolbar's headset: opens Support, with the desk's unread replies counted on it.
struct SupportButton: View {
    @ObservedObject var support: SupportModel
    let l10n: L10n
    let onOpen: () -> Void

    var body: some View {
        Button(action: onOpen) {
            Label {
                Text(l10n[.support])
            } icon: {
                Image(systemName: "headphones")
                    .overlay(alignment: .topTrailing) {
                        if support.unread > 0 {
                            Text(l10n.count(support.unread, cap: 9))
                                .font(.system(size: 9, weight: .bold))
                                .foregroundStyle(.white)
                                .padding(.horizontal, 4)
                                .frame(minWidth: 15, minHeight: 15)
                                .background(Capsule().fill(Color.red))
                                .offset(x: 9, y: -7)
                                .accessibilityIdentifier("support-badge")
                        }
                    }
            }
        }
        .help(l10n[.support])
        .accessibilityLabel(support.unread > 0
                            ? "\(l10n[.support]), \(l10n(.supportUnread, l10n.count(support.unread)))"
                            : l10n[.support])
        .accessibilityIdentifier("support")
    }
}

/// The conversation with the support desk: the person's messages on the trailing side, the desk's on the leading
/// side, each with its time; whether the desk is online; a few words on what helps while it's empty; the composer
/// with a screenshot or a PDF. Loaded and marked seen while it shows, and what's new every five seconds.
public struct SupportView: View {
    @ObservedObject var support: SupportModel
    let l10n: L10n
    let onClose: () -> Void

    public init(support: SupportModel, l10n: L10n, onClose: @escaping () -> Void) {
        self.support = support
        self.l10n = l10n
        self.onClose = onClose
    }

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                SupportStatus(online: support.online, l10n: l10n)
                Divider()
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 14) {
                            if support.messages.isEmpty {
                                SupportIntro(l10n: l10n, loading: !support.loaded)
                            }
                            ForEach(support.messages) { message in
                                SupportRow(message: message, l10n: l10n).id(message.id)
                            }
                        }
                        .padding(16)
                        .frame(maxWidth: 720)
                        .frame(maxWidth: .infinity)
                    }
                    .defaultScrollAnchor(.bottom)
                    .onChange(of: support.messages.last?.id) { _, last in
                        guard let last else { return }
                        withAnimation { proxy.scrollTo(last, anchor: .bottom) }
                    }
                }
                Divider()
                SupportComposer(support: support, l10n: l10n)
            }
            .background(Theme.canvas)
            .navigationTitle(l10n[.support])
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button(l10n[.done], action: onClose).accessibilityIdentifier("support-close")
                }
            }
        }
        .environment(\.layoutDirection, l10n.isRTL ? .rightToLeft : .leftToRight)
        #if os(macOS)
        .frame(minWidth: 540, idealWidth: 600, minHeight: 600, idealHeight: 680)
        #endif
        .task { await support.watchOpen() }
    }
}

/// "● Online — usually answers within a few minutes", or "Away right now — your message is kept…".
struct SupportStatus: View {
    let online: Bool
    let l10n: L10n

    var body: some View {
        Text(online ? l10n[.supportOnline] : l10n[.supportAway])
            .font(.footnote)
            .foregroundStyle(online ? Color(red: 0.13, green: 0.55, blue: 0.29) : Color.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .accessibilityIdentifier("support-status")
    }
}

struct SupportIntro: View {
    let l10n: L10n
    let loading: Bool

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "headphones").font(.system(size: 34)).foregroundStyle(Theme.primary)
                .accessibilityHidden(true)
            Text(l10n[.supportIntro])
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 440)
                .accessibilityIdentifier("support-intro")
            if loading { ProgressView().controlSize(.small) }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 48)
    }
}

/// One message: the person's on the trailing side in the app's colour, the desk's on the leading side; each text
/// reads in its own direction (a Persian message right to left in an English app too).
struct SupportRow: View {
    let message: SupportMessage
    let l10n: L10n

    private var mine: Bool { !message.fromDesk }

    var body: some View {
        HStack(spacing: 0) {
            if mine { Spacer(minLength: 48) }
            VStack(alignment: mine ? .trailing : .leading, spacing: 4) {
                bubble
                Text(caption)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("support-when")
            }
            if !mine { Spacer(minLength: 48) }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(mine ? "support-mine" : "support-desk")
    }

    private var bubble: some View {
        let text = message.text ?? ""
        let files = message.attachments?.count ?? 0
        return VStack(alignment: .leading, spacing: 6) {
            if !text.isEmpty {
                Text(text)
                    .textSelection(.enabled)
                    .multilineTextAlignment(.leading)
                    .environment(\.layoutDirection, text.isRTLScript ? .rightToLeft : .leftToRight)
            }
            if files > 0 {
                Label(l10n(.supportAttached, l10n.count(files)), systemImage: "paperclip")
                    .font(.caption)
                    .opacity(0.85)
            }
        }
        .foregroundStyle(mine ? Color.white : Color.primary)
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(RoundedRectangle(cornerRadius: 16).fill(mine ? Theme.userBubble : Theme.assistantBubble))
        .frame(maxWidth: 480, alignment: mine ? .trailing : .leading)
    }

    /// "Support · 14:05", "You · 3 Dec 2026 at 09:12" (Persian: the Solar Hijri calendar and its digits).
    private var caption: String {
        let who = mine ? l10n[.supportYou] : l10n[.support]
        guard let when = Self.when(message.date, l10n) else { return who }
        return "\(who) · \(when)"
    }

    static func when(_ date: Date?, _ l10n: L10n) -> String? {
        guard let date else { return nil }
        let f = DateFormatter()
        f.locale = l10n.locale
        f.doesRelativeDateFormatting = true
        f.dateStyle = Calendar.current.isDateInToday(date) ? .none : .medium
        f.timeStyle = .short
        return f.string(from: date)
    }
}

/// The words, a screenshot (a photo from the library) or a PDF, and Send.
struct SupportComposer: View {
    @ObservedObject var support: SupportModel
    let l10n: L10n
    @StateObject private var ui = ComposerState()

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let notice = support.notice {
                Label(l10n.notice(notice), systemImage: "info.circle")
                    .font(.footnote).foregroundStyle(.orange)
                    .accessibilityIdentifier("support-notice")
            }
            if let error = support.error {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.footnote).foregroundStyle(.red)
                    .environment(\.layoutDirection, error.isRTLScript ? .rightToLeft : .leftToRight)
                    .accessibilityIdentifier("support-error")
            }
            if !support.attachments.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(support.attachments) { file in
                            Button { support.detach(file.id) } label: {
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
                            .accessibilityIdentifier("support-attachment")
                        }
                    }
                }
            }
            if support.uploading {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(l10n[.uploading]).font(.footnote).foregroundStyle(.secondary)
                }
            }
            HStack(alignment: .bottom, spacing: 10) {
                Menu {
                    Button { ui.showingPhotos = true } label: { Label(l10n[.attachPhoto], systemImage: "photo") }
                    Button { ui.showingFiles = true } label: { Label(l10n[.supportAttachFile], systemImage: "doc") }
                } label: {
                    Image(systemName: "paperclip").font(.system(size: 20)).frame(width: 30, height: 34)
                }
                .menuIndicator(.hidden)
                #if os(macOS)
                .menuStyle(.borderlessButton)
                #endif
                .fixedSize()
                .disabled(!support.canAttach)
                .accessibilityLabel(l10n[.attach])
                .accessibilityIdentifier("support-attach")
                .photosPicker(isPresented: $ui.showingPhotos, selection: $ui.photo, matching: .images)
                TextField(l10n[.supportPlaceholder], text: $support.draft, axis: .vertical)
                    .lineLimit(1...6)
                    .textFieldStyle(.plain)
                    .environment(\.layoutDirection, support.draft.isEmpty
                                 ? (l10n.isRTL ? .rightToLeft : .leftToRight)
                                 : (support.draft.isRTLScript ? .rightToLeft : .leftToRight))
                    .padding(.horizontal, 12)
                    .padding(.vertical, 9)
                    .background(RoundedRectangle(cornerRadius: 18).fill(Theme.card))
                    .overlay(RoundedRectangle(cornerRadius: 18).stroke(.quaternary))
                    .onSubmit { Task { await support.send() } }
                    .accessibilityIdentifier("support-composer")
                Button {
                    Task { await support.send() }
                } label: {
                    if support.sending {
                        ProgressView().controlSize(.small).frame(width: 30, height: 30)
                    } else {
                        Image(systemName: "arrow.up.circle.fill").font(.system(size: 30))
                    }
                }
                .buttonStyle(.plain)
                .foregroundStyle(support.canSend ? Theme.primary : Color.secondary)
                .disabled(!support.canSend)
                .keyboardShortcut(.return, modifiers: .command)
                .accessibilityLabel(l10n[.send])
                .accessibilityIdentifier("support-send")
            }
        }
        .padding(12)
        .background(.bar)
        .fileImporter(isPresented: $ui.showingFiles, allowedContentTypes: [.pdf, .image]) { result in
            guard case .success(let url) = result else { return }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            guard let data = try? Data(contentsOf: url) else { support.say(.uploadFailed); return }
            let name = url.lastPathComponent
            Task { await support.attach(data, name: name, mime: Attachments.mime(forFileName: name)) }
        }
        .onChange(of: ui.photo) { _, item in
            guard let item else { return }
            ui.photo = nil
            Task {
                guard let data = try? await item.loadTransferable(type: Data.self) else { support.say(.uploadFailed); return }
                let type = item.supportedContentTypes.first { $0.conforms(to: .image) } ?? .jpeg
                let mime = type.preferredMIMEType ?? "image/jpeg"
                await support.attach(data, name: "screenshot." + (type.preferredFilenameExtension ?? "jpg"), mime: mime)
            }
        }
    }
}
