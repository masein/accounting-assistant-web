import CryptoKit
import Foundation

/// An error the server (or the network) gave, said the way the app shows it.
public struct APIError: Error, LocalizedError, Equatable, Sendable {
    public let status: Int
    public let detail: String
    public let code: String?

    public init(status: Int, detail: String, code: String?) {
        self.status = status
        self.detail = detail
        self.code = code
    }

    public var errorDescription: String? { detail }

    /// The session is over (refresh refused, signed out elsewhere): back to the sign-in screen.
    public var endsSession: Bool { status == 401 }

    /// This version of the app is older than the server's minimum (426, code `upgrade_required`).
    public var updateRequired: Bool { status == 426 || code == "upgrade_required" }

    /// No answer at all (offline, a dropped connection, a stream cut short): the same message may be sent again.
    public var isNetwork: Bool { status == -1 }

    static let offline = APIError(status: -1, detail: "Can't reach the server. Check the connection and try again.",
                                  code: "offline")
}

/// A file uploaded to the server, its id to send with the next message.
public struct Attachment: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let fileName: String?
    public let contentType: String?
    public let sizeBytes: Int?

    public init(id: String, fileName: String?, contentType: String?, sizeBytes: Int?) {
        self.id = id
        self.fileName = fileName
        self.contentType = contentType
        self.sizeBytes = sizeBytes
    }
}

/// Where a piecewise upload stands on the server (`POST /uploads/begin`, `PUT /uploads/{id}?offset=`).
public struct UploadState: Codable, Equatable, Sendable {
    public let id: String
    public let received: Int
    public let sizeBytes: Int?
    public let maxChunk: Int?
    public let done: Bool?
    public let attachment: Attachment?
}

/// The mobile API (/api/mobile/v1): the same sign-in, threads and chat as the Android app. Access tokens last
/// minutes; on a 401 the client trades its refresh token for a new pair once, then retries the request.
public actor APIClient {
    public nonisolated let baseURL: URL
    private let session: URLSession
    private let tokens: TokenStore
    private let device: DeviceInfo
    private var refreshing: Task<Void, Error>?

    public static let apiPath = "api/mobile/v1"

    public init(baseURL: URL, tokens: TokenStore, device: DeviceInfo = .current, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.tokens = tokens
        self.device = device
        self.session = session
    }

    // MARK: - Signing in

    public func signIn(username: String, password: String) async throws -> SignInResult {
        var body = device.payload
        body["username"] = username
        body["password"] = password
        let data = try await exchange("POST", "auth/login", body: .json(body), authorized: false).data
        if let challenge = try? JSONDecoder.api.decode(TwoFactorChallenge.self, from: data), challenge.twoFactorRequired {
            return .twoFactor(challenge: challenge.challenge)
        }
        let signIn = try JSONDecoder.api.decode(SignIn.self, from: data)
        tokens.save(Tokens(access: signIn.accessToken, refresh: signIn.refreshToken))
        return .signedIn(signIn)
    }

    public func confirmTwoFactor(challenge: String, code: String) async throws -> SignIn {
        var body = device.payload
        body["challenge"] = challenge
        body["code"] = code
        let data = try await exchange("POST", "auth/2fa", body: .json(body), authorized: false).data
        let signIn = try JSONDecoder.api.decode(SignIn.self, from: data)
        tokens.save(Tokens(access: signIn.accessToken, refresh: signIn.refreshToken))
        return signIn
    }

    /// Ends this device's session on the server too (best effort), and forgets the tokens.
    public func signOut() async {
        _ = try? await exchange("DELETE", "session")
        tokens.clear()
    }

    public nonisolated var isSignedIn: Bool { tokens.load() != nil }

    public func me() async throws -> Me {
        try await decode(Me.self, "GET", "me")
    }

    // MARK: - Chat

    public func threads() async throws -> [ChatThread] {
        try await decode([ChatThread].self, "GET", "threads")
    }

    /// The latest messages of a conversation (the server's default page).
    public func messages(thread: String) async throws -> [ChatMessage] {
        try await messagesPage(thread: thread).messages
    }

    /// A page of a conversation, oldest first: the latest `limit`; the page `before` a message id (`moreBefore`
    /// says whether there is another); or what came `after` one, to catch up.
    public func messagesPage(thread: String, before: String? = nil, after: String? = nil,
                             limit: Int? = nil) async throws -> MessagesPage {
        var query: [URLQueryItem] = []
        if let before { query.append(URLQueryItem(name: "before", value: before)) }
        if let after { query.append(URLQueryItem(name: "after", value: after)) }
        if let limit { query.append(URLQueryItem(name: "limit", value: String(limit))) }
        let answer = try await exchange("GET", "threads/\(thread)/messages", query: query)
        let messages = try Self.decodeBody([ChatMessage].self, answer.data.isEmpty ? Data("[]".utf8) : answer.data)
        return MessagesPage(messages: messages,
                            moreBefore: answer.response.value(forHTTPHeaderField: "X-More-Before") == "true")
    }

    /// One message to the assistant. `clientMessageID` makes a retry safe: the server answers a repeat once.
    /// `attachmentIDs` are files uploaded first (`upload`), as the Android app sends them.
    public func send(message: String, thread: String?, clientMessageID: String,
                     attachmentIDs: [String] = []) async throws -> ChatReply {
        try await decode(ChatReply.self, "POST", "chat",
                         body: .json(Self.chatBody(message, thread, clientMessageID, attachmentIDs)), timeout: 180)
    }

    /// The same turn, streamed (`/chat/stream`, server-sent events): `onStatus` hears each step as it happens
    /// ("Reading the books…"), then the reply comes back. A dropped connection, or a stream that ends without a
    /// reply, is a network error (`isNetwork`): send again with the same `clientMessageID` (to `/chat`).
    public func sendStreaming(message: String, thread: String?, clientMessageID: String, attachmentIDs: [String] = [],
                              onStatus: @escaping @Sendable (String) -> Void) async throws -> ChatReply {
        let body = Self.chatBody(message, thread, clientMessageID, attachmentIDs)
        var retried = false
        while true {
            let request = try makeRequest("POST", "chat/stream", body: .json(body), authorized: true, timeout: 180,
                                          accept: "text/event-stream")
            let bytes: URLSession.AsyncBytes, response: URLResponse
            do {
                (bytes, response) = try await session.bytes(for: request)
            } catch {
                throw APIError.offline
            }
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            if status == 401 && !retried && tokens.load() != nil {
                retried = true
                try await refreshTokens()
                continue
            }
            guard (200..<300).contains(status) else {
                var data = Data()
                do { for try await byte in bytes { data.append(byte); if data.count > 65_536 { break } } } catch {}
                throw refusal(status: status, data: data, response: response as? HTTPURLResponse)
            }
            var parser = SSEParser()
            var reply: ChatReply?
            func handle(_ events: [SSEParser.Event]) throws {
                for event in events {
                    switch event.name {
                    case "status":
                        if let text = event.json?["text"] as? String, !text.isEmpty { onStatus(text) }
                    case "reply":
                        reply = try Self.decodeBody(ChatReply.self, Data(event.data.utf8))
                    case "error":
                        let o = event.json ?? [:]
                        let status = (o["status"] as? Int) ?? Int(o["status"] as? String ?? "") ?? 500
                        throw APIError(status: status, detail: (o["detail"] as? String) ?? "The server answered \(status).",
                                       code: o["code"] as? String)
                    default:
                        break
                    }
                }
            }
            var line = Data()
            do {
                for try await byte in bytes {
                    if byte == 0x0A {
                        try handle(parser.feed(line: String(decoding: line, as: UTF8.self)))
                        line.removeAll(keepingCapacity: true)
                    } else {
                        line.append(byte)
                    }
                }
            } catch let e as APIError {
                throw e
            } catch {
                throw APIError.offline
            }
            if !line.isEmpty { try handle(parser.feed(line: String(decoding: line, as: UTF8.self))) }
            try handle(parser.finish())
            guard let reply else {
                throw APIError(status: -1, detail: "The answer was cut short. Try again.", code: "stream_ended")
            }
            return reply
        }
    }

    static func chatBody(_ message: String, _ thread: String?, _ clientMessageID: String,
                         _ attachmentIDs: [String]) -> [String: Any] {
        var body: [String: Any] = ["message": message, "client_message_id": clientMessageID]
        if let thread { body["thread_id"] = thread }
        if !attachmentIDs.isEmpty { body["attachment_ids"] = attachmentIDs }
        return body
    }

    public func confirm(proposal token: String) async throws -> ProposalOutcome {
        try await decode(ProposalOutcome.self, "POST", "proposals/\(token)/confirm", body: .json([:]))
    }

    public func cancel(proposal token: String) async throws -> ProposalOutcome {
        try await decode(ProposalOutcome.self, "POST", "proposals/\(token)/cancel", body: .json([:]))
    }

    /// Takes a confirmed entry back, within its undo window (410 after it).
    public func undo(posting auditLogID: String) async throws -> ProposalOutcome {
        try await decode(ProposalOutcome.self, "POST", "postings/\(auditLogID)/undo", body: .json([:]))
    }

    // MARK: - Approvals (a voucher above the company's threshold waits for a second person)

    /// The vouchers waiting for a second person, as approval cards (the ones this user asked for, to withdraw).
    public func approvals() async throws -> [Block] {
        try await decode([Block].self, "GET", "approvals")
    }

    /// Someone else's voucher, posted by this person's approval: the stamped receipt comes back.
    public func approve(_ token: String) async throws -> ProposalOutcome {
        try await decode(ProposalOutcome.self, "POST", "approvals/\(token)/approve", body: .json([:]))
    }

    /// Turned down, with a reason for the one who asked.
    public func reject(_ token: String, note: String?) async throws -> ProposalOutcome {
        let note = note?.trimmingCharacters(in: .whitespacesAndNewlines)
        return try await decode(ProposalOutcome.self, "POST", "approvals/\(token)/reject",
                                body: .json(note.map { $0.isEmpty ? [:] : ["note": $0] } ?? [:]))
    }

    // MARK: - Files

    /// A document from the books (a file block's path: an invoice's PDF, a payslip), fetched with this session.
    public func download(_ file: FileRef) async throws -> Data {
        guard file.isPhoneDocument else {
            throw APIError(status: 0, detail: "That isn't one of the server's documents.", code: "not_a_document")
        }
        let relative = String(file.path.dropFirst(Self.apiPath.count + 2))
        return try await exchange("GET", relative, timeout: 60, accept: "*/*").data
    }

    /// The first piece of an upload: a few seconds on a slow line.
    public static let firstPiece = 64 * 1024
    public static let maxPiece = 1024 * 1024

    /// The next piece from how long the last took (as the Android app sizes them): twice as large on a fast line,
    /// half on a slow one, never above the server's `max` (1 MB).
    public static func nextPiece(_ piece: Int, seconds: Double, max: Int) -> Int {
        if seconds < 1 { return Swift.min(piece * 2, max) }
        if seconds > 4 { return Swift.max(piece / 2, Swift.min(firstPiece, max)) }
        return Swift.min(piece, max)
    }

    /// A photo, a PDF, a statement, sent in pieces (resumable): after a dropped connection the next call goes on
    /// from what the server kept (it finds the upload by the file's hash), and a file the server already has isn't
    /// sent again. Its id then goes with the chat message. `progress` runs from 0 to 1.
    public func upload(_ data: Data, fileName: String, contentType: String,
                       progress: @escaping @Sendable (Double) -> Void = { _ in }) async throws -> Attachment {
        guard !data.isEmpty else { throw APIError(status: 400, detail: "The file is empty.", code: "empty") }
        let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        let begin: [String: Any] = ["file_name": fileName, "content_type": contentType, "size_bytes": data.count,
                                    "sha256": hash]
        var state = try await decode(UploadState.self, "POST", "uploads/begin", body: .json(begin))
        var restarted = false
        var still = 0
        var piece = min(Self.firstPiece, state.maxChunk ?? Self.maxPiece)
        while true {
            if let attachment = state.attachment { progress(1); return attachment }
            let from = max(0, min(state.received, data.count))
            progress(Double(from) / Double(data.count))
            let limit = state.maxChunk ?? Self.maxPiece
            let count = min(piece, limit, data.count - from)
            let started = Date()
            let next: UploadState
            do {
                let start = data.startIndex + from
                next = try await decode(UploadState.self, "PUT", "uploads/\(state.id)",
                                        query: [URLQueryItem(name: "offset", value: String(from))],
                                        body: .raw(data.subdata(in: start..<start + count), "application/octet-stream"),
                                        timeout: 120)
            } catch let e as APIError where !restarted && (e.code == "upload_corrupt" || e.status == 404) {
                // damaged on the way, or forgotten: once more from what the server has
                restarted = true
                next = try await decode(UploadState.self, "POST", "uploads/begin", body: .json(begin))
            }
            piece = Self.nextPiece(piece, seconds: Date().timeIntervalSince(started), max: limit)
            still = next.received <= state.received && next.attachment == nil ? still + 1 : 0
            if still > 2 { throw APIError(status: -1, detail: "The upload stopped advancing.", code: "upload_stalled") }
            state = next
        }
    }

    /// A voice note's words, for the user to check before sending.
    public func transcribe(_ data: Data, fileName: String, contentType: String) async throws -> String {
        let boundary = "Boundary-\(UUID().uuidString)"
        var body = Data()
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(fileName)\"\r\n".utf8))
        body.append(Data("Content-Type: \(contentType)\r\n\r\n".utf8))
        body.append(data)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        let reply = try await decode(Transcript.self, "POST", "transcribe",
                                     body: .raw(body, "multipart/form-data; boundary=\(boundary)"), timeout: 120)
        return (reply.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: - Requests

    enum Body {
        case json([String: Any])
        case raw(Data, String)
    }

    private func decode<T: Decodable>(_ type: T.Type, _ method: String, _ path: String, query: [URLQueryItem] = [],
                                      body: Body? = nil, timeout: TimeInterval = 30) async throws -> T {
        try Self.decodeBody(T.self, await exchange(method, path, query: query, body: body, timeout: timeout).data)
    }

    static func decodeBody<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do {
            return try JSONDecoder.api.decode(T.self, from: data)
        } catch {
            throw APIError(status: 0, detail: "The server's answer wasn't understood.", code: "decode")
        }
    }

    private func makeRequest(_ method: String, _ path: String, query: [URLQueryItem] = [], body: Body?,
                             authorized: Bool, timeout: TimeInterval, accept: String) throws -> URLRequest {
        var url = baseURL.appendingPathComponent(Self.apiPath).appendingPathComponent(path)
        if !query.isEmpty, var parts = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            parts.queryItems = query
            url = parts.url ?? url
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = timeout
        request.setValue(accept, forHTTPHeaderField: "Accept")
        request.setValue(device.appVersion, forHTTPHeaderField: "X-App-Version")
        if let language = device.language { request.setValue(language, forHTTPHeaderField: "Accept-Language") }
        switch body {
        case .json(let json):
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
        case .raw(let data, let type):
            request.setValue(type, forHTTPHeaderField: "Content-Type")
            request.httpBody = data
        case nil:
            break
        }
        if authorized, let access = tokens.load()?.access {
            request.setValue("Bearer \(access)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    /// One call, renewing an expired access token once; a refusal is thrown as `APIError`.
    private func exchange(_ method: String, _ path: String, query: [URLQueryItem] = [], body: Body? = nil,
                          authorized: Bool = true, retried: Bool = false, timeout: TimeInterval = 30,
                          accept: String = "application/json") async throws -> (data: Data, response: HTTPURLResponse) {
        let request = try makeRequest(method, path, query: query, body: body, authorized: authorized, timeout: timeout,
                                      accept: accept)
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw APIError.offline
        }
        let http = response as? HTTPURLResponse
        let status = http?.statusCode ?? 0
        if status == 401 && authorized && !retried && tokens.load() != nil {
            try await refreshTokens()
            return try await exchange(method, path, query: query, body: body, authorized: authorized, retried: true,
                                      timeout: timeout, accept: accept)
        }
        guard (200..<300).contains(status), let http else {
            if status == 401 && authorized { tokens.clear() }
            throw refusal(status: status, data: data, response: http)
        }
        return (data, http)
    }

    private func refusal(status: Int, data: Data, response: HTTPURLResponse?) -> APIError {
        let body = try? JSONDecoder.api.decode(APIErrorBody.self, from: data)
        let code = body?.code ?? response?.value(forHTTPHeaderField: "X-Error-Code")
        return APIError(status: status, detail: body?.detail ?? "The server answered \(status).", code: code)
    }

    /// One refresh at a time: requests that all got a 401 wait for the same new pair.
    private func refreshTokens() async throws {
        if let refreshing { return try await refreshing.value }
        let task = Task { try await self.exchangeRefreshToken() }
        refreshing = task
        defer { refreshing = nil }
        do {
            try await task.value
        } catch let e as APIError where e.updateRequired {
            throw e
        } catch {
            tokens.clear()
            throw (error as? APIError) ?? APIError(status: 401, detail: "Sign in again.", code: "session_ended")
        }
    }

    private func exchangeRefreshToken() async throws {
        guard let refresh = tokens.load()?.refresh else {
            throw APIError(status: 401, detail: "Sign in again.", code: "session_ended")
        }
        let data = try await exchange("POST", "auth/refresh",
                                      body: .json(["refresh_token": refresh, "app_version": device.appVersion]),
                                      authorized: false, retried: true).data
        let signIn = try JSONDecoder.api.decode(SignIn.self, from: data)
        tokens.save(Tokens(access: signIn.accessToken, refresh: signIn.refreshToken))
    }
}

struct TwoFactorChallenge: Decodable {
    let twoFactorRequired: Bool
    let challenge: String
}

struct Transcript: Decodable {
    let text: String?
}

public struct Me: Decodable, Equatable, Sendable {
    public let user: User
    public let company: Company?
}

/// Server-sent events (`/chat/stream`), line by line: `event:` names the next one, `data:` lines make its data, a
/// blank line sends it. A new `event:` line, or the end of the stream, also sends what was gathered, so a stream
/// whose blank lines went missing still reads.
public struct SSEParser: Sendable {
    public struct Event: Equatable, Sendable {
        public let name: String
        public let data: String

        public init(name: String, data: String) {
            self.name = name
            self.data = data
        }

        /// The data as a JSON object (the server sends one per event).
        public var json: [String: Any]? {
            (try? JSONSerialization.jsonObject(with: Data(data.utf8))) as? [String: Any]
        }
    }

    private var name: String?
    private var data: [String] = []

    public init() {}

    public mutating func feed(line raw: String) -> [Event] {
        let line = raw.hasSuffix("\r") ? String(raw.dropLast()) : raw
        if line.isEmpty { return dispatch() }
        if line.hasPrefix(":") { return [] }                          // a comment (a keep-alive)
        let field: Substring, value: Substring
        if let colon = line.firstIndex(of: ":") {
            field = line[..<colon]
            let rest = line[line.index(after: colon)...]
            value = rest.hasPrefix(" ") ? rest.dropFirst() : rest
        } else {
            field = Substring(line)
            value = ""
        }
        switch field {
        case "event":
            let sent = data.isEmpty ? [] : dispatch()
            name = String(value)
            return sent
        case "data":
            data.append(String(value))
            return []
        default:
            return []
        }
    }

    public mutating func finish() -> [Event] { dispatch() }

    private mutating func dispatch() -> [Event] {
        defer { name = nil; data = [] }
        guard !data.isEmpty else { return [] }
        return [Event(name: name ?? "message", data: data.joined(separator: "\n"))]
    }

    /// Every event in a whole stream's text.
    public static func events(in text: String) -> [Event] {
        var parser = SSEParser()
        var out: [Event] = []
        // by scalars: "\r\n" is one Character, and the stream is split at each "\n" byte
        for line in text.unicodeScalars.split(separator: "\n", omittingEmptySubsequences: false) {
            out += parser.feed(line: String(String.UnicodeScalarView(line)))
        }
        return out + parser.finish()
    }
}

/// What the server is told about this device at sign-in (it lists it under the user's devices): an iPhone, an iPad
/// or a Mac, by its name.
public struct DeviceInfo: Sendable {
    public var name: String
    public var platform: String
    public var appVersion: String
    public var language: String?

    public init(name: String, platform: String, appVersion: String, language: String?) {
        self.name = name
        self.platform = platform
        self.appVersion = appVersion
        self.language = language
    }

    var payload: [String: Any] { ["device_name": name, "platform": platform, "app_version": appVersion] }

    public enum Kind: String, Sendable { case iPhone, iPad, mac }

    /// The kind from the hardware model ("iPad14,1", "iPhone15,2"); an unknown one is taken for an iPhone.
    public static func kind(model: String?) -> Kind {
        let m = (model ?? "").lowercased()
        if m.hasPrefix("ipad") { return .iPad }
        if m.hasPrefix("mac") { return .mac }
        return .iPhone
    }

    public static func make(kind: Kind, macName: String? = nil, appVersion: String, language: String?) -> DeviceInfo {
        let (name, platform): (String, String)
        switch kind {
        case .iPhone: (name, platform) = ("iPhone", "ios")
        case .iPad: (name, platform) = ("iPad", "ipados")
        case .mac: (name, platform) = (macName.flatMap { $0.isEmpty ? nil : $0 } ?? "Mac", "macos")
        }
        return DeviceInfo(name: String(name.prefix(80)), platform: platform, appVersion: appVersion, language: language)
    }

    public static var current: DeviceInfo {
        let version = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "1.0.0"
        let language = Locale.preferredLanguages.first
        #if os(iOS)
        if ProcessInfo.processInfo.isiOSAppOnMac { return make(kind: .mac, appVersion: version, language: language) }
        return make(kind: kind(model: hardwareModel), appVersion: version, language: language)
        #else
        return make(kind: .mac, macName: Host.current().localizedName, appVersion: version, language: language)
        #endif
    }

    /// "iPad14,1": the Simulator says which device it plays; a device, its own model.
    static var hardwareModel: String? {
        if let simulated = ProcessInfo.processInfo.environment["SIMULATOR_MODEL_IDENTIFIER"] { return simulated }
        var size = 0
        guard sysctlbyname("hw.machine", nil, &size, nil, 0) == 0, size > 0 else { return nil }
        var chars = [CChar](repeating: 0, count: size)
        guard sysctlbyname("hw.machine", &chars, &size, nil, 0) == 0 else { return nil }
        return String(cString: chars)
    }
}

extension JSONDecoder {
    static let api: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
}
