import Foundation
import Combine

/// The conversations and the one open: sending, the assistant's answer as blocks, Confirm / Cancel on a card.
@MainActor
public final class ChatModel: ObservableObject {
    @Published public private(set) var threads: [ChatThread] = []
    @Published public private(set) var thread: String?            // nil: a new conversation, until the first answer
    @Published public private(set) var messages: [ChatMessage] = []
    @Published public private(set) var sending = false
    @Published public private(set) var loading = false
    @Published public private(set) var working: Set<String> = []   // cards waiting for Confirm / Cancel
    @Published public var draft = ""
    @Published public var error: String?

    let api: APIClient
    var onSessionEnded: (() -> Void)?
    private var lastFailed: (text: String, clientID: String)?

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

    public func open(_ id: String?) async {
        guard id != thread || messages.isEmpty else { return }
        thread = id
        messages = []
        error = nil
        guard let id else { return }
        loading = true
        defer { loading = false }
        do {
            messages = try await api.messages(thread: id)
        } catch {
            report(error)
        }
    }

    public func newConversation() {
        thread = nil
        messages = []
        draft = ""
        error = nil
    }

    public var canSend: Bool { !sending && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    public func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        draft = ""
        await deliver(text, clientID: UUID().uuidString)
    }

    /// The last message again, with the same id: the server answers it once even if the first try got through.
    public func retry() async {
        guard let failed = lastFailed, !sending else { return }
        messages.removeAll { $0.clientMessageId == failed.clientID && $0.isUser }
        await deliver(failed.text, clientID: failed.clientID)
    }

    public var canRetry: Bool { lastFailed != nil && !sending }

    private func deliver(_ text: String, clientID: String) async {
        error = nil
        lastFailed = nil
        sending = true
        defer { sending = false }
        messages.append(ChatMessage(id: "local-\(clientID)", role: "user", text: text, clientMessageId: clientID))
        do {
            let reply = try await api.send(message: text, thread: thread, clientMessageID: clientID)
            let isNew = thread == nil
            thread = reply.threadId
            messages.append(ChatMessage(id: "reply-\(clientID)", role: "assistant", blocks: reply.blocks))
            if isNew { await refreshThreads() }
        } catch {
            lastFailed = (text, clientID)
            report(error)
        }
    }

    public func confirm(_ token: String) async { await decide(token, confirm: true) }
    public func cancel(_ token: String) async { await decide(token, confirm: false) }

    private func decide(_ token: String, confirm: Bool) async {
        guard !working.contains(token) else { return }
        working.insert(token)
        defer { working.remove(token) }
        do {
            let outcome = confirm ? try await api.confirm(proposal: token) : try await api.cancel(proposal: token)
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

    /// The "recorded" note right under the card it came from.
    func insert(_ block: Block, after token: String) {
        for i in messages.indices {
            guard var blocks = messages[i].blocks,
                  let j = blocks.firstIndex(where: { if case .proposal(_, let p) = $0 { return p.token == token }; return false })
            else { continue }
            blocks.removeAll { $0.id == block.id }
            blocks.insert(block, at: j + 1)
            messages[i].blocks = blocks
            return
        }
    }

    func markUndone(_ auditLogID: String, closed: Bool = false) {
        for i in messages.indices {
            guard var blocks = messages[i].blocks else { continue }
            var changed = false
            for j in blocks.indices {
                if case .posted(let id, var p, let fallback) = blocks[j], p.auditLogId == auditLogID {
                    if closed { p = Posted(token: p.token, auditLogId: p.auditLogId, voucher: p.voucher, date: p.date,
                                           undoSeconds: p.undoSeconds, actions: [], undone: p.undone) }
                    else { p.undone = true }
                    blocks[j] = .posted(id: id, p, fallback: fallback)
                    changed = true
                }
            }
            if changed { messages[i].blocks = blocks }
        }
    }

    /// The card's new state, wherever it is in the conversation.
    func setState(of token: String, to state: String) {
        for i in messages.indices {
            guard var blocks = messages[i].blocks else { continue }
            var changed = false
            for j in blocks.indices {
                if case .proposal(let id, var p) = blocks[j], p.token == token {
                    p.state = state
                    blocks[j] = .proposal(id: id, p)
                    changed = true
                }
            }
            if changed { messages[i].blocks = blocks }
        }
    }

    private func report(_ error: Error) {
        if let e = error as? APIError, e.endsSession {
            onSessionEnded?()
            return
        }
        self.error = error.localizedDescription
    }
}
