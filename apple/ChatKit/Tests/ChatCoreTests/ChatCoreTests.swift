import Foundation
import Testing
@testable import ChatCore

// Swift Testing (not XCTest): the Command Line Tools have it, so `swift test` runs on any Mac, Xcode or not.

/// Answers recorded from the server (Fixtures/), and a stand-in for the network.
final class Stub: URLProtocol {
    struct Seen {
        let method: String; let path: String; let headers: [String: String]; let body: [String: Any]
        var query: [String: String] = [:]; var raw = Data()
        /// the query as it went on the wire (percent-encoded)
        var rawQuery = ""
    }
    nonisolated(unsafe) static var seen: [Seen] = []
    nonisolated(unsafe) static var handler: ((URLRequest, [String: Any]) -> (Int, Data, [String: String])) = { _, _ in (404, Data(), [:]) }
    /// for a request whose body isn't JSON (an upload's piece, a voice note): its bytes
    nonisolated(unsafe) static var rawHandler: ((URLRequest, Data) -> (Int, Data, [String: String]))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}

    override func startLoading() {
        var bodyData = request.httpBody
        if bodyData == nil, let stream = request.httpBodyStream {
            stream.open(); defer { stream.close() }
            var data = Data(); var buf = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable { let n = stream.read(&buf, maxLength: buf.count); if n <= 0 { break }; data.append(buf, count: n) }
            bodyData = data
        }
        let body = (bodyData.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]) ?? [:]
        let items = request.url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false)?.queryItems } ?? []
        Stub.seen.append(Seen(method: request.httpMethod ?? "GET", path: request.url?.path ?? "",
                              headers: request.allHTTPHeaderFields ?? [:], body: body,
                              query: Dictionary(items.map { ($0.name, $0.value ?? "") }, uniquingKeysWith: { a, _ in a }),
                              raw: bodyData ?? Data(), rawQuery: request.url?.query(percentEncoded: true) ?? ""))
        let (status, data, headers) = Stub.rawHandler?(request, bodyData ?? Data()) ?? Stub.handler(request, body)
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    static var session: URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [Stub.self]
        return URLSession(configuration: config)
    }

    static func reset(_ handler: @escaping (URLRequest, [String: Any]) -> (Int, Data, [String: String])) {
        seen = []
        rawHandler = nil
        self.handler = handler
    }

    static func resetRaw(_ handler: @escaping (URLRequest, Data) -> (Int, Data, [String: String])) {
        seen = []
        rawHandler = handler
    }
}

func fixture(_ name: String) -> Data {
    let url = Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")!
    return try! Data(contentsOf: url)
}

func json(_ object: Any) -> Data { try! JSONSerialization.data(withJSONObject: object) }

let server = URL(string: "https://books.example.com")!
let device = DeviceInfo(name: "Test Mac", platform: "macos", appVersion: "1.0.0", language: "en")

func signedInClient() -> APIClient {
    APIClient(baseURL: server, tokens: MemoryTokenStore(Tokens(access: "a", refresh: "r")), device: device, session: Stub.session)
}

/// The chat over the plain `/chat` (the streamed turn has its own tests).
@MainActor func plainChat() -> ChatModel {
    let chat = ChatModel(api: signedInClient())
    chat.streaming = false
    return chat
}

// The suites that use the network stand-in share it, so they run one at a time.
@Suite(.serialized) struct ChatCoreTests {

    @Suite struct Decoding {
        @Test func anAnswerWithACardDecodes() throws {
            let reply = try JSONDecoder.api.decode(ChatReply.self, from: fixture("chat-card"))
            #expect(reply.blocks.count == 2)
            guard case .proposal(_, let p) = reply.blocks[0] else { Issue.record("a card first"); return }
            #expect(p.tool == "propose_create_transaction")
            #expect(p.amount == Money(value: 250000, currency: "IRR"))
            #expect(p.date?.display == "19 Nov 2026")
            #expect(p.lines?.map(\.account) == ["6112", "1110"])
            #expect(p.lines?.first?.debit == 250000)
            #expect(p.canConfirm && p.canCancel && p.isPending)
            guard case .text(_, let text) = reply.blocks[1] else { Issue.record("then the text"); return }
            #expect(text.contains("Confirm"))
        }

        @Test func aThreadsHistoryDecodes() throws {
            let messages = try JSONDecoder.api.decode([ChatMessage].self, from: fixture("messages"))
            #expect(messages.map(\.role) == ["user", "assistant", "user", "assistant"])
            #expect(messages[0].text == "hi")
            #expect(messages[0].clientMessageId == "m1")
            guard case .proposal(_, let p)? = messages[3].blocks?.first else { Issue.record("the card comes back"); return }
            #expect(p.state == "pending")
            let threads = try JSONDecoder.api.decode([ChatThread].self, from: fixture("threads"))
            #expect(threads.first?.title == "hi")
        }

        @Test func aBlockTheAppDoesNotDrawShowsItsText() throws {
            let data = json(["type": "cash_table", "id": "c1", "fallback_text": "Cash: £1,200", "rows": [1, 2]])
            let block = try JSONDecoder.api.decode(Block.self, from: data)
            #expect(block == .other(id: "c1", type: "cash_table", fallback: "Cash: £1,200"))
            #expect(block.plainText == "Cash: £1,200")
        }

        @Test func aDrawnKindWithFieldsItCantReadFallsBackToItsSentence() throws {
            let data = json(["type": "figure", "id": "f1", "label": 7, "fallback_text": "Cash: 10"])
            #expect(try JSONDecoder.api.decode(Block.self, from: data) == .other(id: "f1", type: "figure", fallback: "Cash: 10"))
        }

        @Test func blocksRoundTrip() throws {
            let reply = try JSONDecoder.api.decode(ChatReply.self, from: fixture("chat-card"))
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            #expect(try JSONDecoder.api.decode([Block].self, from: encoder.encode(reply.blocks)) == reply.blocks)
        }
    }

    @Suite struct Client {
        @Test func signingInKeepsTheTokensAndSaysWhichDevice() async throws {
            Stub.reset { _, _ in (200, fixture("login"), [:]) }
            let tokens = MemoryTokenStore()
            let api = APIClient(baseURL: server, tokens: tokens, device: device, session: Stub.session)
            guard case .signedIn(let s) = try await api.signIn(username: "admin", password: "pw") else { Issue.record(); return }
            #expect(s.user.username == "admin")
            #expect(s.company?.name == "Default")
            #expect(tokens.load() == Tokens(access: "access-1", refresh: "refresh-1"))
            let req = try #require(Stub.seen.first)
            #expect(req.path == "/api/mobile/v1/auth/login")
            #expect(req.body["platform"] as? String == "macos")
            #expect(req.body["device_name"] as? String == "Test Mac")
            #expect(req.headers["X-App-Version"] == "1.0.0")
            #expect(req.headers["Authorization"] == nil)
        }

        @Test func twoFactorAsksForTheCode() async throws {
            Stub.reset { req, _ in
                req.url!.path.hasSuffix("/auth/login")
                    ? (200, json(["ok": false, "two_factor_required": true, "challenge": "ch-1"]), [:])
                    : (200, fixture("login"), [:])
            }
            let tokens = MemoryTokenStore()
            let api = APIClient(baseURL: server, tokens: tokens, device: device, session: Stub.session)
            #expect(try await api.signIn(username: "admin", password: "pw") == .twoFactor(challenge: "ch-1"))
            #expect(tokens.load() == nil)
            _ = try await api.confirmTwoFactor(challenge: "ch-1", code: "123456")
            #expect(Stub.seen.last?.body["challenge"] as? String == "ch-1")
            #expect(Stub.seen.last?.body["code"] as? String == "123456")
            #expect(tokens.load() != nil)
        }

        @Test func anExpiredAccessTokenIsRenewedOnceAndTheRequestRepeated() async throws {
            Stub.reset { req, body in
                if req.url!.path.hasSuffix("/auth/refresh") {
                    var signIn = try! JSONSerialization.jsonObject(with: fixture("login")) as! [String: Any]
                    signIn["access_token"] = body["refresh_token"] as? String == "old-refresh" ? "new-access" : "wrong"
                    signIn["refresh_token"] = "new-refresh"
                    return (200, json(signIn), [:])
                }
                return req.value(forHTTPHeaderField: "Authorization") == "Bearer new-access"
                    ? (200, fixture("threads"), [:]) : (401, json(["detail": "expired"]), [:])
            }
            let tokens = MemoryTokenStore(Tokens(access: "old-access", refresh: "old-refresh"))
            let api = APIClient(baseURL: server, tokens: tokens, device: device, session: Stub.session)
            #expect(try await api.threads().count == 3)
            #expect(tokens.load() == Tokens(access: "new-access", refresh: "new-refresh"))
            #expect(Stub.seen.map(\.path) == ["/api/mobile/v1/threads", "/api/mobile/v1/auth/refresh", "/api/mobile/v1/threads"])
        }

        @Test func aRefusedRefreshEndsTheSession() async throws {
            Stub.reset { req, _ in
                req.url!.path.hasSuffix("/auth/refresh")
                    ? (401, json(["detail": "Your session on this phone has ended. Sign in again."]), ["X-Error-Code": "session_ended"])
                    : (401, json(["detail": "expired"]), [:])
            }
            let tokens = MemoryTokenStore(Tokens(access: "a", refresh: "r"))
            let api = APIClient(baseURL: server, tokens: tokens, device: device, session: Stub.session)
            let error = await #expect(throws: APIError.self) { try await api.threads() }
            #expect(error?.endsSession == true)
            #expect(tokens.load() == nil)
        }

        @Test func sendingNamesTheThreadAndTheMessagesOwnId() async throws {
            Stub.reset { _, _ in (200, fixture("chat-card"), [:]) }
            let reply = try await signedInClient().send(message: "paid 20 for lunch", thread: "t-9", clientMessageID: "c-1")
            #expect(reply.threadId == "1db0f7f9-3252-4422-aa7e-f30021811755")
            let req = try #require(Stub.seen.first)
            #expect(req.method == "POST")
            #expect(req.headers["Authorization"] == "Bearer a")
            #expect(req.body["message"] as? String == "paid 20 for lunch")
            #expect(req.body["thread_id"] as? String == "t-9")
            #expect(req.body["client_message_id"] as? String == "c-1")
        }

        @Test func theServersWordsAreTheError() async throws {
            Stub.reset { _, _ in (409, json(["detail": "Still working on this message."]), ["X-Error-Code": "turn_in_progress"]) }
            let error = await #expect(throws: APIError.self) {
                try await signedInClient().send(message: "x", thread: nil, clientMessageID: "c")
            }
            #expect(error == APIError(status: 409, detail: "Still working on this message.", code: "turn_in_progress"))
        }
    }

    @Suite @MainActor struct Chat {
        @Test func sendingShowsTheMessageThenTheAnswerAndConfirmRecordsIt() async throws {
            Stub.reset { req, _ in
                switch req.url!.path {
                case let p where p.hasSuffix("/chat"): return (200, fixture("chat-card"), [:])
                case let p where p.hasSuffix("/confirm"): return (200, fixture("confirm"), [:])
                default: return (200, fixture("threads"), [:])
                }
            }
            let chat = plainChat()
            chat.draft = "  paid 250000 for lunch "
            #expect(chat.canSend)
            await chat.send()
            #expect(chat.draft == "")
            #expect(chat.messages.count == 2)
            #expect(chat.messages[0].text == "paid 250000 for lunch")
            #expect(chat.thread == "1db0f7f9-3252-4422-aa7e-f30021811755")
            #expect(chat.threads.count == 3)                          // a new conversation: the list is fetched again
            guard case .proposal(_, let p)? = chat.messages[1].blocks?.first else { Issue.record(); return }
            await chat.confirm(p.token)
            let blocks = chat.messages[1].blocks ?? []
            guard case .proposal(_, let after)? = blocks.first else { Issue.record(); return }
            #expect(after.state == "posted")
            #expect(!after.canConfirm)
            guard case .posted(_, let posted, _)? = blocks.dropFirst().first else { Issue.record("Recorded, under the card"); return }
            #expect(posted.canUndo)
            #expect(Stub.seen.contains { $0.path == "/api/mobile/v1/proposals/\(p.token)/confirm" })
        }

        @Test func undoTakesTheEntryBack() async throws {
            Stub.reset { req, _ in
                switch req.url!.path {
                case let p where p.hasSuffix("/chat"): return (200, fixture("chat-card"), [:])
                case let p where p.hasSuffix("/confirm"): return (200, json(["state": "posted", "block": ["type": "posted",
                    "id": "posted:x", "token": "x", "audit_log_id": "a-1", "undo_seconds": 120, "actions": ["undo"],
                    "fallback_text": "Posted"]]), [:])
                case let p where p.hasSuffix("/undo"): return (200, json(["state": "undone", "mode": "delete"]), [:])
                default: return (200, fixture("threads"), [:])
                }
            }
            let chat = plainChat()
            chat.draft = "x"
            await chat.send()
            guard case .proposal(_, let p)? = chat.messages.last?.blocks?.first else { Issue.record(); return }
            await chat.confirm(p.token)
            await chat.undo("a-1")
            guard case .posted(_, let posted, _)? = chat.messages.last?.blocks?.dropFirst().first else { Issue.record(); return }
            #expect(posted.undone == true && !posted.canUndo)
            #expect(Stub.seen.contains { $0.path == "/api/mobile/v1/postings/a-1/undo" })
        }

        @Test func aFailedSendCanBeRetriedWithTheSameId() async throws {
            nonisolated(unsafe) var fail = true
            Stub.reset { req, _ in
                if req.url!.path.hasSuffix("/chat") {
                    if fail { fail = false; return (502, Data("bad gateway".utf8), [:]) }
                    return (200, fixture("chat-hi"), [:])
                }
                return (200, fixture("threads"), [:])
            }
            let chat = plainChat()
            chat.draft = "hi"
            await chat.send()
            #expect(chat.error != nil)
            #expect(chat.canRetry)
            await chat.retry()
            #expect(chat.error == nil)
            let ids = Stub.seen.filter { $0.path.hasSuffix("/chat") }.compactMap { $0.body["client_message_id"] as? String }
            #expect(ids.count == 2 && ids[0] == ids[1])               // the server answers a repeat once
            #expect(chat.messages.filter(\.isUser).count == 1)
        }

        @Test func anExpiredCardSaysSo() async throws {
            Stub.reset { req, _ in
                req.url!.path.hasSuffix("/chat") ? (200, fixture("chat-card"), [:])
                    : req.url!.path.hasSuffix("/cancel") ? (410, json(["detail": "expired"]), [:]) : (200, fixture("threads"), [:])
            }
            let chat = plainChat()
            chat.draft = "x"
            await chat.send()
            guard case .proposal(_, let p)? = chat.messages.last?.blocks?.first else { Issue.record(); return }
            await chat.cancel(p.token)
            guard case .proposal(_, let after)? = chat.messages.last?.blocks?.first else { Issue.record(); return }
            #expect(after.state == "expired")
        }
    }

    @Suite @MainActor struct Session {
        @Test func serverAddressesAreHttpsExceptThisMachine() {
            let app = AppModel(defaults: UserDefaults(suiteName: "chatcore-tests-\(UUID())")!, tokens: { _ in MemoryTokenStore() })
            #expect(app.serverURL("accounting.example.com/")?.absoluteString == "https://accounting.example.com")
            #expect(app.serverURL(" https://a.example.com ")?.absoluteString == "https://a.example.com")
            #expect(app.serverURL("http://localhost:18000")?.absoluteString == "http://localhost:18000")
            #expect(app.serverURL("http://accounting.example.com") == nil)       // never a password over plain http
            #expect(app.serverURL("") == nil)
            #expect(app.serverAddress == AppModel.defaultServer)
        }

        @Test func signingInAndOut() async throws {
            Stub.reset { req, _ in req.url!.path.hasSuffix("/auth/login") ? (200, fixture("login"), [:]) : (204, Data(), [:]) }
            let tokens = MemoryTokenStore()
            let app = AppModel(defaults: UserDefaults(suiteName: "chatcore-tests-\(UUID())")!, session: Stub.session,
                               tokens: { _ in tokens })
            app.serverAddress = "https://books.example.com"
            await app.signIn(username: "admin", password: "pw")
            #expect(app.phase == .signedIn)
            #expect(app.chat != nil)
            #expect(app.user?.username == "admin")
            await app.signOut()
            #expect(app.phase == .signedOut)
            #expect(tokens.load() == nil)
            #expect(Stub.seen.contains { $0.method == "DELETE" && $0.path == "/api/mobile/v1/session" })
        }
    }
}

@Suite struct Words {
    @Test func everyWordInEveryLanguage() {
        for lang in L10n.supported {
            let table = L10n.table[lang] ?? [:]
            for key in L10n.Key.allCases {
                #expect(!(table[key] ?? "").isEmpty, "\(lang): \(key)")
            }
        }
        #expect(L10n("fa-IR").isRTL)
        #expect(L10n("ar").isRTL)
        #expect(!L10n("en-GB").isRTL)
        #expect(L10n("de").language == "en")
        // Persian formatting isolates the name (U+2068 … U+2069), so a Latin name sits right in an RTL sentence
        #expect(L10n("fa")(.signOutConfirm, "Default") == "از \u{2068}Default\u{2069} روی این دستگاه خارج می‌شوید؟")
    }
}

/// The contract every client of the replies keeps (docs/contracts/README.md): the server's recorded conversations,
/// read straight from the repository so this app breaks when they change, as the Android app's ContractTest does.
@Suite struct Contract {
    static let conversations: URL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()   // Tests/ChatCoreTests → ChatKit
        .deletingLastPathComponent().deletingLastPathComponent()                               // → apple → the repository
        .appendingPathComponent("docs/contracts/conversations")

    struct Recording: Decodable { let conversation: String; let blocks: [Block] }

    func recordings() throws -> [Recording] {
        let files = try FileManager.default.contentsOfDirectory(at: Self.conversations, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
        #expect(files.count >= 18, "the recordings moved?")
        return try files.map { try JSONDecoder.api.decode(Recording.self, from: Data(contentsOf: $0)) }
    }

    @Test func everyRecordedBlockReadsAndSaysSomething() throws {
        var drawn = Set<String>()
        for rec in try recordings() {
            #expect(!rec.blocks.isEmpty, "\(rec.conversation)")
            for block in rec.blocks {
                #expect(!block.plainText.isEmpty, "\(rec.conversation): \(block.id) says nothing")
                if case .other = block {} else { drawn.insert(block.type) }
            }
        }
        // every kind of the contract is drawn by the app itself: none of the recordings falls back to its sentence
        #expect(drawn == ["text", "proposal", "approval", "posted", "figure", "table", "chart", "review", "intake", "file"],
                "\(drawn)")
        for rec in try recordings() {
            for block in rec.blocks { if case .other(let id, let type, _) = block { Issue.record("\(rec.conversation): \(id) (\(type)) not drawn") } }
        }
    }

    @Test func theDrawnKindsKeepTheirFields() throws {
        let all = Dictionary(uniqueKeysWithValues: try recordings().map { ($0.conversation, $0.blocks) })
        guard case .figure(_, let cash, _)? = all["cash-fa"]?.first else { Issue.record("cash-fa: a figure first"); return }
        #expect(cash.value == 1_240_000_000)
        #expect(cash.currency == "IRR")
        #expect(cash.breakdown?.count == 2)
        let draft = all["draft-fa"]?.compactMap { b -> Proposal? in if case .proposal(_, let p) = b { return p }; return nil }.first
        #expect(draft?.token != nil)
        #expect((draft?.lines?.count ?? 0) >= 2)
        let posted = all["posted-fa"]?.compactMap { b -> Posted? in if case .posted(_, let p, _) = b { return p }; return nil }.first
        #expect(posted?.auditLogId != nil)
        #expect(posted?.canUndo == true)
        let chart = all["forecast-fa"]?.compactMap { b -> ChartData? in if case .chart(_, let c, _) = b { return c }; return nil }.first
        #expect(!(chart?.points.isEmpty ?? true))
        let review = all["review-fa"]?.compactMap { b -> Review? in if case .review(_, let r, _) = b { return r }; return nil }.first
        #expect(!(review?.title.isEmpty ?? true))
    }
}
