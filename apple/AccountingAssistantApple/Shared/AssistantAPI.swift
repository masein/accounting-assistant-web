import Foundation

final class AssistantAPI {
    private let jsonDecoder: JSONDecoder
    private let jsonEncoder: JSONEncoder

    init() {
        self.jsonDecoder = JSONDecoder()
        self.jsonEncoder = JSONEncoder()
    }

    func sendChat(
        baseURL: String,
        messages: [BackendChatMessage],
        attachmentIDs: [UUID]
    ) async throws -> BackendChatResponse {
        let payload = BackendChatRequest(messages: messages, attachment_ids: attachmentIDs)
        return try await request(
            baseURL: baseURL,
            path: "/transactions/chat",
            method: "POST",
            body: payload,
            responseType: BackendChatResponse.self
        )
    }

    func saveTransaction(
        baseURL: String,
        draft: TransactionDraft,
        attachmentIDs: [UUID]
    ) async throws -> BackendTransactionRead {
        let requestBody = BackendTransactionCreateRequest(
            date: draft.suggestion.date,
            reference: draft.suggestion.reference,
            description: draft.suggestion.description,
            lines: draft.suggestion.lines,
            entity_links: draft.resolvedEntities.map { BackendEntityLink(role: $0.role, entity_id: $0.entity_id) },
            attachment_ids: attachmentIDs
        )
        return try await request(
            baseURL: baseURL,
            path: "/transactions",
            method: "POST",
            body: requestBody,
            responseType: BackendTransactionRead.self
        )
    }

    func uploadAttachment(
        baseURL: String,
        fileData: Data,
        filename: String,
        mimeType: String
    ) async throws -> UploadedAttachment {
        guard let url = URL(string: baseURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/transactions/attachments") else {
            throw AssistantError.invalidURL
        }

        let boundary = "Boundary-\(UUID().uuidString)"
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = makeMultipartBody(boundary: boundary, fileData: fileData, filename: filename, mimeType: mimeType)

        let (data, response) = try await URLSession.shared.data(for: request)
        try throwIfNeeded(data: data, response: response)
        return try jsonDecoder.decode(UploadedAttachment.self, from: data)
    }

    func ownerDashboard(baseURL: String) async throws -> OwnerDashboardResponse {
        try await request(baseURL: baseURL, path: "/reports/owner-dashboard", method: "GET", body: Optional<String>.none, responseType: OwnerDashboardResponse.self)
    }

    func ledgerSummary(baseURL: String) async throws -> LedgerSummaryResponse {
        try await request(baseURL: baseURL, path: "/reports/ledger-summary", method: "GET", body: Optional<String>.none, responseType: LedgerSummaryResponse.self)
    }

    func missingReferences(baseURL: String) async throws -> MissingReferenceResponse {
        try await request(baseURL: baseURL, path: "/reports/missing-references", method: "GET", body: Optional<String>.none, responseType: MissingReferenceResponse.self)
    }

    func invoices(baseURL: String) async throws -> [InvoiceItem] {
        try await request(baseURL: baseURL, path: "/invoices", method: "GET", body: Optional<String>.none, responseType: [InvoiceItem].self)
    }

    func entities(baseURL: String) async throws -> [EntityRead] {
        try await request(baseURL: baseURL, path: "/entities", method: "GET", body: Optional<String>.none, responseType: [EntityRead].self)
    }

    func transactions(baseURL: String, skip: Int = 0, limit: Int = 100) async throws -> [BackendTransactionRead] {
        let q = "/transactions?skip=\(max(0, skip))&limit=\(min(max(1, limit), 200))"
        return try await request(baseURL: baseURL, path: q, method: "GET", body: Optional<String>.none, responseType: [BackendTransactionRead].self)
    }

    func entityTransactions(baseURL: String, entityID: UUID) async throws -> [BackendTransactionRead] {
        try await request(
            baseURL: baseURL,
            path: "/reports/entities/\(entityID.uuidString)/transactions",
            method: "GET",
            body: Optional<String>.none,
            responseType: [BackendTransactionRead].self
        )
    }

    func accountDetail(baseURL: String, accountCode: String) async throws -> AccountDetailResponse {
        try await request(
            baseURL: baseURL,
            path: "/reports/accounts/\(accountCode)/detail",
            method: "GET",
            body: Optional<String>.none,
            responseType: AccountDetailResponse.self
        )
    }

    private func request<T: Decodable, Body: Encodable>(
        baseURL: String,
        path: String,
        method: String,
        body: Body?,
        responseType: T.Type
    ) async throws -> T {
        guard let url = URL(string: normalized(baseURL: baseURL) + path) else {
            throw AssistantError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try jsonEncoder.encode(body)
        }

        let (data, response) = try await URLSession.shared.data(for: request)
        try throwIfNeeded(data: data, response: response)
        return try jsonDecoder.decode(responseType, from: data)
    }

    private func throwIfNeeded(data: Data, response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else {
            throw AssistantError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            if let message = try? parseServerError(data: data) {
                throw AssistantError.server(message)
            }
            throw AssistantError.server("Server error \(http.statusCode)")
        }
    }

    private func parseServerError(data: Data) throws -> String {
        struct ErrorDetail: Decodable {
            let detail: String?
        }
        if let detail = try? jsonDecoder.decode(ErrorDetail.self, from: data), let text = detail.detail, !text.isEmpty {
            return text
        }
        if let raw = String(data: data, encoding: .utf8), !raw.isEmpty {
            return raw
        }
        return "Unknown backend error"
    }

    private func makeMultipartBody(boundary: String, fileData: Data, filename: String, mimeType: String) -> Data {
        var body = Data()
        let boundaryPrefix = "--\(boundary)\r\n"
        body.append(Data(boundaryPrefix.utf8))
        body.append(Data("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".utf8))
        body.append(Data("Content-Type: \(mimeType)\r\n\r\n".utf8))
        body.append(fileData)
        body.append(Data("\r\n".utf8))
        body.append(Data("--\(boundary)--\r\n".utf8))
        return body
    }

    private func normalized(baseURL: String) -> String {
        let trimmed = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.hasSuffix("/") ? String(trimmed.dropLast()) : trimmed
    }
}
