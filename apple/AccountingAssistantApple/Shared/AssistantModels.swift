import Foundation

enum ChatActor: String {
    case user
    case assistant
    case system
}

enum ChatCardPayload {
    case draft(TransactionDraft)
    case dashboard(OwnerDashboardResponse)
    case ledger(LedgerSummaryResponse)
    case invoices([InvoiceItem])
    case missingReferences([MissingReferenceItem])
    case transactions(TransactionQueryCard)
    case history(HistoryChartCardData)
}

struct ChatEntry: Identifiable {
    let id: UUID
    let actor: ChatActor
    let text: String
    let payload: ChatCardPayload?
    let createdAt: Date

    init(id: UUID = UUID(), actor: ChatActor, text: String, payload: ChatCardPayload? = nil, createdAt: Date = Date()) {
        self.id = id
        self.actor = actor
        self.text = text
        self.payload = payload
        self.createdAt = createdAt
    }
}

struct UploadedAttachment: Identifiable, Decodable {
    let id: UUID
    let file_name: String
    let content_type: String
    let size_bytes: Int
    let url: String
    let transaction_id: UUID?
}

struct EntityRead: Decodable, Identifiable {
    let id: UUID
    let type: String
    let name: String
    let code: String?
}

struct BackendChatMessage: Codable {
    let role: String
    let content: String
}

struct BackendChatRequest: Codable {
    let messages: [BackendChatMessage]
    let attachment_ids: [UUID]
}

struct BackendEntityMention: Codable {
    let role: String
    let name: String
}

struct BackendResolvedEntityLink: Codable {
    let role: String
    let entity_id: UUID
}

struct BackendTransactionLine: Codable {
    let account_code: String
    let debit: Int
    let credit: Int
    let line_description: String?
}

struct BackendTransactionSuggestion: Codable {
    let date: String
    let reference: String?
    let description: String?
    let lines: [BackendTransactionLine]
}

struct BackendChatResponse: Codable {
    let message: String
    let transaction: BackendTransactionSuggestion?
    let entity_mentions: [BackendEntityMention]?
    let resolved_entities: [BackendResolvedEntityLink]?
}

struct BackendEntityLink: Codable {
    let role: String
    let entity_id: UUID
}

struct BackendTransactionCreateRequest: Codable {
    let date: String
    let reference: String?
    let description: String?
    let lines: [BackendTransactionLine]
    let entity_links: [BackendEntityLink]
    let attachment_ids: [UUID]
}

struct BackendTransactionRead: Decodable {
    let id: UUID
    let date: String
    let reference: String?
    let description: String?
    let lines: [BackendTransactionLine]?
    let entity_links: [BackendTransactionEntityLink]?
    let attachments: [UploadedAttachment]?
    let created_at: String?
    let updated_at: String?
}

struct BackendTransactionEntityLink: Decodable {
    let role: String
    let entity_id: UUID
    let entity_name: String?
    let entity_type: String?
}

struct TransactionDraft {
    let suggestion: BackendTransactionSuggestion
    let resolvedEntities: [BackendResolvedEntityLink]
    let mentions: [BackendEntityMention]
}

struct KPI: Decodable, Identifiable {
    var id: String { key }
    let key: String
    let label: String
    let value: Double
    let unit: String?
    let trend: Double?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        label = try c.decode(String.self, forKey: .label)
        unit = try c.decodeIfPresent(String.self, forKey: .unit)
        trend = try c.decodeIfPresent(Double.self, forKey: .trend)

        if let intVal = try? c.decode(Int.self, forKey: .value) {
            value = Double(intVal)
        } else {
            value = try c.decode(Double.self, forKey: .value)
        }
    }

    private enum CodingKeys: String, CodingKey {
        case key, label, value, unit, trend
    }
}

struct ForecastPoint: Decodable, Identifiable {
    var id: String { week_start }
    let week_start: String
    let projected_inflow: Int
    let projected_outflow: Int
    let projected_net: Int
    let projected_cash: Int
    let risk: Bool
}

struct MonthlySeriesPoint: Decodable, Identifiable {
    var id: String { period }
    let period: String
    let value: Int
}

struct VendorSpendPoint: Decodable, Identifiable {
    var id: String { vendor }
    let vendor: String
    let amount: Int
}

struct OwnerDashboardResponse: Decodable {
    let generated_on: String
    let kpis: [KPI]
    let forecast_13_weeks: [ForecastPoint]
    let spend_by_vendor: [VendorSpendPoint]
    let monthly_expense_series: [MonthlySeriesPoint]
    let owner_pack_markdown: String
}

struct LedgerRow: Decodable, Identifiable {
    var id: String { account_code }
    let account_code: String
    let account_name: String
    let debit_turnover: Int
    let credit_turnover: Int
    let debit_balance: Int
    let credit_balance: Int
}

struct LedgerSummaryResponse: Decodable {
    let rows: [LedgerRow]
    let total_debit_turnover: Int
    let total_credit_turnover: Int
    let total_debit_balance: Int
    let total_credit_balance: Int
}

struct AccountDetailLine: Decodable, Identifiable {
    var id: String { "\(transaction_date)-\(reference ?? "-")-\(debit)-\(credit)-\(line_description ?? "-")" }
    let transaction_date: String
    let reference: String?
    let description: String?
    let debit: Int
    let credit: Int
    let line_description: String?
}

struct AccountDetailResponse: Decodable {
    let account_code: String
    let account_name: String
    let debit_turnover: Int
    let credit_turnover: Int
    let debit_balance: Int
    let credit_balance: Int
    let lines: [AccountDetailLine]
}

struct MissingReferenceItem: Decodable, Identifiable {
    var id: String { transaction_id }
    let transaction_id: String
    let date: String
    let description: String?
    let suggested_reference: String?
}

struct MissingReferenceResponse: Decodable {
    let items: [MissingReferenceItem]
}

struct InvoiceItem: Decodable, Identifiable {
    let id: UUID
    let number: String
    let kind: String
    let status: String
    let issue_date: String
    let due_date: String
    let amount: Int
    let currency: String
    let description: String?
}

struct TransactionQueryCard {
    let title: String
    let subtitle: String
    let items: [BackendTransactionRead]
}

struct HistoryChartPoint: Identifiable {
    var id: String { label }
    let label: String
    let value: Int
}

struct HistoryChartCardData {
    let title: String
    let subtitle: String
    let metricLabel: String
    let points: [HistoryChartPoint]
}

enum AssistantError: LocalizedError {
    case invalidURL
    case invalidResponse
    case server(String)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Backend URL is invalid."
        case .invalidResponse:
            return "Unexpected server response."
        case .server(let message):
            return message
        }
    }
}
