import Combine
import Foundation

// Support: the person's chat with the support desk — Claude in the owner's Nightshift, on their Mac — which
// answers, and the night shift fixes what it reports (app/api/support.py, DEPLOY.md §16, scenarios DA1–DA8 and
// DC1–DC9). The web page's headset button beside the bell and this app's are the same conversation.

/// A message in the conversation: the person's own ("user") or the desk's ("desk").
public struct SupportMessage: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public let author: String
    public let channel: String?
    public let text: String?
    public let attachments: [String]?
    public let at: String?
    /// a desk reply read (nil for the person's own)
    public let seen: Bool?

    public init(id: String, author: String, channel: String? = nil, text: String?, attachments: [String]? = nil,
                at: String?, seen: Bool? = nil) {
        self.id = id
        self.author = author
        self.channel = channel
        self.text = text
        self.attachments = attachments
        self.at = at
        self.seen = seen
    }

    public var fromDesk: Bool { author == "desk" }
    public var date: Date? { ServerTime.date(at) }
}

public struct SupportDesk: Codable, Equatable, Sendable {
    public let online: Bool
}

/// GET /support: the newest messages (or those after one), the unread replies, whether the desk is online.
public struct SupportConversation: Decodable, Equatable, Sendable {
    public let threadId: String?
    public let messages: [SupportMessage]
    public let unread: Int
    public let desk: SupportDesk?
    public let dailyLimit: Int?

    enum CodingKeys: String, CodingKey { case threadId, messages, unread, desk, dailyLimit }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        threadId = try c.decodeIfPresent(String.self, forKey: .threadId)
        messages = try c.decodeIfPresent([SupportMessage].self, forKey: .messages) ?? []
        unread = try c.decodeIfPresent(Int.self, forKey: .unread) ?? 0
        desk = try c.decodeIfPresent(SupportDesk.self, forKey: .desk)
        dailyLimit = try c.decodeIfPresent(Int.self, forKey: .dailyLimit)
    }
}

/// POST /support/messages: the message as kept, and whether the desk is online.
public struct SupportSent: Decodable, Equatable, Sendable {
    public let message: SupportMessage
    public let desk: SupportDesk?
}

struct SupportSeen: Decodable {
    let seen: Int?
}

/// A call the server refused (400 and up; not an expired token renewed, not support's own), kept to go with a
/// support message: its request id finds it in the server's log.
public struct FailedCall: Equatable, Sendable {
    public let method: String
    public let path: String
    public let status: Int
    public let requestID: String?
    public let at: String

    public init(method: String, path: String, status: Int, requestID: String?, at: String) {
        self.method = method
        self.path = path
        self.status = status
        self.requestID = requestID
        self.at = at
    }

    var payload: [String: Any] {
        var out: [String: Any] = ["method": method, "path": path, "status": status, "at": at]
        if let requestID, !requestID.isEmpty { out["request_id"] = requestID }
        return out
    }

    static func stamp(_ date: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.string(from: date)
    }
}

/// What the app knew when the message was sent, besides the refused calls (which the client keeps itself).
public struct SupportContext: Equatable, Sendable {
    /// where the person was: "chat", "chat/<conversation id>"
    public var page: String
    /// the person's language (their setting on the server)
    public var language: String
    /// the window's size in points, "390x844"
    public var screen: String?

    public init(page: String, language: String, screen: String? = nil) {
        self.page = page
        self.language = language
        self.screen = screen
    }

    func payload(device: DeviceInfo, errors: [FailedCall]) -> [String: Any] {
        let os = ProcessInfo.processInfo.operatingSystemVersion
        var out: [String: Any] = [
            "page": page, "app_version": device.appVersion, "language": language, "platform": "ios",
            // which of the three it is (an iPhone, an iPad or a Mac) and its system
            "user_agent": "AccountingChat/\(device.appVersion) (\(device.platform); \(os.majorVersion).\(os.minorVersion).\(os.patchVersion))",
            "errors": errors.suffix(APIClient.keptFailures).map(\.payload),
        ]
        if let screen { out["screen"] = screen }
        return out
    }
}

/// Something the Support screen says in the person's words (L10n), not the server's.
public enum SupportNotice: Equatable, Sendable {
    case sendFailed, uploadFailed, tooLarge
}

/// The server's times ("2026-11-28T10:20:30.123456+00:00", or without the fraction).
enum ServerTime {
    static func date(_ iso: String?) -> Date? {
        guard let iso, !iso.isEmpty else { return nil }
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = f.date(from: iso) { return d }
        f.formatOptions = [.withInternetDateTime]
        return f.date(from: iso)
    }
}

/// The conversation with the support desk: loaded and marked seen while the Support screen shows (and what's new
/// every five seconds), the unread replies counted every minute for the toolbar's badge, a message sent with its
/// screenshots and what the app knew.
@MainActor
public final class SupportModel: ObservableObject {
    @Published public private(set) var messages: [SupportMessage] = []
    @Published public private(set) var online = false
    /// the desk's replies not read yet: the toolbar's badge (0 while the screen shows)
    @Published public private(set) var unread = 0
    @Published public private(set) var loaded = false
    @Published public private(set) var sending = false
    @Published public private(set) var uploading = false
    @Published public private(set) var attachments: [PendingFile] = []
    @Published public var draft = ""
    /// the server's own words (past the day's limit, a file it won't take), already in the person's language
    @Published public var error: String?
    @Published public var notice: SupportNotice?
    /// the Support screen asked for (the toolbar's headset, a scripted run)
    @Published public var presented = false
    /// the Support screen shows: what's new every five seconds, the replies marked seen as they come
    @Published public private(set) var isOpen = false

    public nonisolated static let openInterval: Duration = .seconds(5)
    public nonisolated static let closedInterval: Duration = .seconds(60)
    /// what the server keeps for support: a picture or a PDF, 20 MB at most, five to a message
    public static let maxFileBytes = 20 * 1024 * 1024
    public static let maxFiles = 5

    let api: APIClient
    /// the person's language, for the server's refusals
    public var language: String
    /// where the person was when they opened Support ("chat", "chat/<id>")
    public var page = "chat"
    /// the window's size in points ("1000x720")
    public var screen: String?
    var onSessionEnded: (() -> Void)?
    var onUpdateRequired: (() -> Void)?
    /// the newest message's time as the server wrote it: only what came after is asked for
    private var after: String?
    private var watchers = 0

    public init(api: APIClient, language: String = "en") {
        self.api = api
        self.language = language
    }

    public var context: SupportContext { SupportContext(page: page, language: language, screen: screen) }

    public var canSend: Bool {
        !sending && !uploading && (!draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty)
    }

    public var canAttach: Bool { !uploading && !sending && attachments.count < Self.maxFiles }

    /// What's new (`full`: the newest page, as when the screen opens), merged in; while the screen shows, the
    /// desk's replies are marked seen at once.
    public func refresh(full: Bool = false) async {
        do {
            let page = try await api.supportConversation(after: full ? nil : after)
            if let desk = page.desk { online = desk.online }
            merge(page.messages)
            // only a fetch moves `after` on: a reply written while a message was being sent isn't skipped
            // (and never back, should an older answer arrive last)
            if let newest = page.messages.last?.at,
               (ServerTime.date(newest) ?? .distantPast) >= (ServerTime.date(after) ?? .distantPast) {
                after = newest
            }
            loaded = true
            if isOpen {
                if page.unread > 0 { await markSeen() } else { unread = 0 }
            } else {
                unread = page.unread
            }
        } catch {
            quiet(error)
        }
    }

    /// The desk's replies read, here and on the web: the badge goes.
    public func markSeen() async {
        do {
            try await api.markSupportSeen()
            unread = 0
        } catch {
            quiet(error)
        }
    }

    /// While the Support screen shows (its `.task`): the conversation now, then what's new every five seconds.
    public func watchOpen(every interval: Duration = SupportModel.openInterval) async {
        watchers += 1
        isOpen = true
        unread = 0
        defer {
            watchers -= 1
            isOpen = watchers > 0
        }
        await refresh(full: true)
        while !Task.isCancelled {
            try? await Task.sleep(for: interval)
            if Task.isCancelled { break }
            await refresh()
        }
    }

    /// While signed in (the chat screen's `.task`): the unread replies, now and every minute, for the badge.
    public func watchUnread(every interval: Duration = SupportModel.closedInterval) async {
        while !Task.isCancelled {
            if !isOpen { await refresh() }
            try? await Task.sleep(for: interval)
        }
    }

    public func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let files = attachments
        guard canSend else { return }
        sending = true
        error = nil
        notice = nil
        defer { sending = false }
        do {
            let sent = try await api.sendSupport(text: text, attachmentIDs: files.map(\.id), context: context)
            if draft.trimmingCharacters(in: .whitespacesAndNewlines) == text { draft = "" }
            attachments.removeAll { file in files.contains { $0.id == file.id } }
            merge([sent.message])
            if let desk = sent.desk { online = desk.online }
            await refresh()
        } catch let e as APIError where e.endsSession || e.updateRequired {
            report(e)
        } catch let e as APIError where (400..<500).contains(e.status) {
            error = e.detail                                       // the day's limit, an empty message: its words
        } catch {
            notice = .sendFailed
        }
    }

    /// A screenshot (a photo from the library) or a PDF for the next message: uploaded now, its id sent with it.
    public func attach(_ data: Data, name: String, mime: String) async {
        guard canAttach else { return }
        notice = nil
        error = nil
        let file = Attachments.prepare(data, name: name, mime: mime)
        guard file.data.count <= Self.maxFileBytes else { notice = .tooLarge; return }
        uploading = true
        defer { uploading = false }
        do {
            let a = try await api.uploadSupportFile(file.data, fileName: file.name, contentType: file.mime,
                                                    language: language)
            attachments.append(PendingFile(id: a.id, name: a.fileName ?? file.name, mime: a.contentType ?? file.mime))
        } catch let e as APIError where e.endsSession || e.updateRequired {
            report(e)
        } catch let e as APIError where (400..<500).contains(e.status) {
            error = e.detail                                       // not a picture or a PDF: its words
        } catch {
            notice = .uploadFailed
        }
    }

    public func detach(_ id: String) {
        attachments.removeAll { $0.id == id }
    }

    public func say(_ notice: SupportNotice?) { self.notice = notice }

    /// New messages in, each once (by its id), in the order they were written.
    func merge(_ incoming: [SupportMessage]) {
        guard !incoming.isEmpty else { return }
        var all = messages
        for m in incoming {
            if let i = all.firstIndex(where: { $0.id == m.id }) { all[i] = m } else { all.append(m) }
        }
        let far = Date.distantFuture
        messages = all.enumerated()
            .sorted { a, b in
                let x = a.element.date ?? far, y = b.element.date ?? far
                return x != y ? x < y : a.offset < b.offset
            }
            .map(\.element)
    }

    /// A poll that failed says nothing (the next one tries again), unless the session is over or the app too old.
    private func quiet(_ error: Error) {
        if let e = error as? APIError, e.endsSession || e.updateRequired { report(e) }
    }

    private func report(_ e: APIError) {
        if e.updateRequired { onUpdateRequired?(); return }
        if e.endsSession { onSessionEnded?() }
    }
}
