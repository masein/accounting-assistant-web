import Combine
import CryptoKit
import Foundation

/// Something to tell the user under the conversation, in their language (`L10n.notice`) — not the server's words.
public enum ChatNotice: Equatable, Sendable {
    case uploadFailed, fileFailed, voiceFailed, voiceTooShort, microphoneOff
}

/// A file to go with the next message: uploaded already, its id sent with the words.
public struct PendingFile: Identifiable, Equatable, Sendable {
    public let id: String
    public let name: String
    public let mime: String

    public init(id: String, name: String, mime: String) {
        self.id = id
        self.name = name
        self.mime = mime
    }
}

/// The conversations and the one open: sending (with files, the reply streamed), the assistant's answer as blocks,
/// Confirm / Cancel on a card, Approve / Reject on someone else's, the documents the books made.
@MainActor
public final class ChatModel: ObservableObject {
    @Published public private(set) var threads: [ChatThread] = []
    @Published public private(set) var thread: String?            // nil: a new conversation, until the first answer
    @Published public private(set) var messages: [ChatMessage] = []
    @Published public private(set) var sending = false
    @Published public private(set) var loading = false
    @Published public private(set) var working: Set<String> = []   // cards waiting for an answer, documents downloading
    @Published public var draft = ""
    @Published public var error: String?
    /// older messages wait on the server (scroll to the top for them)
    @Published public private(set) var moreBefore = false
    @Published public private(set) var loadingEarlier = false
    /// the step the server says it is on while it answers ("Reading the books…")
    @Published public private(set) var status: String?
    @Published public private(set) var attachments: [PendingFile] = []
    @Published public private(set) var uploading = false
    @Published public private(set) var uploadProgress: Double?
    @Published public private(set) var transcribing = false
    @Published public var notice: ChatNotice?
    /// documents fetched, by their path on the server: the local copy to open or share
    @Published public private(set) var documents: [String: URL] = [:]
    /// the vouchers waiting for a second person (GET /approvals)
    @Published public private(set) var approvals: [Block] = []
    @Published public private(set) var loadingApprovals = false

    /// how many messages a page brings (the Android app's)
    public static let pageSize = 60

    let api: APIClient
    var onSessionEnded: (() -> Void)?
    var onUpdateRequired: (() -> Void)?
    /// the reply streamed with the server's steps (`/chat/stream`); off, the plain `/chat`
    public var streaming = true
    private var lastFailed: (text: String, clientID: String, files: [PendingFile])?
    private var oldestID: String?
    private var turn: UUID?

    public init(api: APIClient) {
        self.api = api
    }

    public func refreshThreads() async {
        do {
            threads = try await api.threads()
        } catch {
            report(error)
        }
    }

    /// A conversation, its latest page with its cards as they stand now.
    public func open(_ id: String?) async {
        guard id != thread || messages.isEmpty else { return }
        thread = id
        messages = []
        error = nil
        moreBefore = false
        oldestID = nil
        guard let id else { return }
        loading = true
        defer { loading = false }
        do {
            let page = try await api.messagesPage(thread: id, limit: Self.pageSize)
            guard thread == id else { return }                    // another one was opened meanwhile
            messages = page.messages
            moreBefore = page.moreBefore
            oldestID = page.messages.first?.id
        } catch {
            report(error)
        }
    }

    /// The page before the oldest message drawn (scrolled to the top).
    public func loadEarlier() async {
        guard let id = thread, let before = oldestID, moreBefore, !loadingEarlier else { return }
        loadingEarlier = true
        defer { loadingEarlier = false }
        do {
            let page = try await api.messagesPage(thread: id, before: before, limit: Self.pageSize)
            guard thread == id else { return }
            let have = Set(messages.map(\.id))
            messages = page.messages.filter { !have.contains($0.id) } + messages
            moreBefore = page.moreBefore
            oldestID = page.messages.first?.id ?? oldestID
        } catch {
            report(error)
        }
    }

    public func newConversation() {
        thread = nil
        messages = []
        draft = ""
        error = nil
        notice = nil
        moreBefore = false
        oldestID = nil
        attachments = []
    }

    public var canSend: Bool {
        !sending && !uploading && (!draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty)
    }

    public func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let files = attachments
        guard !text.isEmpty || !files.isEmpty, !sending, !uploading else { return }
        draft = ""
        attachments = []
        await deliver(text, clientID: UUID().uuidString, files: files)
    }

    /// The last message again, with the same id: the server answers it once even if the first try got through.
    public func retry() async {
        guard let failed = lastFailed, !sending else { return }
        messages.removeAll { $0.clientMessageId == failed.clientID && $0.isUser }
        await deliver(failed.text, clientID: failed.clientID, files: failed.files)
    }

    public var canRetry: Bool { lastFailed != nil && !sending }

    private func deliver(_ text: String, clientID: String, files: [PendingFile]) async {
        error = nil
        notice = nil
        lastFailed = nil
        sending = true
        status = nil
        let turn = UUID()
        self.turn = turn
        defer { sending = false; status = nil; self.turn = nil }
        messages.append(ChatMessage(id: "local-\(clientID)", role: "user", text: text, clientMessageId: clientID,
                                    files: files.isEmpty ? nil : files.map { SentFile(name: $0.name, contentType: $0.mime) }))
        do {
            let reply = try await ask(text, clientID: clientID, files: files.map(\.id), turn: turn)
            let isNew = thread == nil
            thread = reply.threadId
            messages.append(ChatMessage(id: "reply-\(clientID)", role: "assistant", blocks: reply.blocks))
            if isNew { await refreshThreads() }
        } catch {
            lastFailed = (text, clientID, files)
            report(error)
        }
    }

    /// Streamed, with the server's steps shown, when it can be; a stream cut short (or a server without it) goes
    /// once more to `/chat` with the same id, which the server answers once.
    private func ask(_ text: String, clientID: String, files: [String], turn: UUID) async throws -> ChatReply {
        guard streaming else {
            return try await api.send(message: text, thread: thread, clientMessageID: clientID, attachmentIDs: files)
        }
        do {
            return try await api.sendStreaming(message: text, thread: thread, clientMessageID: clientID,
                                               attachmentIDs: files) { [weak self] step in
                Task { @MainActor in
                    guard let self, self.turn == turn, self.sending else { return }
                    self.status = step
                }
            }
        } catch let e as APIError where e.isNetwork || e.status == 404 || e.status == 405 {
            status = nil
            return try await api.send(message: text, thread: thread, clientMessageID: clientID, attachmentIDs: files)
        }
    }

    // MARK: - Cards

    public func confirm(_ token: String) async { await decide(token, .confirm) }
    public func cancel(_ token: String) async { await decide(token, .cancel) }
    /// Someone else's voucher, posted by this person's approval.
    public func approve(_ token: String) async { await decide(token, .approve) }
    /// Turned down, with a reason for the one who asked.
    public func reject(_ token: String, note: String?) async { await decide(token, .reject(note)) }
    /// The one who asked takes the request back.
    public func withdraw(_ token: String) async { await decide(token, .cancel) }

    enum Decision { case confirm, cancel, approve, reject(String?) }

    private func decide(_ token: String, _ decision: Decision) async {
        guard !working.contains(token) else { return }
        working.insert(token)
        defer { working.remove(token) }
        do {
            let outcome: ProposalOutcome
            switch decision {
            case .confirm: outcome = try await api.confirm(proposal: token)
            case .cancel: outcome = try await api.cancel(proposal: token)
            case .approve: outcome = try await api.approve(token)
            case .reject(let note): outcome = try await api.reject(token, note: note)
            }
            setState(of: token, to: outcome.state)
            if let block = outcome.block, case .posted = block { insert(block, after: token) }
        } catch let e as APIError where e.status == 410 {
            setState(of: token, to: "expired")
        } catch {
            report(error)
        }
    }

    /// Takes a confirmed entry back (the "Undo" under it, for its first two minutes).
    public func undo(_ auditLogID: String) async {
        guard !working.contains(auditLogID) else { return }
        working.insert(auditLogID)
        defer { working.remove(auditLogID) }
        do {
            _ = try await api.undo(posting: auditLogID)
            markUndone(auditLogID)
        } catch let e as APIError where e.status == 410 {
            markUndone(auditLogID, closed: true)
            error = e.detail
        } catch {
            report(error)
        }
    }

    /// The vouchers waiting for a second person.
    public func loadApprovals() async {
        loadingApprovals = true
        defer { loadingApprovals = false }
        do {
            approvals = try await api.approvals()
        } catch {
            report(error)
        }
    }

    /// The "recorded" note right under the card it came from.
    func insert(_ block: Block, after token: String) {
        func place(_ blocks: inout [Block]) -> Bool {
            guard let j = blocks.firstIndex(where: { $0.card?.token == token }) else { return false }
            blocks.removeAll { $0.id == block.id }
            blocks.insert(block, at: min(j + 1, blocks.count))
            return true
        }
        var waiting = approvals
        if place(&waiting) { approvals = waiting }
        for i in messages.indices {
            guard var blocks = messages[i].blocks, place(&blocks) else { continue }
            messages[i].blocks = blocks
            return
        }
    }

    func markUndone(_ auditLogID: String, closed: Bool = false) {
        var tokens: [String] = []
        func mark(_ blocks: inout [Block]) -> Bool {
            var changed = false
            for j in blocks.indices {
                if case .posted(let id, var p, let fallback) = blocks[j], p.auditLogId == auditLogID {
                    if closed {
                        p = Posted(token: p.token, auditLogId: p.auditLogId, voucher: p.voucher, date: p.date,
                                   undoSeconds: p.undoSeconds, actions: [], undone: p.undone, file: p.file)
                    } else {
                        p.undone = true
                        if let token = p.token { tokens.append(token) }
                    }
                    blocks[j] = .posted(id: id, p, fallback: fallback)
                    changed = true
                }
            }
            return changed
        }
        var waiting = approvals
        if mark(&waiting) { approvals = waiting }
        for i in messages.indices {
            guard var blocks = messages[i].blocks, mark(&blocks) else { continue }
            messages[i].blocks = blocks
        }
        // the card the entry came from says so too
        for token in Set(tokens) { setState(of: token, to: "undone") }
    }

    /// The card's new state, wherever it is in the conversation (and in the approvals waiting).
    func setState(of token: String, to state: String) {
        func set(_ blocks: inout [Block]) -> Bool {
            var changed = false
            for j in blocks.indices {
                switch blocks[j] {
                case .proposal(let id, var p) where p.token == token:
                    p.state = state
                    blocks[j] = .proposal(id: id, p)
                    changed = true
                case .approval(let id, var p) where p.token == token:
                    p.state = state
                    blocks[j] = .approval(id: id, p)
                    changed = true
                default:
                    break
                }
            }
            return changed
        }
        var waiting = approvals
        if set(&waiting) { approvals = waiting }
        for i in messages.indices {
            guard var blocks = messages[i].blocks, set(&blocks) else { continue }
            messages[i].blocks = blocks
        }
    }

    // MARK: - Files

    /// A photo or a document for the next message: uploaded now (in pieces), its id sent with the words.
    public func attach(_ data: Data, name: String, mime: String) async {
        notice = nil
        error = nil
        let file = Attachments.prepare(data, name: name, mime: mime)
        uploading = true
        uploadProgress = 0
        defer { uploading = false; uploadProgress = nil }
        do {
            let a = try await api.upload(file.data, fileName: file.name, contentType: file.mime) { [weak self] p in
                Task { @MainActor in
                    guard let self, self.uploading else { return }
                    self.uploadProgress = p
                }
            }
            attachments.append(PendingFile(id: a.id, name: a.fileName ?? file.name, mime: a.contentType ?? file.mime))
        } catch let e as APIError where e.endsSession || e.updateRequired {
            report(e)
        } catch let e as APIError where (400..<500).contains(e.status) {
            error = e.detail                                       // a type it doesn't take, too large: its words
        } catch {
            notice = .uploadFailed
        }
    }

    public func detach(_ id: String) {
        attachments.removeAll { $0.id == id }
    }

    /// A document from the books (an invoice's PDF, a payslip), fetched once and kept for this session: the local
    /// copy to open or share. Nil when it couldn't be fetched (the notice says so).
    @discardableResult
    public func document(_ file: FileRef) async -> URL? {
        if let url = documents[file.path], FileManager.default.fileExists(atPath: url.path) { return url }
        guard !working.contains(file.path) else { return nil }
        working.insert(file.path)
        defer { working.remove(file.path) }
        do {
            let data = try await api.download(file)
            // its own folder per document: two payslips may share a name
            let key = SHA256.hash(data: Data(file.path.utf8)).prefix(8).map { String(format: "%02x", $0) }.joined()
            let dir = FileManager.default.temporaryDirectory.appendingPathComponent("documents/\(key)", isDirectory: true)
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let url = dir.appendingPathComponent(file.safeName)
            try data.write(to: url, options: .atomic)
            documents[file.path] = url
            return url
        } catch let e as APIError where e.endsSession || e.updateRequired {
            report(e)
        } catch {
            notice = .fileFailed
        }
        return nil
    }

    // MARK: - Voice

    /// A voice note's words go into the composer, to check before sending.
    public func heard(_ data: Data, name: String = "voice-note.m4a", mime: String = "audio/mp4") async {
        notice = nil
        transcribing = true
        defer { transcribing = false }
        do {
            let text = try await api.transcribe(data, fileName: name, contentType: mime)
            guard !text.isEmpty else { notice = .voiceFailed; return }
            draft = (draft + " " + text).trimmingCharacters(in: .whitespacesAndNewlines)
        } catch let e as APIError where e.endsSession || e.updateRequired {
            report(e)
        } catch {
            notice = .voiceFailed
        }
    }

    /// Let go too soon to hold any words.
    public func tooShort() { notice = .voiceTooShort }

    public func microphoneOff() { notice = .microphoneOff }

    public func say(_ notice: ChatNotice?) { self.notice = notice }

    private func report(_ error: Error) {
        if let e = error as? APIError {
            if e.updateRequired { onUpdateRequired?(); return }
            if e.endsSession { onSessionEnded?(); return }
        }
        self.error = error.localizedDescription
    }
}
