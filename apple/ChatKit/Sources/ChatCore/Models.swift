import Foundation

// The mobile API's answers (app/api/mobile.py, app/api/mobile_chat.py), as the app reads them. Field names follow
// the server's snake_case through the decoder's key strategy. A chat answer is a list of *blocks*: the app draws
// every kind of the contract (docs/contracts/mobile-blocks.schema.json); a kind the server adds later shows its
// `fallback_text`, so it still reads.

public struct User: Codable, Equatable, Sendable {
    public let id: String
    public let username: String
    public let role: String?
    public let preferredLanguage: String?
}

public struct Company: Codable, Equatable, Sendable {
    public let id: String
    public let name: String
    public let baseCurrency: String?
    public let calendar: String?
}

public struct SignIn: Codable, Equatable, Sendable {
    public let accessToken: String
    public let refreshToken: String
    public let expiresIn: Int?
    public let deviceId: String?
    public let user: User
    public let company: Company?
}

public enum SignInResult: Equatable, Sendable {
    case signedIn(SignIn)
    case twoFactor(challenge: String)
}

public struct ChatThread: Codable, Identifiable, Equatable, Hashable, Sendable {
    public let id: String
    public let title: String?
    public let updatedAt: String?
    public let messageCount: Int?

    public init(id: String, title: String?, updatedAt: String? = nil, messageCount: Int? = nil) {
        self.id = id
        self.title = title
        self.updatedAt = updatedAt
        self.messageCount = messageCount
    }
}

/// A file a message carried (its name and type), as the conversation shows it again.
public struct SentFile: Codable, Equatable, Sendable {
    public let name: String?
    public let contentType: String?

    public init(name: String?, contentType: String?) {
        self.name = name
        self.contentType = contentType
    }
}

public struct ChatMessage: Codable, Identifiable, Equatable, Sendable {
    public let id: String
    public let role: String
    public let createdAt: String?
    public var text: String?
    public var blocks: [Block]?
    public let clientMessageId: String?
    public var files: [SentFile]?

    public init(id: String, role: String, text: String? = nil, blocks: [Block]? = nil, createdAt: String? = nil,
                clientMessageId: String? = nil, files: [SentFile]? = nil) {
        self.id = id
        self.role = role
        self.text = text
        self.blocks = blocks
        self.createdAt = createdAt
        self.clientMessageId = clientMessageId
        self.files = files
    }

    public var isUser: Bool { role == "user" }
}

/// A page of a conversation, oldest first; `moreBefore` says whether older messages wait on the server.
public struct MessagesPage: Equatable, Sendable {
    public let messages: [ChatMessage]
    public let moreBefore: Bool

    public init(messages: [ChatMessage], moreBefore: Bool) {
        self.messages = messages
        self.moreBefore = moreBefore
    }
}

public struct ChatReply: Codable, Equatable, Sendable {
    public let threadId: String
    public let blocks: [Block]
    public let stopReason: String?
}

/// What Confirm, Approve, Reject, Cancel and Undo answer: the card's new state ("posted", "waiting_for_approval",
/// "rejected", "cancelled", "undone"), and for a posting the stamped receipt.
public struct ProposalOutcome: Codable, Equatable, Sendable {
    public let state: String
    public let block: Block?
}

public struct Money: Codable, Equatable, Sendable {
    public let value: Decimal
    public let currency: String?

    public init(value: Decimal, currency: String?) {
        self.value = value
        self.currency = currency
    }
}

public struct BlockDate: Codable, Equatable, Sendable {
    public let iso: String?
    public let display: String?

    public init(iso: String?, display: String?) {
        self.iso = iso
        self.display = display
    }
}

public struct EntryLine: Codable, Equatable, Sendable, Identifiable {
    public var id: String { "\(account ?? "")|\(debit)|\(credit)|\(name ?? "")" }
    public let account: String?
    public let name: String?
    public let debit: Decimal
    public let credit: Decimal

    enum CodingKeys: String, CodingKey { case account, name, debit, credit }

    public init(account: String?, name: String?, debit: Decimal, credit: Decimal) {
        self.account = account
        self.name = name
        self.debit = debit
        self.credit = credit
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        account = try c.decodeIfPresent(String.self, forKey: .account)
        name = try c.decodeIfPresent(String.self, forKey: .name)
        debit = (try? c.decodeIfPresent(Decimal.self, forKey: .debit)) ?? 0
        credit = (try? c.decodeIfPresent(Decimal.self, forKey: .credit)) ?? 0
    }
}

/// In a thread's history, a posted card's seal (contract: "stamp"): the entry's reference and date, and whether it was
/// undone since — the card's state stays "executed" after an undo, so this is what says so.
public struct Stamp: Codable, Equatable, Sendable {
    public let voucher: String?
    public let date: BlockDate?
    public var undone: Bool?

    public init(voucher: String? = nil, date: BlockDate? = nil, undone: Bool? = nil) {
        self.voucher = voucher
        self.date = date
        self.undone = undone
    }
}

/// A proposal card: something the assistant would record, waiting for Confirm or Cancel. An *approval* card (contract
/// "approval") is the same voucher drawn for the second person who decides on it: who asked, Approve and Reject — or,
/// for the one who asked, Withdraw.
public struct Proposal: Codable, Equatable, Sendable {
    public let token: String
    public let tool: String?
    public let title: String?
    public let summary: String?
    public let amount: Money?
    public let date: BlockDate?
    public let lines: [EntryLine]?
    public let actions: [String]?
    public let needsApproval: Bool?
    public var state: String?
    /// "requested" while it waits for a second person; "approved", "rejected" or "withdrawn" after
    public var approvalStatus: String?
    /// the seal of a card posted before, when the thread is opened again
    public var posted: Stamp?
    public let replaces: String?
    // an approval card's own
    public let requestedBy: String?
    public let mine: Bool?
    public let requestedAt: BlockDate?
    public let userMessage: String?

    enum CodingKeys: String, CodingKey {
        case token, tool, title, summary, amount, date, lines, actions, needsApproval, state, approvalStatus, posted,
             replaces, requestedBy, mine, requestedAt, userMessage
    }

    public init(token: String, tool: String? = nil, title: String? = nil, summary: String? = nil, amount: Money? = nil,
                date: BlockDate? = nil, lines: [EntryLine]? = nil, actions: [String]? = nil, needsApproval: Bool? = nil,
                state: String? = nil, approvalStatus: String? = nil, posted: Stamp? = nil, replaces: String? = nil,
                requestedBy: String? = nil, mine: Bool? = nil, requestedAt: BlockDate? = nil, userMessage: String? = nil) {
        self.token = token; self.tool = tool; self.title = title; self.summary = summary; self.amount = amount
        self.date = date; self.lines = lines; self.actions = actions; self.needsApproval = needsApproval
        self.state = state; self.approvalStatus = approvalStatus; self.posted = posted; self.replaces = replaces
        self.requestedBy = requestedBy; self.mine = mine; self.requestedAt = requestedAt; self.userMessage = userMessage
    }

    /// Only the token is a must: a field that doesn't read (an amount of null, a seal of another shape) is left out,
    /// never the whole card.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        func opt<T: Decodable>(_ type: T.Type, _ key: CodingKeys) -> T? { (try? c.decodeIfPresent(T.self, forKey: key)) ?? nil }
        token = try c.decode(String.self, forKey: .token)
        tool = opt(String.self, .tool)
        title = opt(String.self, .title)
        summary = opt(String.self, .summary)
        amount = opt(Money.self, .amount)
        date = opt(BlockDate.self, .date)
        lines = opt([EntryLine].self, .lines)
        actions = opt([String].self, .actions)
        needsApproval = opt(Bool.self, .needsApproval)
        state = opt(String.self, .state)
        approvalStatus = opt(String.self, .approvalStatus)
        posted = opt(Stamp.self, .posted)
        replaces = opt(String.self, .replaces)
        requestedBy = opt(String.self, .requestedBy)
        mine = opt(Bool.self, .mine)
        requestedAt = opt(BlockDate.self, .requestedAt)
        userMessage = opt(String.self, .userMessage)
    }

    /// Where the card stands, as it is drawn: every state the server (or this app) gives it has its words (L10n).
    public enum Phase: Equatable, Sendable, CaseIterable {
        case draft          // Confirm / Cancel; on an approval card, Approve / Reject (or Withdraw)
        case waiting        // sent to a second person for approval
        case posted
        case undone         // posted, then taken back
        case cancelled
        case rejected       // turned down by the second person
        case expired
        case replaced       // changed on the card: the new card follows
        case closed         // a state this version doesn't know: no buttons, and a plain word for it
    }

    /// An approval card (contract "approval"): someone's voucher for a second person to decide on.
    public var isApproval: Bool { requestedBy != nil || mine != nil }

    public var phase: Phase {
        switch state ?? "pending" {
        case "pending":
            // the asker's own card, sent for approval: it waits (the approval card itself offers the decision)
            return approvalStatus == "requested" && !isApproval ? .waiting : .draft
        case "waiting_for_approval": return .waiting
        case "posted", "executed": return posted?.undone == true ? .undone : .posted
        case "undone": return .undone
        case "cancelled": return approvalStatus == "rejected" ? .rejected : .cancelled
        case "rejected": return .rejected
        case "expired": return .expired
        case "replaced": return .replaced
        default: return .closed
        }
    }

    public var isPending: Bool { phase == .draft }
    public var canConfirm: Bool { isPending && !isApproval && (actions ?? ["confirm"]).contains("confirm") }
    public var canCancel: Bool { isPending && !isApproval && (actions ?? ["cancel"]).contains("cancel") }
    public var canApprove: Bool { isPending && isApproval && (actions ?? []).contains("approve") }
    public var canReject: Bool { isPending && isApproval && (actions ?? []).contains("reject") }
    /// the one who asked takes the request back
    public var canWithdraw: Bool { isPending && isApproval && (actions ?? []).contains("cancel") }
}

/// A number with its label: cash at hand, what's owed (contract: "figure").
public struct Figure: Codable, Equatable, Sendable {
    public struct Part: Codable, Equatable, Sendable {
        /// an account with no name comes with none (the contract allows null)
        public let label: String?
        public let value: Decimal
    }
    public let label: String
    public let value: Decimal
    public let currency: String?
    public let asOf: String?
    public let breakdown: [Part]?
}

/// A list from the books (contract: "table"): spending by category, invoices, budgets, pay runs, an employee's pay,
/// holdings. Each row is a label, a note under it, and an amount.
public struct TableData: Codable, Equatable, Sendable {
    public struct Row: Codable, Equatable, Sendable {
        public let label: String?
        public let sub: String?
        public let value: Decimal
        public let currency: String?
        /// holdings: what today's rate adds (or takes)
        public let gain: Decimal?
        /// budgets: the budget, and how much of it is used
        public let limit: Decimal?
        public let usedPct: Double?
        /// budgets ("ok", "over"…), invoices ("overdue", "sent"…): drawn as a colour, never shown as a word
        public let state: String?
        public let dueDate: String?

        enum CodingKeys: String, CodingKey { case label, sub, value, currency, gain, limit, usedPct, state, dueDate }

        public init(label: String?, sub: String? = nil, value: Decimal, currency: String? = nil, gain: Decimal? = nil,
                    limit: Decimal? = nil, usedPct: Double? = nil, state: String? = nil, dueDate: String? = nil) {
            self.label = label; self.sub = sub; self.value = value; self.currency = currency; self.gain = gain
            self.limit = limit; self.usedPct = usedPct; self.state = state; self.dueDate = dueDate
        }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            func opt<T: Decodable>(_ type: T.Type, _ key: CodingKeys) -> T? { (try? c.decodeIfPresent(T.self, forKey: key)) ?? nil }
            label = opt(String.self, .label)
            sub = opt(String.self, .sub)
            value = opt(Decimal.self, .value) ?? 0
            currency = opt(String.self, .currency)
            gain = opt(Decimal.self, .gain)
            limit = opt(Decimal.self, .limit)
            usedPct = opt(Double.self, .usedPct)
            state = opt(String.self, .state)
            dueDate = opt(String.self, .dueDate)
        }

        /// A budget spent past its limit.
        public var isOver: Bool { state == "over" || (usedPct ?? 0) > 100 }
    }
    public let kind: String?
    public let title: String?
    public let rows: [Row]
    public let count: Int?

    public init(kind: String?, title: String? = nil, rows: [Row], count: Int? = nil) {
        self.kind = kind; self.title = title; self.rows = rows; self.count = count
    }

    /// The rows' currencies, once each: a table in two currencies names each row's.
    public var currencies: [String] {
        var seen: [String] = []
        for c in rows.compactMap({ $0.currency?.uppercased() }) where !seen.contains(c) { seen.append(c) }
        return seen
    }

    /// The table as lines of text (to copy, or for a screen reader).
    public var plainText: String {
        ([title].compactMap { $0 } + rows.map { r in
            [r.label, r.sub.map { "(\($0))" }, "\(r.value)", r.currency].compactMap { $0 }.joined(separator: " ")
        }).joined(separator: "\n")
    }
}

/// A document from the books (contract: "file"): an invoice's PDF, a payslip — fetched with the session's token.
public struct FileRef: Codable, Equatable, Sendable {
    public let name: String
    public let mime: String?
    public let path: String

    public init(name: String, mime: String?, path: String) {
        self.name = name
        self.mime = mime
        self.path = path
    }

    /// Only the server's own documents (/api/mobile/v1/…) are fetched, and only with this session's token.
    public var isPhoneDocument: Bool {
        path.hasPrefix("/" + APIClient.apiPath + "/") && !path.contains("..") && !path.contains("?") && !path.contains("#")
    }

    /// The name it is saved under: letters, digits, dot, dash and underscore only.
    public var safeName: String {
        let kept = name.unicodeScalars.map { CharacterSet.alphanumerics.contains($0) || "._-".unicodeScalars.contains($0) ? Character($0) : "_" }
        let s = String(kept).trimmingCharacters(in: CharacterSet(charactersIn: "."))
        return s.isEmpty ? "document" : String(s.prefix(120))
    }
}

/// A forecast or a history over time (contract: "chart").
public struct ChartData: Codable, Equatable, Sendable {
    public struct Point: Codable, Equatable, Sendable, Identifiable {
        public var id: String { (x ?? "") + "\(value)" }
        public let x: String?
        public let value: Double
    }
    public let kind: String?
    public let style: String?
    public let title: String
    public let currency: String?
    public let points: [Point]
}

/// The week's or the month's review (contract: "review"): a mood, a title, and what stood out.
public struct Review: Codable, Equatable, Sendable {
    public let kind: String?
    public let mood: String?
    public let emoji: String?
    public let title: String
    public let facts: [String]?
    public let note: String?
}

/// A file read for the books (contract: "intake"): a bank statement, or a batch of transactions. The server sends
/// it without a sentence, so its words are made here.
public struct Intake: Codable, Equatable, Sendable {
    public let kind: String
    public let status: String?
    public let bankName: String?
    public let bankLabel: String?
    public let fileName: String?
    public let totalRows: Int?
    public let rows: Int?
    public let matched: Int?
    public let fromDate: String?
    public let toDate: String?
    public let counts: [String: Int]?

    public var rowCount: Int? { totalRows ?? rows }
    public var matchedCount: Int? { matched ?? counts?["matched"] }

    /// One plain line: what was read, how many rows, the period, how many matched.
    public var summary: String {
        var parts: [String] = [kind == "bank_statement" ? "Bank statement" : "Transactions"]
        if let bank = bankLabel ?? bankName { parts.append(bank) }
        if let n = rowCount { parts.append("\(n) rows") }
        if let from = fromDate, let to = toDate { parts.append("\(from) – \(to)") }
        if let m = matchedCount { parts.append("\(m) matched") }
        return parts.joined(separator: " · ")
    }
}

/// Recorded (contract: "posted"): undo is open for `undoSeconds` after it; an invoice's posting brings its PDF.
public struct Posted: Codable, Equatable, Sendable {
    public let token: String?
    public let auditLogId: String?
    public let voucher: String?
    public let date: BlockDate?
    public let undoSeconds: Int?
    public let actions: [String]?
    public var undone: Bool?
    public let file: FileRef?

    enum CodingKeys: String, CodingKey { case token, auditLogId, voucher, date, undoSeconds, actions, undone, file }

    public init(token: String?, auditLogId: String?, voucher: String?, date: BlockDate?, undoSeconds: Int?,
                actions: [String]?, undone: Bool?, file: FileRef? = nil) {
        self.token = token; self.auditLogId = auditLogId; self.voucher = voucher; self.date = date
        self.undoSeconds = undoSeconds; self.actions = actions; self.undone = undone; self.file = file
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        func opt<T: Decodable>(_ type: T.Type, _ key: CodingKeys) -> T? { (try? c.decodeIfPresent(T.self, forKey: key)) ?? nil }
        token = opt(String.self, .token)
        auditLogId = opt(String.self, .auditLogId)
        voucher = opt(String.self, .voucher)
        date = opt(BlockDate.self, .date)
        undoSeconds = opt(Int.self, .undoSeconds)
        actions = opt([String].self, .actions)
        undone = opt(Bool.self, .undone)
        file = opt(FileRef.self, .file)
    }

    public var canUndo: Bool { (actions ?? []).contains("undo") && auditLogId != nil && undone != true }
}

public enum Block: Codable, Equatable, Sendable, Identifiable {
    case text(id: String, text: String)
    case proposal(id: String, Proposal)
    /// someone's voucher for a second person: Approve / Reject (or, for the one who asked, Withdraw)
    case approval(id: String, Proposal)
    case posted(id: String, Posted, fallback: String)
    case figure(id: String, Figure, fallback: String)
    case table(id: String, TableData, fallback: String)
    case chart(id: String, ChartData, fallback: String)
    case review(id: String, Review, fallback: String)
    case intake(id: String, Intake, fallback: String)
    case file(id: String, FileRef, fallback: String)
    /// kinds added later: their own sentence
    case other(id: String, type: String, fallback: String)

    public var id: String {
        switch self {
        case .text(let id, _), .proposal(let id, _), .approval(let id, _), .posted(let id, _, _), .figure(let id, _, _),
             .table(let id, _, _), .chart(let id, _, _), .review(let id, _, _), .intake(let id, _, _),
             .file(let id, _, _), .other(let id, _, _): return id
        }
    }

    /// What the block says as plain text (for copying, and for kinds the app doesn't draw).
    public var plainText: String {
        switch self {
        case .text(_, let text): return text
        case .proposal(_, let p), .approval(_, let p): return p.summary ?? p.title ?? ""
        case .intake(_, let intake, let fallback): return fallback.isEmpty ? intake.summary : fallback
        case .table(_, let table, let fallback): return fallback.isEmpty ? table.plainText : fallback
        case .file(_, let file, let fallback): return fallback.isEmpty ? file.name : fallback
        case .posted(_, _, let fallback), .figure(_, _, let fallback), .chart(_, _, let fallback),
             .review(_, _, let fallback), .other(_, _, let fallback): return fallback
        }
    }

    public var type: String {
        switch self {
        case .text: return "text"
        case .proposal: return "proposal"
        case .approval: return "approval"
        case .posted: return "posted"
        case .figure: return "figure"
        case .table: return "table"
        case .chart: return "chart"
        case .review: return "review"
        case .intake: return "intake"
        case .file: return "file"
        case .other(_, let type, _): return type
        }
    }

    /// The card's voucher, for a proposal or an approval.
    public var card: Proposal? {
        switch self {
        case .proposal(_, let p), .approval(_, let p): return p
        default: return nil
        }
    }

    enum Keys: String, CodingKey { case type, id, text, fallbackText }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        let type = (try? c.decode(String.self, forKey: .type)) ?? "text"
        let id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
        let fallback = (try? c.decodeIfPresent(String.self, forKey: .fallbackText)) ?? nil
        let said = fallback ?? ""
        // a kind the app draws, but whose fields don't read: its sentence, never a failed answer
        switch type {
        case "text":
            let text = (try? c.decodeIfPresent(String.self, forKey: .text)) ?? nil
            self = .text(id: id, text: text ?? said)
        case "proposal":
            self = (try? Proposal(from: decoder)).map { .proposal(id: id, $0) } ?? .other(id: id, type: type, fallback: said)
        case "approval":
            self = (try? Proposal(from: decoder)).map { .approval(id: id, $0) } ?? .other(id: id, type: type, fallback: said)
        case "posted":
            self = (try? Posted(from: decoder)).map { .posted(id: id, $0, fallback: said) } ?? .other(id: id, type: type, fallback: said)
        case "figure":
            self = (try? Figure(from: decoder)).map { .figure(id: id, $0, fallback: said) } ?? .other(id: id, type: type, fallback: said)
        case "table":
            self = (try? TableData(from: decoder)).map { .table(id: id, $0, fallback: said) } ?? .other(id: id, type: type, fallback: said)
        case "chart":
            self = (try? ChartData(from: decoder)).map { .chart(id: id, $0, fallback: said) } ?? .other(id: id, type: type, fallback: said)
        case "review":
            self = (try? Review(from: decoder)).map { .review(id: id, $0, fallback: said) } ?? .other(id: id, type: type, fallback: said)
        case "intake":
            self = (try? Intake(from: decoder)).map { .intake(id: id, $0, fallback: said) } ?? .other(id: id, type: type, fallback: said)
        case "file":
            // only the server's own documents are fetched with the session's token; any other path is its sentence
            self = (try? FileRef(from: decoder)).flatMap { $0.isPhoneDocument ? .file(id: id, $0, fallback: said) : nil }
                ?? .other(id: id, type: type, fallback: said)
        default:
            self = .other(id: id, type: type, fallback: said)
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: Keys.self)
        func with(_ type: String, _ id: String, _ fallback: String?) throws {
            try c.encode(type, forKey: .type); try c.encode(id, forKey: .id)
            if let fallback { try c.encode(fallback, forKey: .fallbackText) }
        }
        switch self {
        case .text(let id, let text):
            try with("text", id, nil); try c.encode(text, forKey: .text)
        case .proposal(let id, let p):
            try p.encode(to: encoder); try with("proposal", id, nil)
        case .approval(let id, let p):
            try p.encode(to: encoder); try with("approval", id, nil)
        case .posted(let id, let value, let fallback):
            try value.encode(to: encoder); try with("posted", id, fallback)
        case .figure(let id, let value, let fallback):
            try value.encode(to: encoder); try with("figure", id, fallback)
        case .table(let id, let value, let fallback):
            try value.encode(to: encoder); try with("table", id, fallback)
        case .chart(let id, let value, let fallback):
            try value.encode(to: encoder); try with("chart", id, fallback)
        case .review(let id, let value, let fallback):
            try value.encode(to: encoder); try with("review", id, fallback)
        case .intake(let id, let value, let fallback):
            try value.encode(to: encoder); try with("intake", id, fallback)
        case .file(let id, let value, let fallback):
            try value.encode(to: encoder); try with("file", id, fallback)
        case .other(let id, let type, let fallback):
            try with(type, id, fallback)
        }
    }
}

/// A refusal's body: its words (a 422's list of field errors isn't words, and is left out) and, on a 426, its code.
public struct APIErrorBody: Decodable, Sendable {
    public let detail: String?
    public let code: String?

    enum CodingKeys: String, CodingKey { case detail, code }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        detail = (try? c.decodeIfPresent(String.self, forKey: .detail)) ?? nil
        code = (try? c.decodeIfPresent(String.self, forKey: .code)) ?? nil
    }
}
