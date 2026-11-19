import Foundation

// The mobile API's answers (app/api/mobile.py, app/api/mobile_chat.py), as the app reads them. Field names follow
// the server's snake_case through the decoder's key strategy. A chat answer is a list of *blocks*: the app draws
// text and proposal cards (and a posted card after Confirm); any other kind shows its `fallback_text`, so a block
// type the server adds later still reads.

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

public struct ChatMessage: Codable, Identifiable, Equatable, Sendable {
    public let id: String
    public let role: String
    public let createdAt: String?
    public var text: String?
    public var blocks: [Block]?
    public let clientMessageId: String?

    public init(id: String, role: String, text: String? = nil, blocks: [Block]? = nil, createdAt: String? = nil,
                clientMessageId: String? = nil) {
        self.id = id
        self.role = role
        self.text = text
        self.blocks = blocks
        self.createdAt = createdAt
        self.clientMessageId = clientMessageId
    }

    public var isUser: Bool { role == "user" }
}

public struct ChatReply: Codable, Equatable, Sendable {
    public let threadId: String
    public let blocks: [Block]
    public let stopReason: String?
}

public struct ProposalOutcome: Codable, Equatable, Sendable {
    public let state: String
    public let block: Block?
}

public struct Money: Codable, Equatable, Sendable {
    public let value: Decimal
    public let currency: String?
}

public struct BlockDate: Codable, Equatable, Sendable {
    public let iso: String?
    public let display: String?
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

/// A proposal card: something the assistant would record, waiting for Confirm or Cancel.
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

    public var isPending: Bool { (state ?? "pending") == "pending" }
    public var canConfirm: Bool { isPending && (actions ?? ["confirm"]).contains("confirm") }
    public var canCancel: Bool { isPending && (actions ?? ["cancel"]).contains("cancel") }
}

/// A number with its label: cash at hand, what's owed (contract: "figure").
public struct Figure: Codable, Equatable, Sendable {
    public struct Part: Codable, Equatable, Sendable {
        public let label: String
        public let value: Decimal
    }
    public let label: String
    public let value: Decimal
    public let currency: String?
    public let asOf: String?
    public let breakdown: [Part]?
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

/// Recorded (contract: "posted"): undo is open for `undoSeconds` after it.
public struct Posted: Codable, Equatable, Sendable {
    public let token: String?
    public let auditLogId: String?
    public let voucher: String?
    public let date: BlockDate?
    public let undoSeconds: Int?
    public let actions: [String]?
    public var undone: Bool?

    public var canUndo: Bool { (actions ?? []).contains("undo") && auditLogId != nil && undone != true }
}

public enum Block: Codable, Equatable, Sendable, Identifiable {
    case text(id: String, text: String)
    case proposal(id: String, Proposal)
    case posted(id: String, Posted, fallback: String)
    case figure(id: String, Figure, fallback: String)
    case chart(id: String, ChartData, fallback: String)
    case review(id: String, Review, fallback: String)
    /// tables, files, approvals, statement intake, and kinds added later: their own sentence
    case other(id: String, type: String, fallback: String)

    public var id: String {
        switch self {
        case .text(let id, _), .proposal(let id, _), .posted(let id, _, _), .figure(let id, _, _), .chart(let id, _, _),
             .review(let id, _, _), .other(let id, _, _): return id
        }
    }

    /// What the block says as plain text (for copying, and for kinds the app doesn't draw).
    public var plainText: String {
        switch self {
        case .text(_, let text): return text
        case .proposal(_, let p): return p.summary ?? p.title ?? ""
        case .posted(_, _, let fallback), .figure(_, _, let fallback), .chart(_, _, let fallback),
             .review(_, _, let fallback), .other(_, _, let fallback): return fallback
        }
    }

    public var type: String {
        switch self {
        case .text: return "text"
        case .proposal: return "proposal"
        case .posted: return "posted"
        case .figure: return "figure"
        case .chart: return "chart"
        case .review: return "review"
        case .other(_, let type, _): return type
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
        case "posted":
            self = (try? Posted(from: decoder)).map { .posted(id: id, $0, fallback: said) } ?? .other(id: id, type: type, fallback: said)
        case "figure":
            self = (try? Figure(from: decoder)).map { .figure(id: id, $0, fallback: said) } ?? .other(id: id, type: type, fallback: said)
        case "chart":
            self = (try? ChartData(from: decoder)).map { .chart(id: id, $0, fallback: said) } ?? .other(id: id, type: type, fallback: said)
        case "review":
            self = (try? Review(from: decoder)).map { .review(id: id, $0, fallback: said) } ?? .other(id: id, type: type, fallback: said)
        default:
            self = .other(id: id, type: type, fallback: said)
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: Keys.self)
        switch self {
        case .text(let id, let text):
            try c.encode("text", forKey: .type); try c.encode(id, forKey: .id); try c.encode(text, forKey: .text)
        case .proposal(let id, let p):
            try p.encode(to: encoder)
            try c.encode("proposal", forKey: .type); try c.encode(id, forKey: .id)
        case .posted(let id, let value, let fallback):
            try value.encode(to: encoder)
            try c.encode("posted", forKey: .type); try c.encode(id, forKey: .id); try c.encode(fallback, forKey: .fallbackText)
        case .figure(let id, let value, let fallback):
            try value.encode(to: encoder)
            try c.encode("figure", forKey: .type); try c.encode(id, forKey: .id); try c.encode(fallback, forKey: .fallbackText)
        case .chart(let id, let value, let fallback):
            try value.encode(to: encoder)
            try c.encode("chart", forKey: .type); try c.encode(id, forKey: .id); try c.encode(fallback, forKey: .fallbackText)
        case .review(let id, let value, let fallback):
            try value.encode(to: encoder)
            try c.encode("review", forKey: .type); try c.encode(id, forKey: .id); try c.encode(fallback, forKey: .fallbackText)
        case .other(let id, let type, let fallback):
            try c.encode(type, forKey: .type); try c.encode(id, forKey: .id); try c.encode(fallback, forKey: .fallbackText)
        }
    }
}

public struct APIErrorBody: Decodable, Sendable {
    public let detail: String?
}
