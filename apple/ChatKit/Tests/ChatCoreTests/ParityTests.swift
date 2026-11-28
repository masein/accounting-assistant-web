import CryptoKit
import Foundation
import ImageIO
import Testing
import UniformTypeIdentifiers
@testable import ChatCore

// What the app does like the Android app (review 2026-11-28, scenarios CY1–CY12): a card's state as it stands when
// a conversation is opened again, every block of the contract drawn, older pages, files both ways, the streamed
// reply, the voice note, the update screen.

/// A recorded conversation's blocks as the server sent them (docs/contracts/conversations/).
func recordedBlocks(_ name: String) throws -> [[String: Any]] {
    let url = Contract.conversations.appendingPathComponent("\(name).json")
    let object = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any]
    return object?["blocks"] as? [[String: Any]] ?? []
}

func block(_ object: [String: Any]) throws -> Block {
    try JSONDecoder.api.decode(Block.self, from: json(object))
}

/// A thread's history card, as GET /threads/{id}/messages draws it again (app/api/mobile_chat.py).
func historyCard(state: String, approvalStatus: String? = nil, posted: [String: Any]? = nil,
                 type: String = "proposal") -> [String: Any] {
    var b: [String: Any] = ["type": type, "id": "\(type):tok-1", "token": "tok-1", "tool": "propose_create_transaction",
                            "title": "Lunch", "amount": ["value": 20, "currency": "GBP"], "actions": ["confirm", "cancel"],
                            "needs_approval": false, "state": state, "fallback_text": "Lunch"]
    b["approval_status"] = approvalStatus ?? NSNull()
    if let posted { b["posted"] = posted }
    return b
}

extension ChatCoreTests {

    // MARK: CY1–CY3: a card's state, opened again

    @Suite struct History {
        @Test func anUndoneEntrySaysUndoneWhenTheConversationIsOpenedAgain() throws {
            let card = try block(historyCard(state: "executed", posted: ["voucher": "V-12", "undone": true,
                                                                       "date": ["iso": "2026-11-20", "display": "20 Nov 2026"]]))
            guard case .proposal(_, let p) = card else { Issue.record("a card"); return }
            #expect(p.phase == .undone)
            #expect(p.posted?.voucher == "V-12")
            #expect(!p.canConfirm && !p.canCancel)
            let kept = try block(historyCard(state: "executed", posted: ["voucher": "V-13", "undone": false, "date": NSNull()]))
            #expect(kept.card?.phase == .posted)
            #expect(kept.card?.posted?.voucher == "V-13")
        }

        @Test func everyStateTheServerGivesHasItsPhase() throws {
            let cases: [(String, String?, String, Proposal.Phase)] = [
                ("pending", nil, "proposal", .draft),
                ("pending", "requested", "proposal", .waiting),         // the asker's card, sent for approval
                ("pending", "requested", "approval", .draft),           // the approver's: Approve / Reject
                ("waiting_for_approval", nil, "proposal", .waiting),    // Confirm's answer above the threshold
                ("executed", "approved", "proposal", .posted),
                ("posted", nil, "proposal", .posted),
                ("cancelled", "rejected", "proposal", .rejected),
                ("cancelled", "withdrawn", "proposal", .cancelled),
                ("cancelled", nil, "proposal", .cancelled),
                ("rejected", nil, "approval", .rejected),
                ("expired", nil, "proposal", .expired),
                ("replaced", nil, "proposal", .replaced),
                ("undone", nil, "proposal", .undone),
                ("something_new", nil, "proposal", .closed),
            ]
            for (state, approval, type, phase) in cases {
                var b = historyCard(state: state, approvalStatus: approval, type: type)
                if type == "approval" { b["requested_by"] = "maryam"; b["mine"] = false; b["actions"] = ["approve", "reject"] }
                #expect(try block(b).card?.phase == phase, "\(state) / \(approval ?? "-") on \(type)")
            }
        }

        @Test func aHistoryCardWithFieldsOfAnotherShapeStillReads() throws {
            var b = historyCard(state: "executed")
            b["posted"] = true                                   // an older server's flag, not a seal
            b["amount"] = ["value": NSNull(), "currency": "GBP"]
            guard case .proposal(_, let p) = try block(b) else { Issue.record("still a card"); return }
            #expect(p.posted == nil && p.amount == nil && p.phase == .posted)
        }

        @Test func aFigureWithANamelessPartIsStillAFigure() throws {
            let f = try block(["type": "figure", "id": "f1", "label": "Cash and bank", "value": 1200, "currency": "GBP",
                               "breakdown": [["label": NSNull(), "value": 200], ["label": "Bank", "value": 1000]],
                               "fallback_text": "Cash and bank: 1,200 GBP"])
            guard case .figure(_, let figure, _) = f else { Issue.record("a figure, not its sentence"); return }
            #expect(figure.breakdown?.map(\.label) == [nil, "Bank"])
        }
    }

    // MARK: CY4–CY6: tables, approvals, documents from the contract recordings

    @Suite struct ContractKinds {
        @Test func tablesReadFromTheRecordings() throws {
            guard case .table(_, let spending, _)? = try recordedBlocks("spending-fa").first.map(block) else {
                Issue.record("spending: a table"); return
            }
            #expect(spending.kind == "spending")
            #expect(spending.rows.map(\.label) == ["اجاره", "سفر و ایاب‌وذهاب"])
            #expect(spending.rows.first?.value == 80_000_000)
            #expect(spending.currencies == ["IRR"])
            guard case .table(_, let budgets, _)? = try recordedBlocks("budgets-fa").first.map(block) else { Issue.record(); return }
            #expect(budgets.rows.map(\.usedPct) == [100, 75])
            #expect(budgets.rows.first?.limit == 80_000_000)
            #expect(budgets.rows.map(\.isOver) == [true, false])        // the server says "over" at 100%
            #expect(budgets.currencies.isEmpty)
            guard case .table(_, let invoices, _)? = try recordedBlocks("invoices-en").first.map(block) else { Issue.record(); return }
            #expect(invoices.rows.first?.dueDate == "2026-10-01")
            #expect(invoices.rows.first?.sub == "INV-1042")
            #expect(invoices.count == 2)
            let pay = try recordedBlocks("pay-history-fa").map(block)
            guard case .table(_, let history, _)? = pay.first else { Issue.record(); return }
            #expect(history.title == "Sara Ahmadi")
            guard case .file(_, let payslip, _)? = pay.dropFirst().first else { Issue.record("then the payslip"); return }
            #expect(payslip.path.hasPrefix("/api/mobile/v1/documents/payslips/"))
        }

        @Test func aHoldingSaysWhatItGained() throws {
            let t = try block(["type": "table", "id": "h", "kind": "holdings", "fallback_text": "Gold 1,200",
                               "rows": [["label": "Gold", "sub": "10 g", "value": 1200, "currency": "GBP", "gain": 150],
                                        ["label": NSNull(), "value": 7.5, "currency": "USD"]]])
            guard case .table(_, let holdings, _) = t else { Issue.record(); return }
            #expect(holdings.rows.map(\.gain) == [150, nil])
            #expect(holdings.rows.last?.value == Decimal(string: "7.5"))
            #expect(holdings.currencies == ["GBP", "USD"])
        }

        @Test func anApprovalCardOffersApproveAndReject() throws {
            guard case .approval(_, let a)? = try recordedBlocks("approval-fa").first.map(block) else {
                Issue.record("approval: its own card"); return
            }
            #expect(a.requestedBy == "maryam" && a.mine == false)
            #expect(a.isApproval && a.phase == .draft)
            #expect(a.canApprove && a.canReject && !a.canWithdraw)
            #expect(!a.canConfirm && !a.canCancel)
            var mine = try recordedBlocks("approval-fa")[0]
            mine["mine"] = true
            mine["actions"] = ["cancel"]
            let own = try block(mine).card
            #expect(own?.canWithdraw == true && own?.canApprove == false)
        }

        @Test func documentsAreTheServersOwnOnly() throws {
            guard case .file(_, let invoice, let said)? = try recordedBlocks("invoice-pdf-en").first.map(block) else {
                Issue.record("a document"); return
            }
            #expect(invoice.name == "invoice-INV-1042.pdf" && invoice.mime == "application/pdf")
            #expect(invoice.isPhoneDocument && said == "Invoice INV-1042 (PDF)")
            for path in ["https://evil.example.com/x.pdf", "/api/mobile/v1/../../etc/passwd", "/static/a.pdf"] {
                let b = try block(["type": "file", "id": "f", "name": "x.pdf", "mime": "application/pdf", "path": path,
                                   "fallback_text": "x"])
                #expect(b == .other(id: "f", type: "file", fallback: "x"), "\(path)")
            }
            guard case .posted(_, let posted, _)? = try recordedBlocks("posted-fa").first.map(block) else { Issue.record(); return }
            #expect(posted.file?.name == "invoice-INV-1042.pdf")
            #expect(FileRef(name: "payslip Sara/../x.pdf", mime: nil, path: "/api/mobile/v1/x").safeName == "payslip_Sara_.._x.pdf")
        }

        @Test func newKindsRoundTrip() throws {
            let blocks = try ["approval-fa", "spending-fa", "invoice-pdf-en", "posted-fa"].flatMap { try recordedBlocks($0).map(block) }
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            #expect(try JSONDecoder.api.decode([Block].self, from: encoder.encode(blocks)) == blocks)
        }
    }

    // MARK: CY7: iPad, iPhone, Mac

    @Suite struct Devices {
        @Test func theDeviceIsNamedForWhatItIs() {
            #expect(DeviceInfo.kind(model: "iPad14,1") == .iPad)
            #expect(DeviceInfo.kind(model: "iPhone15,2") == .iPhone)
            #expect(DeviceInfo.kind(model: "MacBookPro18,3") == .mac)
            #expect(DeviceInfo.kind(model: nil) == .iPhone)
            let pad = DeviceInfo.make(kind: .iPad, appVersion: "1.0.0", language: "en")
            #expect(pad.name == "iPad" && pad.platform == "ipados")
            let phone = DeviceInfo.make(kind: .iPhone, appVersion: "1.0.0", language: "en")
            #expect(phone.name == "iPhone" && phone.platform == "ios")
            #expect(DeviceInfo.make(kind: .mac, macName: "Studio", appVersion: "1", language: nil).name == "Studio")
            #expect(DeviceInfo.make(kind: .mac, macName: "", appVersion: "1", language: nil).name == "Mac")
        }

        @Test func anIPadSignsInAsAnIPad() async throws {
            Stub.reset { _, _ in (200, fixture("login"), [:]) }
            let pad = DeviceInfo.make(kind: .iPad, appVersion: "1.0.0", language: "en")
            let api = APIClient(baseURL: server, tokens: MemoryTokenStore(), device: pad, session: Stub.session)
            _ = try await api.signIn(username: "admin", password: "pw")
            #expect(Stub.seen.first?.body["device_name"] as? String == "iPad")
            #expect(Stub.seen.first?.body["platform"] as? String == "ipados")
        }
    }

    // MARK: CY8: older messages

    @Suite @MainActor struct Paging {
        nonisolated static func page(_ ids: [String]) -> Data {
            json(ids.map { ["id": $0, "role": "user", "text": "message \($0)", "created_at": "2026-11-28T10:00:00+00:00"] })
        }

        @Test func theLatestPageThenOlderOnesAsTheReaderScrollsUp() async throws {
            Stub.reset { req, _ in
                let before = URLComponents(url: req.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "before" }?.value
                switch before {
                case nil: return (200, Self.page(["m3", "m4"]), ["X-More-Before": "true"])
                case "m3": return (200, Self.page(["m1", "m2"]), ["X-More-Before": "false"])
                default: return (404, json(["detail": "Message not found"]), [:])
                }
            }
            let chat = plainChat()
            await chat.open("t-1")
            #expect(chat.messages.map(\.id) == ["m3", "m4"])
            #expect(chat.moreBefore)
            #expect(Stub.seen.last?.path == "/api/mobile/v1/threads/t-1/messages")
            #expect(Stub.seen.last?.query == ["limit": "\(ChatModel.pageSize)"])
            await chat.loadEarlier()
            #expect(chat.messages.map(\.id) == ["m1", "m2", "m3", "m4"])
            #expect(Stub.seen.last?.query == ["before": "m3", "limit": "\(ChatModel.pageSize)"])
            #expect(!chat.moreBefore)
            let asked = Stub.seen.count
            await chat.loadEarlier()                              // nothing older: nothing asked
            #expect(Stub.seen.count == asked)
        }

        @Test func aPageWithoutTheHeaderHasNothingBefore() async throws {
            Stub.reset { _, _ in (200, Self.page(["m1"]), [:]) }
            let page = try await signedInClient().messagesPage(thread: "t", after: "m0")
            #expect(page.messages.count == 1 && !page.moreBefore)
            #expect(Stub.seen.last?.query == ["after": "m0"])
        }
    }

    // MARK: CY9: files with a message, in pieces

    /// The server's side of a piecewise upload (app/services/mobile_uploads.py): a piece anywhere but where the
    /// upload stands writes nothing; the last one makes the attachment.
    final class UploadServer: @unchecked Sendable {
        var have = Data()
        var size = 0
        var sha = ""
        var corruptNext = false
        var already = 0
        var pieces: [(offset: Int, count: Int)] = []
        var chatBodies: [[String: Any]] = []

        func state() -> Data {
            let done = size > 0 && have.count == size
            let attachment: Any = done ? ["id": "att-1", "file_name": "statement.csv", "content_type": "text/csv",
                                          "size_bytes": size] as [String: Any] : NSNull()
            return json(["id": "up-1", "received": have.count, "size_bytes": size, "max_chunk": 1_048_576, "done": done,
                         "attachment": attachment])
        }

        func handle(_ req: URLRequest, _ raw: Data) -> (Int, Data, [String: String]) {
            let path = req.url!.path
            if path.hasSuffix("/uploads/begin") {
                let body = (try? JSONSerialization.jsonObject(with: raw)) as? [String: Any] ?? [:]
                size = body["size_bytes"] as? Int ?? 0
                sha = body["sha256"] as? String ?? ""
                return (200, state(), [:])
            }
            if path.hasSuffix("/uploads/up-1") {
                let offset = Int(URLComponents(url: req.url!, resolvingAgainstBaseURL: false)?.queryItems?.first?.value ?? "") ?? -1
                pieces.append((offset, raw.count))
                if corruptNext { corruptNext = false; have = Data(); return (400, json(["detail": "damaged"]), ["X-Error-Code": "upload_corrupt"]) }
                if offset == have.count { have.append(raw) }
                return (200, state(), [:])
            }
            if path.hasSuffix("/chat") {
                chatBodies.append((try? JSONSerialization.jsonObject(with: raw)) as? [String: Any] ?? [:])
                return (200, fixture("chat-hi"), [:])
            }
            return (200, fixture("threads"), [:])
        }
    }

    @Suite @MainActor struct Uploads {
        static let file = Data((0..<3_500_000).map { UInt8($0 % 251) })

        @Test func aFileGoesInPiecesNoneAboveAMegabyte() async throws {
            let srv = UploadServer()
            Stub.resetRaw(srv.handle)
            let seen = Heard()
            let a = try await signedInClient().upload(Self.file, fileName: "statement.csv", contentType: "text/csv") { p in
                seen.add(p)
            }
            #expect(a.id == "att-1")
            #expect(srv.have == Self.file)
            #expect(srv.sha == SHA256.hash(data: Self.file).map { String(format: "%02x", $0) }.joined())
            #expect(srv.pieces.first?.count == APIClient.firstPiece)                  // small first, as on EDGE
            #expect(srv.pieces.allSatisfy { $0.count <= 1_048_576 && $0.count > 0 })
            var next = 0
            for piece in srv.pieces { #expect(piece.offset == next); next += piece.count }  // one after another
            #expect(next == Self.file.count)
            let puts = Stub.seen.filter { $0.method == "PUT" }
            #expect(puts.allSatisfy { $0.headers["Content-Type"] == "application/octet-stream" })
            #expect(seen.values.last == 1)
        }

        @Test func anUploadGoesOnFromWhatTheServerKept() async throws {
            let srv = UploadServer()
            srv.have = Self.file.prefix(1_048_576)
            Stub.resetRaw(srv.handle)
            _ = try await signedInClient().upload(Self.file, fileName: "s.csv", contentType: "text/csv")
            #expect(srv.pieces.first?.offset == 1_048_576)
            #expect(srv.have == Self.file)
        }

        @Test func aDamagedUploadStartsOnceMore() async throws {
            let srv = UploadServer()
            srv.corruptNext = true
            Stub.resetRaw(srv.handle)
            let a = try await signedInClient().upload(Data(Self.file.prefix(200_000)), fileName: "s.csv", contentType: "text/csv")
            #expect(a.id == "att-1")
            #expect(Stub.seen.filter { $0.path.hasSuffix("/uploads/begin") }.count == 2)
            #expect(srv.have == Self.file.prefix(200_000))
        }

        @Test func piecesFollowTheLine() {
            #expect(APIClient.nextPiece(65_536, seconds: 0.2, max: 1_048_576) == 131_072)
            #expect(APIClient.nextPiece(1_048_576, seconds: 0.2, max: 1_048_576) == 1_048_576)
            #expect(APIClient.nextPiece(262_144, seconds: 6, max: 1_048_576) == 131_072)
            #expect(APIClient.nextPiece(65_536, seconds: 6, max: 1_048_576) == 65_536)
            #expect(APIClient.nextPiece(262_144, seconds: 2, max: 1_048_576) == 262_144)
        }

        @Test func theUploadedFileGoesWithTheMessage() async throws {
            let srv = UploadServer()
            Stub.resetRaw(srv.handle)
            let chat = plainChat()
            await chat.attach(Data(Self.file.prefix(1000)), name: "statement.csv", mime: "text/csv")
            #expect(chat.attachments.map(\.id) == ["att-1"])
            #expect(chat.canSend)                                  // a file alone may be sent
            await chat.send()
            #expect(srv.chatBodies.first?["attachment_ids"] as? [String] == ["att-1"])
            #expect(chat.attachments.isEmpty)
            #expect(chat.messages.first?.files?.first?.name == "statement.csv")
        }

        @Test func aFailedUploadSaysSoInTheUsersWords() async throws {
            Stub.reset { _, _ in (502, Data(), [:]) }
            let chat = plainChat()
            await chat.attach(Data("a,b\n".utf8), name: "s.csv", mime: "text/csv")
            #expect(chat.notice == .uploadFailed)
            Stub.reset { _, _ in (400, json(["detail": "Unsupported file type."]), [:]) }
            await chat.attach(Data("x".utf8), name: "s.exe", mime: "application/octet-stream")
            #expect(chat.error == "Unsupported file type.")
            #expect(chat.attachments.isEmpty)
        }
    }

    // MARK: CY10: the streamed reply

    @Suite @MainActor struct Streaming {
        static let reply = String(data: fixture("chat-hi"), encoding: .utf8)!.replacingOccurrences(of: "\n", with: "")

        @Test func theStepsThenTheReply() async throws {
            let stream = "event: status\ndata: {\"stage\": \"thinking\", \"text\": \"Reading the books…\"}\n\n"
                + "event: reply\ndata: \(Self.reply)\n\nevent: done\ndata: {}\n\n"
            Stub.reset { _, _ in (200, Data(stream.utf8), ["Content-Type": "text/event-stream"]) }
            let steps = Heard()
            let reply = try await signedInClient().sendStreaming(message: "hi", thread: nil, clientMessageID: "c-1",
                                                                 attachmentIDs: ["att-1"]) { steps.say($0) }
            #expect(reply.threadId == (try JSONDecoder.api.decode(ChatReply.self, from: fixture("chat-hi"))).threadId)
            #expect(steps.said == ["Reading the books…"])
            let req = try #require(Stub.seen.first)
            #expect(req.path == "/api/mobile/v1/chat/stream")
            #expect(req.headers["Accept"] == "text/event-stream")
            #expect(req.body["client_message_id"] as? String == "c-1")
            #expect(req.body["attachment_ids"] as? [String] == ["att-1"])
        }

        @Test func anErrorEventIsTheServersRefusal() async throws {
            let stream = "event: error\ndata: {\"status\": 429, \"detail\": \"Too many messages.\", \"code\": \"ai_rate_limited\"}\n\nevent: done\ndata: {}\n\n"
            Stub.reset { _, _ in (200, Data(stream.utf8), [:]) }
            let error = await #expect(throws: APIError.self) {
                try await signedInClient().sendStreaming(message: "hi", thread: nil, clientMessageID: "c") { _ in }
            }
            #expect(error == APIError(status: 429, detail: "Too many messages.", code: "ai_rate_limited"))
        }

        @Test func aStreamCutShortGoesOnceMoreToChatWithTheSameId() async throws {
            Stub.reset { req, _ in
                switch req.url!.path {
                case let p where p.hasSuffix("/chat/stream"):
                    return (200, Data("event: status\ndata: {\"text\": \"Thinking\"}\n\n".utf8), [:])   // and nothing more
                case let p where p.hasSuffix("/chat"): return (200, fixture("chat-hi"), [:])
                default: return (200, fixture("threads"), [:])
                }
            }
            let chat = ChatModel(api: signedInClient())
            chat.draft = "hi"
            await chat.send()
            #expect(chat.error == nil)
            #expect(chat.messages.count == 2)
            let ids = Stub.seen.filter { $0.path.hasSuffix("/chat") || $0.path.hasSuffix("/chat/stream") }
                .compactMap { $0.body["client_message_id"] as? String }
            #expect(ids.count == 2 && ids[0] == ids[1])
            #expect(chat.status == nil)
        }

        @Test func aServerWithoutTheStreamIsAskedPlainly() async throws {
            Stub.reset { req, _ in
                req.url!.path.hasSuffix("/chat/stream") ? (404, json(["detail": "Not Found"]), [:])
                    : req.url!.path.hasSuffix("/chat") ? (200, fixture("chat-hi"), [:]) : (200, fixture("threads"), [:])
            }
            let chat = ChatModel(api: signedInClient())
            chat.draft = "hi"
            await chat.send()
            #expect(chat.error == nil && chat.messages.count == 2)
        }

        @Test func aRefusalInTheStreamIsNotSentTwice() async throws {
            Stub.reset { req, _ in
                req.url!.path.hasSuffix("/chat/stream")
                    ? (200, Data("event: error\ndata: {\"status\": 409, \"detail\": \"Still working.\", \"code\": \"turn_in_progress\"}\n\n".utf8), [:])
                    : (200, fixture("chat-hi"), [:])
            }
            let chat = ChatModel(api: signedInClient())
            chat.draft = "hi"
            await chat.send()
            #expect(chat.error == "Still working.")
            #expect(!Stub.seen.contains { $0.path.hasSuffix("/chat") })
            #expect(chat.canRetry)
        }
    }

    // MARK: CY5: approvals

    @Suite @MainActor struct Approvals {
        nonisolated static let token = "0b6f2b8e-7a51-4c2e-9d3e-5f1a2b3c4d5e"

        @Test func approvedItIsPostedWithItsUndo() async throws {
            let card = try recordedBlocks("approval-fa")
            Stub.reset { req, _ in
                switch req.url!.path {
                case "/api/mobile/v1/approvals": return (200, json(card), [:])
                case let p where p.hasSuffix("/approve"):
                    return (200, json(["state": "posted", "block": ["type": "posted", "id": "posted:\(Self.token)",
                        "token": Self.token, "audit_log_id": "a-9", "voucher": "1042", "undo_seconds": 120,
                        "actions": ["undo"], "fallback_text": "Posted · 1042"]]), [:])
                default: return (404, Data(), [:])
                }
            }
            let chat = plainChat()
            await chat.loadApprovals()
            #expect(chat.approvals.count == 1)
            await chat.approve(Self.token)
            #expect(chat.approvals.first?.card?.phase == .posted)
            guard case .posted(_, let posted, _)? = chat.approvals.dropFirst().first else { Issue.record("the receipt under it"); return }
            #expect(posted.canUndo)
            #expect(Stub.seen.contains { $0.method == "POST" && $0.path == "/api/mobile/v1/approvals/\(Self.token)/approve" })
        }

        @Test func rejectedItSaysSoWithTheReasonSent() async throws {
            let card = try recordedBlocks("approval-fa")
            Stub.reset { req, _ in
                req.url!.path.hasSuffix("/reject") ? (200, json(["state": "rejected"]), [:]) : (200, json(card), [:])
            }
            let chat = plainChat()
            await chat.loadApprovals()
            await chat.reject(Self.token, note: "  not this month ")
            #expect(chat.approvals.first?.card?.phase == .rejected)
            #expect(Stub.seen.last?.body["note"] as? String == "not this month")
        }

        @Test func aConfirmAboveTheThresholdWaitsForASecondPerson() async throws {
            Stub.reset { req, _ in
                switch req.url!.path {
                case let p where p.hasSuffix("/chat"): return (200, fixture("chat-card"), [:])
                case let p where p.hasSuffix("/confirm"):
                    return (200, json(["state": "waiting_for_approval", "approval": ["threshold": 1000]]), [:])
                default: return (200, fixture("threads"), [:])
                }
            }
            let chat = plainChat()
            chat.draft = "rent 80000000"
            await chat.send()
            guard let token = chat.messages.last?.blocks?.first?.card?.token else { Issue.record(); return }
            await chat.confirm(token)
            let card = chat.messages.last?.blocks?.first?.card
            #expect(card?.phase == .waiting)
            #expect(card?.canConfirm == false)
            #expect(L10n("fa").phase(card!.phase) == "در انتظار تأیید دوم")
        }

        @Test func undoTurnsItsCardToUndone() async throws {
            Stub.reset { req, _ in
                switch req.url!.path {
                case let p where p.hasSuffix("/chat"): return (200, fixture("chat-card"), [:])
                case let p where p.hasSuffix("/confirm"):
                    let token = req.url!.pathComponents.dropLast().last!
                    return (200, json(["state": "posted", "block": ["type": "posted", "id": "posted:x", "token": token,
                        "audit_log_id": "a-1", "undo_seconds": 120, "actions": ["undo"], "fallback_text": "Posted"]]), [:])
                case let p where p.hasSuffix("/undo"): return (200, json(["state": "undone", "mode": "delete"]), [:])
                default: return (200, fixture("threads"), [:])
                }
            }
            let chat = plainChat()
            chat.draft = "x"
            await chat.send()
            guard let token = chat.messages.last?.blocks?.first?.card?.token else { Issue.record(); return }
            await chat.confirm(token)
            #expect(chat.messages.last?.blocks?.first?.card?.phase == .posted)
            await chat.undo("a-1")
            #expect(chat.messages.last?.blocks?.first?.card?.phase == .undone)
        }
    }

    // MARK: CY6: documents

    @Suite @MainActor struct Documents {
        nonisolated static let invoice = FileRef(name: "invoice-INV-1042.pdf", mime: "application/pdf",
                                     path: "/api/mobile/v1/documents/invoices/7d0c9a40-1f6b-4b7e-8a3c-2b9d6e5f4a31")

        @Test func aDocumentIsFetchedWithTheSessionAndKept() async throws {
            Stub.reset { req, _ in
                req.url!.path == Self.invoice.path ? (200, Data("%PDF-1.4 test".utf8), ["Content-Type": "application/pdf"])
                    : (404, Data(), [:])
            }
            let chat = plainChat()
            let url = try #require(await chat.document(Self.invoice))
            #expect(url.lastPathComponent == "invoice-INV-1042.pdf")
            #expect(try Data(contentsOf: url) == Data("%PDF-1.4 test".utf8))
            #expect(Stub.seen.first?.headers["Authorization"] == "Bearer a")
            #expect(chat.documents[Self.invoice.path] == url)
            _ = await chat.document(Self.invoice)                  // kept: not fetched again
            #expect(Stub.seen.count == 1)
        }

        @Test func aDocumentThatDoesntComeSaysSo() async throws {
            Stub.reset { _, _ in (500, Data(), [:]) }
            let chat = plainChat()
            let url = await chat.document(FileRef(name: "p.pdf", mime: nil, path: "/api/mobile/v1/documents/payslips/a/b"))
            #expect(url == nil && chat.notice == .fileFailed)
            let error = await #expect(throws: APIError.self) {
                try await signedInClient().download(FileRef(name: "x", mime: nil, path: "https://elsewhere.example.com/x"))
            }
            #expect(error?.code == "not_a_document")
        }
    }

    // MARK: CY11: the voice note

    @Suite @MainActor struct Voice {
        @Test func theVoiceNotesWordsGoIntoTheComposer() async throws {
            Stub.resetRaw { req, raw in
                req.url!.path.hasSuffix("/transcribe") ? (200, json(["text": " paid 20 for lunch "]), [:]) : (404, Data(), [:])
            }
            let chat = plainChat()
            chat.draft = "and"
            await chat.heard(Data([0, 1, 2, 3]))
            #expect(chat.draft == "and paid 20 for lunch")
            let req = try #require(Stub.seen.first)
            #expect(req.headers["Content-Type"]?.hasPrefix("multipart/form-data; boundary=") == true)
            let body = String(decoding: req.raw, as: UTF8.self)
            #expect(body.contains("name=\"file\"; filename=\"voice-note.m4a\""))
            #expect(body.contains("Content-Type: audio/mp4"))
            #expect(!chat.transcribing)
        }

        @Test func aVoiceNoteNotMadeOutSaysSo() async throws {
            Stub.reset { _, _ in (422, json(["detail": "No speech"]), [:]) }
            let chat = plainChat()
            await chat.heard(Data([1]))
            #expect(chat.notice == .voiceFailed && chat.draft.isEmpty)
        }
    }

    // MARK: CY12: an app too old

    @Suite @MainActor struct Update {
        @Test func aTooOldAppIsToldToUpdate() async throws {
            Stub.reset { _, _ in
                (426, json(["code": "upgrade_required", "min_version": "2.0.0",
                            "detail": "This version of the app is too old. Update it to keep going."]), [:])
            }
            let error = await #expect(throws: APIError.self) { try await signedInClient().threads() }
            #expect(error?.updateRequired == true && error?.code == "upgrade_required")
            let app = AppModel(defaults: UserDefaults(suiteName: "chatcore-tests-\(UUID())")!, session: Stub.session,
                               tokens: { _ in MemoryTokenStore() })
            app.serverAddress = "https://books.example.com"
            await app.signIn(username: "admin", password: "pw")
            #expect(app.phase == .updateRequired)
            #expect(app.error == nil)
        }

        @Test func theChatTooSendsItToTheUpdateScreen() async throws {
            nonisolated(unsafe) var old = false
            Stub.reset { req, _ in
                if old { return (426, json(["code": "upgrade_required", "detail": "too old"]), [:]) }
                return req.url!.path.hasSuffix("/auth/login") ? (200, fixture("login"), [:]) : (200, fixture("threads"), [:])
            }
            let tokens = MemoryTokenStore()
            let app = AppModel(defaults: UserDefaults(suiteName: "chatcore-tests-\(UUID())")!, session: Stub.session,
                               tokens: { _ in tokens })
            app.serverAddress = "https://books.example.com"
            await app.signIn(username: "admin", password: "pw")
            #expect(app.phase == .signedIn)
            old = true
            await app.chat?.refreshThreads()
            #expect(app.phase == .updateRequired)
            #expect(tokens.load() != nil)                          // still signed in, for after the update
        }
    }
}

/// What a callback heard, from wherever it was called.
final class Heard: @unchecked Sendable {
    private let lock = NSLock()
    private var _values: [Double] = []
    private var _said: [String] = []
    func add(_ v: Double) { lock.lock(); _values.append(v); lock.unlock() }
    func say(_ s: String) { lock.lock(); _said.append(s); lock.unlock() }
    var values: [Double] { lock.lock(); defer { lock.unlock() }; return _values }
    var said: [String] { lock.lock(); defer { lock.unlock() }; return _said }
}

// MARK: - Without the network

@Suite struct ServerSentEvents {
    @Test func eventsWithTheirData() {
        let text = ": keep-alive\nevent: status\ndata: {\"text\": \"one\"}\n\nevent: reply\r\ndata: line 1\ndata: line 2\r\n\r\nevent: done\ndata: {}\n\n"
        #expect(SSEParser.events(in: text) == [
            .init(name: "status", data: "{\"text\": \"one\"}"),
            .init(name: "reply", data: "line 1\nline 2"),
            .init(name: "done", data: "{}"),
        ])
        #expect(SSEParser.events(in: text).first?.json?["text"] as? String == "one")
    }

    @Test func aStreamWithoutBlankLinesStillReads() {
        let text = "event: status\ndata: {}\nevent: reply\ndata: {\"a\":1}\nevent: done\ndata: {}"
        #expect(SSEParser.events(in: text).map(\.name) == ["status", "reply", "done"])
        #expect(SSEParser.events(in: "data:x\n\n") == [.init(name: "message", data: "x")])
        #expect(SSEParser.events(in: "event: done\n\n").isEmpty)
    }
}

@Suite struct Photos {
    static func image(as type: UTType) -> Data {
        let ctx = CGContext(data: nil, width: 8, height: 8, bitsPerComponent: 8, bytesPerRow: 0,
                            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        ctx.setFillColor(red: 0, green: 0.43, blue: 0.47, alpha: 1)
        ctx.fill(CGRect(x: 0, y: 0, width: 8, height: 8))
        let out = NSMutableData()
        let dest = CGImageDestinationCreateWithData(out, type.identifier as CFString, 1, nil)!
        CGImageDestinationAddImage(dest, ctx.makeImage()!, nil)
        CGImageDestinationFinalize(dest)
        return out as Data
    }

    @Test func aPhotoTheServerDoesntReadGoesAsAJPEG() {
        let tiff = Self.image(as: .tiff)
        let sent = Attachments.prepare(tiff, name: "scan.tiff", mime: "image/tiff")
        #expect(sent.mime == "image/jpeg" && sent.name == "scan.jpg")
        #expect(Array(sent.data.prefix(2)) == [0xFF, 0xD8])
        let png = Self.image(as: .png)
        #expect(Attachments.prepare(png, name: "a.png", mime: "image/png").data == png)
        let pdf = Data("%PDF-1.4".utf8)
        #expect(Attachments.prepare(pdf, name: "a.pdf", mime: "application/pdf").data == pdf)
    }

    @Test func aFilesTypeFromItsName() {
        #expect(Attachments.mime(forFileName: "s.csv") == "text/csv")
        #expect(Attachments.mime(forFileName: "s.XLSX") == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        #expect(Attachments.mime(forFileName: "s.xls") == "application/vnd.ms-excel")
        #expect(Attachments.mime(forFileName: "s.pdf") == "application/pdf")
        #expect(Attachments.mime(forFileName: "photo.jpeg") == "image/jpeg")
    }
}

@Suite struct StateWords {
    @Test func everyStateHasWordsInEveryLanguage() {
        for lang in L10n.supported {
            let t = L10n(lang)
            var said = Set<String>()
            for phase in Proposal.Phase.allCases {
                let words = t.phase(phase)
                #expect(!words.isEmpty, "\(lang): \(phase)")
                #expect(!words.contains("_"), "\(lang): \(phase) reads as a server word: \(words)")
                said.insert(words)
            }
            #expect(said.count == Proposal.Phase.allCases.count, "\(lang): two states said the same")
            for notice in [ChatNotice.uploadFailed, .fileFailed, .voiceFailed, .voiceTooShort, .microphoneOff] {
                #expect(!t.notice(notice).isEmpty)
            }
        }
        #expect(L10n("es").phase(.rejected) == "Rechazado")
        #expect(L10n("ar").phase(.undone) == "تم التراجع — الدفاتر كما كانت")
    }

    @Test func everyTranslationTakesWhatEnglishTakes() {
        func slots(_ s: String) -> Int { s.components(separatedBy: "%@").count - 1 }
        for key in L10n.Key.allCases {
            let english = slots(L10n("en")[key])
            for lang in L10n.supported { #expect(slots(L10n(lang)[key]) == english, "\(lang): \(key)") }
        }
        #expect(L10n("fa")(.askedBy, "maryam") == "منتظر تأیید شما، به درخواست \u{2068}maryam\u{2069}")
    }
}
