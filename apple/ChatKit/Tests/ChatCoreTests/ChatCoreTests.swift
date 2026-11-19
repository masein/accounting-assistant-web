import XCTest
@testable import ChatCore

/// Answers recorded from the server (Fixtures/), and a stand-in for the network.
final class Stub: URLProtocol {
    struct Seen { let method: String; let path: String; let headers: [String: String]; let body: [String: Any] }
    nonisolated(unsafe) static var seen: [Seen] = []
    nonisolated(unsafe) static var handler: ((URLRequest, [String: Any]) -> (Int, Data, [String: String])) = { _, _ in (404, Data(), [:]) }

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
        Stub.seen.append(Seen(method: request.httpMethod ?? "GET", path: request.url?.path ?? "",
                              headers: request.allHTTPHeaderFields ?? [:], body: body))
        let (status, data, headers) = Stub.handler(request, body)
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
}

func fixture(_ name: String) -> Data {
    let url = Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")!
    return try! Data(contentsOf: url)
}

func json(_ object: Any) -> Data { try! JSONSerialization.data(withJSONObject: object) }

let server = URL(string: "https://books.example.com")!
let device = DeviceInfo(name: "Test Mac", platform: "macos", appVersion: "1.0.0", language: "en")

final class DecodingTests: XCTestCase {
    func testAnAnswerWithACardDecodes() throws {
        let reply = try JSONDecoder.api.decode(ChatReply.self, from: fixture("chat-card"))
        XCTAssertEqual(reply.blocks.count, 2)
        guard case .proposal(_, let p) = reply.blocks[0] else { return XCTFail("a card first") }
        XCTAssertEqual(p.tool, "propose_create_transaction")
        XCTAssertEqual(p.amount, Money(value: 250000, currency: "IRR"))
        XCTAssertEqual(p.date?.display, "19 Nov 2026")
        XCTAssertEqual(p.lines?.map(\.account), ["6112", "1110"])
        XCTAssertEqual(p.lines?.first?.debit, 250000)
        XCTAssertTrue(p.canConfirm && p.canCancel && p.isPending)
        guard case .text(_, let text) = reply.blocks[1] else { return XCTFail("then the text") }
        XCTAssertTrue(text.contains("Confirm"))
    }

    func testAThreadsHistoryDecodes() throws {
        let messages = try JSONDecoder.api.decode([ChatMessage].self, from: fixture("messages"))
        XCTAssertEqual(messages.map(\.role), ["user", "assistant", "user", "assistant"])
        XCTAssertEqual(messages[0].text, "hi")
        XCTAssertEqual(messages[0].clientMessageId, "m1")
        guard case .proposal(_, let p)? = messages[3].blocks?.first else { return XCTFail("the card comes back") }
        XCTAssertEqual(p.state, "pending")
        let threads = try JSONDecoder.api.decode([ChatThread].self, from: fixture("threads"))
        XCTAssertEqual(threads.first?.title, "hi")
    }

    func testABlockTheAppDoesNotDrawShowsItsText() throws {
        let data = json(["type": "cash_overview", "id": "c1", "fallback_text": "Cash: £1,200", "rows": [1, 2]])
        let block = try JSONDecoder.api.decode(Block.self, from: data)
        XCTAssertEqual(block, .other(id: "c1", type: "cash_overview", fallback: "Cash: £1,200"))
        XCTAssertEqual(block.plainText, "Cash: £1,200")
    }

    func testABlockRoundTrips() throws {
        let reply = try JSONDecoder.api.decode(ChatReply.self, from: fixture("chat-card"))
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let again = try JSONDecoder.api.decode([Block].self, from: encoder.encode(reply.blocks))
        XCTAssertEqual(again, reply.blocks)
    }
}

final class APIClientTests: XCTestCase {
    override func setUp() { Stub.seen = [] }

    func testSigningInKeepsTheTokensAndSaysWhichDevice() async throws {
        Stub.handler = { _, _ in (200, fixture("login"), [:]) }
        let tokens = MemoryTokenStore()
        let api = APIClient(baseURL: server, tokens: tokens, device: device, session: Stub.session)
        guard case .signedIn(let s) = try await api.signIn(username: "admin", password: "pw") else { return XCTFail() }
        XCTAssertEqual(s.user.username, "admin")
        XCTAssertEqual(s.company?.name, "Default")
        XCTAssertEqual(tokens.load(), Tokens(access: "access-1", refresh: "refresh-1"))
        let req = try XCTUnwrap(Stub.seen.first)
        XCTAssertEqual(req.path, "/api/mobile/v1/auth/login")
        XCTAssertEqual(req.body["platform"] as? String, "macos")
        XCTAssertEqual(req.body["device_name"] as? String, "Test Mac")
        XCTAssertEqual(req.headers["X-App-Version"], "1.0.0")
        XCTAssertNil(req.headers["Authorization"])
    }

    func testTwoFactorAsksForTheCode() async throws {
        Stub.handler = { req, _ in
            req.url!.path.hasSuffix("/auth/login")
                ? (200, json(["ok": false, "two_factor_required": true, "challenge": "ch-1"]), [:])
                : (200, fixture("login"), [:])
        }
        let tokens = MemoryTokenStore()
        let api = APIClient(baseURL: server, tokens: tokens, device: device, session: Stub.session)
        let first = try await api.signIn(username: "admin", password: "pw")
        XCTAssertEqual(first, .twoFactor(challenge: "ch-1"))
        XCTAssertNil(tokens.load())
        _ = try await api.confirmTwoFactor(challenge: "ch-1", code: "123456")
        XCTAssertEqual(Stub.seen.last?.body["challenge"] as? String, "ch-1")
        XCTAssertEqual(Stub.seen.last?.body["code"] as? String, "123456")
        XCTAssertNotNil(tokens.load())
    }

    func testAnExpiredAccessTokenIsRenewedOnceAndTheRequestRepeated() async throws {
        Stub.handler = { req, body in
            let auth = req.value(forHTTPHeaderField: "Authorization")
            if req.url!.path.hasSuffix("/auth/refresh") {
                XCTAssertEqual(body["refresh_token"] as? String, "old-refresh")
                var signIn = try! JSONSerialization.jsonObject(with: fixture("login")) as! [String: Any]
                signIn["access_token"] = "new-access"; signIn["refresh_token"] = "new-refresh"
                return (200, json(signIn), [:])
            }
            return auth == "Bearer new-access" ? (200, fixture("threads"), [:]) : (401, json(["detail": "expired"]), [:])
        }
        let tokens = MemoryTokenStore(Tokens(access: "old-access", refresh: "old-refresh"))
        let api = APIClient(baseURL: server, tokens: tokens, device: device, session: Stub.session)
        let threads = try await api.threads()
        XCTAssertEqual(threads.count, 3)
        XCTAssertEqual(tokens.load(), Tokens(access: "new-access", refresh: "new-refresh"))
        XCTAssertEqual(Stub.seen.map(\.path), ["/api/mobile/v1/threads", "/api/mobile/v1/auth/refresh", "/api/mobile/v1/threads"])
    }

    func testARefusedRefreshEndsTheSession() async throws {
        Stub.handler = { req, _ in
            req.url!.path.hasSuffix("/auth/refresh")
                ? (401, json(["detail": "Your session on this phone has ended. Sign in again."]), ["X-Error-Code": "session_ended"])
                : (401, json(["detail": "expired"]), [:])
        }
        let tokens = MemoryTokenStore(Tokens(access: "a", refresh: "r"))
        let api = APIClient(baseURL: server, tokens: tokens, device: device, session: Stub.session)
        do {
            _ = try await api.threads()
            XCTFail("should end the session")
        } catch let e as APIError {
            XCTAssertTrue(e.endsSession)
        }
        XCTAssertNil(tokens.load())
    }

    func testSendingNamesTheThreadAndTheMessagesOwnId() async throws {
        Stub.handler = { _, _ in (200, fixture("chat-card"), [:]) }
        let api = APIClient(baseURL: server, tokens: MemoryTokenStore(Tokens(access: "a", refresh: "r")), device: device,
                            session: Stub.session)
        let reply = try await api.send(message: "paid 20 for lunch", thread: "t-9", clientMessageID: "c-1")
        XCTAssertEqual(reply.threadId, "1db0f7f9-3252-4422-aa7e-f30021811755")
        let req = try XCTUnwrap(Stub.seen.first)
        XCTAssertEqual(req.method, "POST")
        XCTAssertEqual(req.headers["Authorization"], "Bearer a")
        XCTAssertEqual(req.body["message"] as? String, "paid 20 for lunch")
        XCTAssertEqual(req.body["thread_id"] as? String, "t-9")
        XCTAssertEqual(req.body["client_message_id"] as? String, "c-1")
    }

    func testTheServersWordsAreTheError() async throws {
        Stub.handler = { _, _ in (409, json(["detail": "Still working on this message."]), ["X-Error-Code": "turn_in_progress"]) }
        let api = APIClient(baseURL: server, tokens: MemoryTokenStore(Tokens(access: "a", refresh: "r")), device: device,
                            session: Stub.session)
        do {
            _ = try await api.send(message: "x", thread: nil, clientMessageID: "c")
            XCTFail()
        } catch let e as APIError {
            XCTAssertEqual(e, APIError(status: 409, detail: "Still working on this message.", code: "turn_in_progress"))
        }
    }
}

@MainActor
final class ChatModelTests: XCTestCase {
    override func setUp() async throws { Stub.seen = [] }

    private func model() -> ChatModel {
        ChatModel(api: APIClient(baseURL: server, tokens: MemoryTokenStore(Tokens(access: "a", refresh: "r")),
                                 device: device, session: Stub.session))
    }

    func testSendingShowsTheMessageThenTheAnswerAndConfirmMarksTheCard() async throws {
        Stub.handler = { req, _ in
            switch req.url!.path {
            case let p where p.hasSuffix("/chat"): return (200, fixture("chat-card"), [:])
            case let p where p.hasSuffix("/confirm"): return (200, fixture("confirm"), [:])
            default: return (200, fixture("threads"), [:])
            }
        }
        let chat = model()
        chat.draft = "  paid 250000 for lunch "
        XCTAssertTrue(chat.canSend)
        await chat.send()
        XCTAssertEqual(chat.draft, "")
        XCTAssertEqual(chat.messages.count, 2)
        XCTAssertEqual(chat.messages[0].text, "paid 250000 for lunch")
        XCTAssertEqual(chat.thread, "1db0f7f9-3252-4422-aa7e-f30021811755")
        XCTAssertEqual(chat.threads.count, 3)                    // a new conversation: the list is fetched again
        guard case .proposal(_, let p)? = chat.messages[1].blocks?.first else { return XCTFail() }
        await chat.confirm(p.token)
        guard case .proposal(_, let after)? = chat.messages[1].blocks?.first else { return XCTFail() }
        XCTAssertEqual(after.state, "posted")
        XCTAssertFalse(after.canConfirm)
        XCTAssertTrue(Stub.seen.contains { $0.path == "/api/mobile/v1/proposals/\(p.token)/confirm" })
    }

    func testAFailedSendCanBeRetriedWithTheSameId() async throws {
        var fail = true
        Stub.handler = { req, _ in
            if req.url!.path.hasSuffix("/chat") {
                if fail { fail = false; return (502, Data("bad gateway".utf8), [:]) }
                return (200, fixture("chat-hi"), [:])
            }
            return (200, fixture("threads"), [:])
        }
        let chat = model()
        chat.draft = "hi"
        await chat.send()
        XCTAssertNotNil(chat.error)
        XCTAssertTrue(chat.canRetry)
        await chat.retry()
        XCTAssertNil(chat.error)
        let ids = Stub.seen.filter { $0.path.hasSuffix("/chat") }.compactMap { $0.body["client_message_id"] as? String }
        XCTAssertEqual(ids.count, 2)
        XCTAssertEqual(ids[0], ids[1])                           // the server answers a repeat once
        XCTAssertEqual(chat.messages.filter(\.isUser).count, 1)
    }

    func testAnExpiredCardSaysSo() async throws {
        Stub.handler = { req, _ in
            req.url!.path.hasSuffix("/chat") ? (200, fixture("chat-card"), [:])
                : req.url!.path.hasSuffix("/cancel") ? (410, json(["detail": "expired"]), [:]) : (200, fixture("threads"), [:])
        }
        let chat = model()
        chat.draft = "x"
        await chat.send()
        guard case .proposal(_, let p)? = chat.messages.last?.blocks?.first else { return XCTFail() }
        await chat.cancel(p.token)
        guard case .proposal(_, let after)? = chat.messages.last?.blocks?.first else { return XCTFail() }
        XCTAssertEqual(after.state, "expired")
    }
}

@MainActor
final class AppModelTests: XCTestCase {
    func testServerAddressesAreHttpsExceptThisMachine() {
        let app = AppModel(defaults: UserDefaults(suiteName: "chatcore-tests-\(UUID())")!, tokens: { _ in MemoryTokenStore() })
        XCTAssertEqual(app.serverURL("accounting.example.com/")?.absoluteString, "https://accounting.example.com")
        XCTAssertEqual(app.serverURL(" https://a.example.com ")?.absoluteString, "https://a.example.com")
        XCTAssertEqual(app.serverURL("http://localhost:18000")?.absoluteString, "http://localhost:18000")
        XCTAssertNil(app.serverURL("http://accounting.example.com"))      // never a password over plain http
        XCTAssertNil(app.serverURL(""))
        XCTAssertEqual(app.serverAddress, AppModel.defaultServer)
    }

    func testSigningInAndOut() async throws {
        Stub.handler = { req, _ in req.url!.path.hasSuffix("/auth/login") ? (200, fixture("login"), [:]) : (204, Data(), [:]) }
        let tokens = MemoryTokenStore()
        let app = AppModel(defaults: UserDefaults(suiteName: "chatcore-tests-\(UUID())")!, session: Stub.session,
                           tokens: { _ in tokens })
        app.serverAddress = "https://books.example.com"
        await app.signIn(username: "admin", password: "pw")
        XCTAssertEqual(app.phase, .signedIn)
        XCTAssertNotNil(app.chat)
        XCTAssertEqual(app.user?.username, "admin")
        await app.signOut()
        XCTAssertEqual(app.phase, .signedOut)
        XCTAssertNil(tokens.load())
        XCTAssertTrue(Stub.seen.contains { $0.method == "DELETE" && $0.path == "/api/mobile/v1/session" })
    }
}

final class L10nTests: XCTestCase {
    func testEveryWordInEveryLanguage() {
        for lang in L10n.supported {
            let table = L10n.table[lang] ?? [:]
            for key in L10n.Key.allCases {
                XCTAssertFalse((table[key] ?? "").isEmpty, "\(lang): \(key)")
            }
        }
        XCTAssertTrue(L10n("fa-IR").isRTL)
        XCTAssertTrue(L10n("ar").isRTL)
        XCTAssertFalse(L10n("en-GB").isRTL)
        XCTAssertEqual(L10n("de").language, "en")
        XCTAssertEqual(L10n("fa")(.signOutConfirm, "Default"), "از Default روی این دستگاه خارج می‌شوید؟")
    }
}

/// The contract every client of the replies keeps (docs/contracts/README.md): the server's recorded conversations,
/// read straight from the repository so this app breaks when they change, as the Android app's ContractTest does.
final class ContractTests: XCTestCase {
    static let conversations: URL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()   // Tests/ChatCoreTests → ChatKit
        .deletingLastPathComponent().deletingLastPathComponent()                               // → apple → the repository
        .appendingPathComponent("docs/contracts/conversations")

    struct Recording: Decodable { let conversation: String; let blocks: [Block] }

    func recordings() throws -> [Recording] {
        let files = try FileManager.default.contentsOfDirectory(at: Self.conversations, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
        XCTAssertGreaterThanOrEqual(files.count, 18, "the recordings moved?")
        return try files.map { try JSONDecoder.api.decode(Recording.self, from: Data(contentsOf: $0)) }
    }

    func testEveryRecordedBlockReadsAndSaysSomething() throws {
        var drawn = Set<String>()
        for rec in try recordings() {
            XCTAssertFalse(rec.blocks.isEmpty, rec.conversation)
            for block in rec.blocks {
                XCTAssertFalse(block.plainText.isEmpty, "\(rec.conversation): \(block.id) says nothing")
                if case .other = block {} else { drawn.insert(block.type) }
            }
        }
        // the kinds this app draws itself; the rest (tables, files, approvals, intake) show their sentence
        XCTAssertTrue(drawn.isSuperset(of: ["text", "proposal", "posted", "figure", "chart", "review"]), "\(drawn)")
    }

    func testTheDrawnKindsKeepTheirFields() throws {
        let all = Dictionary(uniqueKeysWithValues: try recordings().map { ($0.conversation, $0.blocks) })
        guard case .figure(_, let cash, _)? = all["cash-fa"]?.first else { return XCTFail("cash-fa: a figure first") }
        XCTAssertEqual(cash.value, 1_240_000_000)
        XCTAssertEqual(cash.currency, "IRR")
        XCTAssertEqual(cash.breakdown?.count, 2)
        let draft = all["draft-fa"]?.compactMap { b -> Proposal? in if case .proposal(_, let p) = b { return p }; return nil }.first
        XCTAssertNotNil(draft?.token)
        XCTAssertGreaterThanOrEqual(draft?.lines?.count ?? 0, 2)
        let posted = all["posted-fa"]?.compactMap { b -> Posted? in if case .posted(_, let p, _) = b { return p }; return nil }.first
        XCTAssertNotNil(posted?.auditLogId)
        XCTAssertTrue(posted?.canUndo ?? false)
        let chart = all["forecast-fa"]?.compactMap { b -> ChartData? in if case .chart(_, let c, _) = b { return c }; return nil }.first
        XCTAssertFalse(chart?.points.isEmpty ?? true)
        let review = all["review-fa"]?.compactMap { b -> Review? in if case .review(_, let r, _) = b { return r }; return nil }.first
        XCTAssertFalse(review?.title.isEmpty ?? true)
    }
}
