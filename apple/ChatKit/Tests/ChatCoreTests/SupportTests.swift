import Foundation
import Testing
@testable import ChatCore

// Support: the chat with the support desk (app/api/support.py, scenarios DC1–DC9) — the conversation as the server
// sends it, a message with what the app knew, the refused calls kept for it, only what's new asked for, the
// replies marked seen.

/// A support message as the server writes it (app/services/support_desk.py `message_dict`).
func supportMessage(_ id: String, _ author: String, at: String, text: String = "", attachments: [String] = [],
                    seen: Any = NSNull()) -> [String: Any] {
    ["id": id, "author": author, "channel": author == "desk" ? "web" : "ios", "text": text,
     "attachments": attachments, "at": at, "seen": seen]
}

/// GET /support's answer.
func supportPage(_ messages: [[String: Any]], unread: Int = 0, online: Bool = false) -> Data {
    json(["thread_id": messages.isEmpty ? NSNull() : "th-1", "messages": messages, "unread": unread,
          "desk": ["online": online], "daily_limit": 30])
}

extension ChatCoreTests {

    @Suite @MainActor struct Support {
        static let t1 = "2026-11-28T10:20:30.123456+00:00"
        static let t2 = "2026-11-28T10:21:00+00:00"                 // Python leaves out a fraction of .000000
        static let t3 = "2026-11-28T10:25:12.5+00:00"

        func model() -> SupportModel {
            SupportModel(api: signedInClient(), language: "fa")
        }

        // MARK: decoding

        @Test func theConversationDecodes() throws {
            let data = supportPage([supportMessage("m1", "user", at: Self.t1, text: "the ledger shows an error",
                                                   attachments: ["att-1"]),
                                    supportMessage("m2", "desk", at: Self.t2, text: "Fixed — it's live", seen: false)],
                                   unread: 1, online: true)
            let page = try JSONDecoder.api.decode(SupportConversation.self, from: data)
            #expect(page.threadId == "th-1")
            #expect(page.unread == 1 && page.desk?.online == true && page.dailyLimit == 30)
            #expect(page.messages.map(\.author) == ["user", "desk"])
            #expect(page.messages[0].attachments == ["att-1"] && page.messages[0].seen == nil)
            #expect(page.messages[1].fromDesk && page.messages[1].seen == false)
            #expect(page.messages[0].date != nil && page.messages[1].date != nil)          // with or without a fraction
            #expect(page.messages[0].date! < page.messages[1].date!)
            // no conversation yet
            let empty = try JSONDecoder.api.decode(SupportConversation.self, from: supportPage([]))
            #expect(empty.threadId == nil && empty.messages.isEmpty && empty.unread == 0)
        }

        // MARK: posting, with what the app knew

        @Test func aMessageGoesWithItsContextAndShows() async throws {
            Stub.reset { req, _ in
                if req.url!.path.hasSuffix("/support/messages") {
                    return (201, json(["message": supportMessage("m9", "user", at: Self.t3, text: "the ledger page errors",
                                                                 attachments: ["att-1"]),
                                       "desk": ["online": true]]), [:])
                }
                return (200, supportPage([supportMessage("m9", "user", at: Self.t3, text: "the ledger page errors")],
                                         online: true), [:])
            }
            let support = model()
            support.page = "chat/th-42"
            support.screen = "1000x720"
            support.draft = "  the ledger page errors "
            await support.send()
            let post = try #require(Stub.seen.first { $0.method == "POST" })
            #expect(post.path == "/api/mobile/v1/support/messages")
            #expect(post.body["text"] as? String == "the ledger page errors")
            #expect(post.body["attachment_ids"] as? [String] == [])
            let context = try #require(post.body["context"] as? [String: Any])
            #expect(context["page"] as? String == "chat/th-42")
            #expect(context["platform"] as? String == "ios")
            #expect(context["app_version"] as? String == "1.0.0")
            #expect(context["language"] as? String == "fa")
            #expect(context["screen"] as? String == "1000x720")
            #expect((context["user_agent"] as? String)?.hasPrefix("AccountingChat/1.0.0 (macos; ") == true)
            #expect((context["errors"] as? [Any])?.isEmpty == true)
            #expect(post.headers["Authorization"] == "Bearer a")
            #expect(post.headers["X-App-Version"] == "1.0.0")
            #expect(post.headers["X-UI-Language"] == "fa")                         // a refusal in the person's words
            #expect(support.draft.isEmpty)
            #expect(support.messages.map(\.id) == ["m9"])                          // once, though fetched again
            #expect(support.online)
            #expect(Stub.seen.last?.method == "GET")                               // then what's new
        }

        @Test func pastTheDaysLimitThePersonIsToldInTheirWords() async throws {
            let words = "امروز ۳۰ پیام به پشتیبانی فرستاده‌اید. همه به دستش رسیده؛ فردا دوباره بنویسید."
            Stub.reset { _, _ in (429, json(["detail": words]), ["X-Error-Code": "support_limit"]) }
            let support = model()
            support.draft = "one more"
            await support.send()
            #expect(support.error == words)
            #expect(support.draft == "one more")                                   // kept, to send tomorrow
            #expect(support.messages.isEmpty)
            Stub.reset { _, _ in (502, Data(), [:]) }
            await support.send()
            #expect(support.notice == .sendFailed)
            #expect(L10n("fa").notice(.sendFailed) == "ارسال نشد — دوباره امتحان کنید.")
        }

        @Test func aScreenshotIsUploadedThenItsIDGoesWithTheMessage() async throws {
            var sent: [String: Any] = [:]
            Stub.resetRaw { req, raw in
                if req.url!.path.hasSuffix("/support/attachments") {
                    return (201, json(["id": "att-7", "file_name": "screenshot.png", "content_type": "image/png",
                                       "size_bytes": raw.count]), [:])
                }
                if req.url!.path.hasSuffix("/support/messages") {
                    sent = (try? JSONSerialization.jsonObject(with: raw)) as? [String: Any] ?? [:]
                    return (201, json(["message": supportMessage("m1", "user", at: Self.t1, attachments: ["att-7"])]), [:])
                }
                return (200, supportPage([]), [:])
            }
            let support = model()
            let png = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + Data(repeating: 7, count: 64)
            await support.attach(png, name: "screenshot.png", mime: "image/png")
            let upload = try #require(Stub.seen.first)
            #expect(upload.path == "/api/mobile/v1/support/attachments")
            #expect(upload.headers["Content-Type"]?.hasPrefix("multipart/form-data; boundary=") == true)
            let form = String(decoding: upload.raw, as: UTF8.self)
            #expect(form.contains("name=\"file\"; filename=\"screenshot.png\""))
            #expect(form.contains("Content-Type: image/png"))
            #expect(support.attachments.map(\.id) == ["att-7"])
            #expect(support.canSend)                                               // a screenshot alone may go
            await support.send()
            #expect(sent["attachment_ids"] as? [String] == ["att-7"])
            #expect(support.attachments.isEmpty)
            #expect(support.messages.first?.attachments == ["att-7"])
        }

        @Test func aFileOverTwentyMegabytesIsntSent() async throws {
            Stub.reset { _, _ in (201, json(["id": "x"]), [:]) }
            let support = model()
            await support.attach(Data(count: SupportModel.maxFileBytes + 1), name: "big.pdf", mime: "application/pdf")
            #expect(support.notice == .tooLarge)
            #expect(Stub.seen.isEmpty)
            Stub.reset { _, _ in (422, json(["detail": "یک تصویر یا PDF پیوست کنید."]), [:]) }
            await support.attach(Data("a,b".utf8), name: "s.csv", mime: "text/csv")
            #expect(support.error == "یک تصویر یا PDF پیوست کنید.")
            #expect(support.attachments.isEmpty)
        }

        // MARK: the refused calls, kept for support

        @Test func theLastTenRefusedCallsAreKeptWithTheirRequestIDs() async throws {
            Stub.reset { req, _ in
                let n = Stub.seen.count
                if req.url!.path.hasSuffix("/support") { return (500, Data(), ["x-request-id": "support-own"]) }
                return (n % 2 == 0 ? 500 : 404, json(["detail": "no"]), ["x-request-id": "req-\(n)"])
            }
            let api = signedInClient()
            for _ in 0..<12 { _ = try? await api.threads() }
            _ = try? await api.supportConversation()                               // support's own: not kept
            let kept = await api.recentFailures()
            #expect(kept.count == APIClient.keptFailures)
            #expect(kept.first?.requestID == "req-3" && kept.last?.requestID == "req-12")   // the oldest two dropped
            #expect(kept.allSatisfy { $0.method == "GET" && $0.path == "/api/mobile/v1/threads" })
            #expect(kept.last?.status == 500 && kept[kept.count - 2].status == 404)
            #expect(kept.allSatisfy { ServerTime.date($0.at) != nil })
            // and they go with the next message
            Stub.reset { req, _ in
                req.httpMethod == "POST" ? (201, json(["message": supportMessage("m1", "user", at: Self.t1, text: "x")]), [:])
                                         : (200, supportPage([]), [:])
            }
            _ = try await api.sendSupport(text: "x", context: SupportContext(page: "chat", language: "en"))
            let errors = try #require((Stub.seen.first?.body["context"] as? [String: Any])?["errors"] as? [[String: Any]])
            #expect(errors.count == 10)
            #expect(errors.last?["request_id"] as? String == "req-12")
            #expect(errors.last?["status"] as? Int == 500)
            #expect(errors.last?["path"] as? String == "/api/mobile/v1/threads")
            #expect(errors.last?["method"] as? String == "GET")
            #expect(errors.last?["at"] is String)
        }

        @Test func anExpiredTokenRenewedIsntAFailure() async throws {
            var expired = true
            Stub.reset { req, _ in
                if req.url!.path.hasSuffix("/auth/refresh") { return (200, fixture("login"), [:]) }
                if expired { expired = false; return (401, json(["detail": "expired"]), [:]) }
                return (200, fixture("threads"), [:])
            }
            let api = signedInClient()
            _ = try await api.threads()
            #expect(await api.recentFailures().isEmpty)
        }

        // MARK: only what's new, the replies seen

        @Test func onlyWhatCameAfterTheNewestIsAskedFor() async throws {
            var answers = [supportPage([supportMessage("m1", "user", at: Self.t1, text: "hi"),
                                        supportMessage("m2", "desk", at: Self.t2, text: "hello", seen: false)],
                                       unread: 1),
                           supportPage([supportMessage("m3", "desk", at: Self.t3, text: "and fixed", seen: false)],
                                       unread: 2),
                           supportPage([], unread: 2)]
            Stub.reset { _, _ in (200, answers.isEmpty ? supportPage([]) : answers.removeFirst(), [:]) }
            let support = model()
            await support.refresh()
            #expect(Stub.seen[0].query["after"] == nil)
            #expect(support.messages.map(\.id) == ["m1", "m2"])
            #expect(support.unread == 1)                                           // the badge, while closed
            await support.refresh()
            #expect(Stub.seen[1].query["after"] == Self.t2)                        // the server's own time, as it came
            #expect(Stub.seen[1].rawQuery == "after=2026-11-28T10:21:00%2B00:00")  // its "+" not taken for a space
            #expect(support.messages.map(\.id) == ["m1", "m2", "m3"])
            #expect(support.unread == 2)
            await support.refresh()
            #expect(Stub.seen[2].query["after"] == Self.t3)
            #expect(support.messages.count == 3)
            #expect(!Stub.seen.contains { $0.path.hasSuffix("/support/seen") })   // not read while closed
        }

        @Test func aReplyWrittenWhileSendingIsntSkipped() {
            let support = model()
            support.merge([supportMessage("m1", "user", at: Self.t1)].map(Self.decode))
            support.merge([supportMessage("m3", "user", at: Self.t3)].map(Self.decode))  // just sent
            support.merge([supportMessage("m2", "desk", at: Self.t2), supportMessage("m3", "user", at: Self.t3)]
                .map(Self.decode))
            #expect(support.messages.map(\.id) == ["m1", "m2", "m3"])              // in the order written, each once
        }

        @Test func openTheRepliesAreMarkedSeenAndWhatsNewComesEveryFewSeconds() async throws {
            var unread = 2
            Stub.reset { req, _ in
                if req.url!.path.hasSuffix("/support/seen") { defer { unread = 0 }; return (200, json(["seen": unread]), [:]) }
                return (200, supportPage([supportMessage("m1", "desk", at: Self.t1, text: "hello", seen: unread == 0)],
                                         unread: unread, online: true), [:])
            }
            let support = model()
            await support.refresh()
            #expect(support.unread == 2)                                           // the badge
            let watching = Task { await support.watchOpen(every: .milliseconds(20)) }
            try await Task.sleep(for: .milliseconds(250))
            #expect(support.isOpen)
            #expect(support.unread == 0)
            #expect(support.online)
            let seen = Stub.seen.filter { $0.path == "/api/mobile/v1/support/seen" }
            #expect(seen.count == 1 && seen.first?.method == "POST")               // once: nothing unread after
            let gets = Stub.seen.filter { $0.method == "GET" }
            #expect(gets.count >= 4)
            #expect(gets[1].query["after"] == nil)                                 // opened: the whole page
            #expect(gets.dropFirst(2).allSatisfy { $0.query["after"] == Self.t1 }) // then only what's new
            watching.cancel()
            await watching.value
            #expect(!support.isOpen)
            // a request already on its way when it closed is recorded a moment later (CI's runner was slower than
            // this Mac: 8 against 7); let those land, then nothing more may come
            try await Task.sleep(for: .milliseconds(100))
            let asked = Stub.seen.count
            try await Task.sleep(for: .milliseconds(120))
            #expect(Stub.seen.count == asked)                                      // closed: it stops asking
        }

        @Test func aSessionEndedWhilePollingSignsOut() async throws {
            Stub.reset { req, _ in (401, json(["detail": "Sign in again."]), [:]) }
            let support = SupportModel(api: APIClient(baseURL: server, tokens: MemoryTokenStore(Tokens(access: "a", refresh: "r")),
                                                      device: device, session: Stub.session))
            var ended = false
            support.onSessionEnded = { ended = true }
            await support.refresh()
            #expect(ended)
        }

        @Test func signedInThereIsSupportAndSignedOutThereIsnt() async throws {
            Stub.reset { req, _ in req.url!.path.hasSuffix("/auth/login") ? (200, fixture("login"), [:]) : (204, Data(), [:]) }
            let app = AppModel(defaults: UserDefaults(suiteName: "chatcore-tests-\(UUID())")!, session: Stub.session,
                               tokens: { _ in MemoryTokenStore() })
            app.serverAddress = "https://books.example.com"
            await app.signIn(username: "admin", password: "pw")
            #expect(app.support != nil)
            #expect(app.support?.language == app.l10n.language)
            await app.signOut()
            #expect(app.support == nil)
        }

        @Test func theBadgeCountsInTheReadersDigits() {
            #expect(L10n("fa").count(3) == "۳")
            #expect(L10n("en").count(12, cap: 9) == "9+")
            #expect(L10n("fa").count(12, cap: 9) == "۹+")
            #expect(L10n("fa")[.support] == "پشتیبانی" && L10n("ar")[.support] == "الدعم")
            #expect(L10n("es")[.support] == "Soporte" && L10n("en")[.support] == "Support")
        }

        static func decode(_ object: [String: Any]) -> SupportMessage {
            try! JSONDecoder.api.decode(SupportMessage.self, from: json(object))
        }
    }
}
