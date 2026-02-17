import Foundation

@MainActor
final class ChatViewModel: ObservableObject {
    @Published var messages: [ChatEntry] = []
    @Published var composerText: String = ""
    @Published var backendURL: String {
        didSet {
            UserDefaults.standard.set(backendURL, forKey: AppConfig.userDefaultsBackendKey)
        }
    }
    @Published var uploadedAttachments: [UploadedAttachment] = []
    @Published var pendingDraft: TransactionDraft?
    @Published var isWorking: Bool = false
    @Published var bannerMessage: String?

    let voice = VoiceInputManager()

    private let api = AssistantAPI()
    private var cachedEntities: [EntityRead] = []
    private var entitiesCacheTime: Date?

    private static let isoDayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(secondsFromGMT: 0)
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    private let calendar = Calendar(identifier: .gregorian)

    private struct TimeWindow {
        let start: Date?
        let end: Date?
        let label: String
    }

    init() {
        let saved = UserDefaults.standard.string(forKey: AppConfig.userDefaultsBackendKey) ?? AppConfig.defaultBackendURL
        let normalized = Self.normalizeBackendURL(saved)
        self.backendURL = normalized
        UserDefaults.standard.set(normalized, forKey: AppConfig.userDefaultsBackendKey)
        self.messages = [
            ChatEntry(
                actor: .assistant,
                text: "Hi. I can post vouchers, find transactions, and show dashboard/ledger/balance charts. Try: \"/dashboard\", \"last 10 transactions for Melli\", or \"what is my current balance\"."
            )
        ]
    }

    func sendComposerText() async {
        let text = composerText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        composerText = ""
        await send(text: text)
    }

    func send(text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        append(.user, trimmed)
        let lowerTrimmed = trimmed.lowercased()

        if await handleCommand(trimmed) {
            return
        }

        isWorking = true
        defer { isWorking = false }

        do {
            let history = historyForBackend()
            let response = try await api.sendChat(
                baseURL: backendURL,
                messages: history,
                attachmentIDs: uploadedAttachments.map(\.id)
            )
            append(.assistant, response.message)

            // If backend fallback couldn't parse, retry with local structured query handlers.
            if isBackendFallbackMessage(response.message) {
                if isTransactionQueryCommand(lowerTrimmed) {
                    await loadTransactionQuery(query: trimmed)
                    return
                }
                if isHistoryQueryCommand(lowerTrimmed) {
                    await loadHistoryQuery(query: trimmed)
                    return
                }
            }

            if let suggestion = response.transaction {
                let draft = TransactionDraft(
                    suggestion: suggestion,
                    resolvedEntities: response.resolved_entities ?? [],
                    mentions: response.entity_mentions ?? []
                )
                pendingDraft = draft
                messages.append(
                    ChatEntry(
                        actor: .assistant,
                        text: "Voucher draft is ready. Review and save.",
                        payload: .draft(draft)
                    )
                )
            }
        } catch {
            append(.system, "Request failed: \(error.localizedDescription)")
        }
    }

    func uploadAttachment(data: Data, filename: String, mimeType: String) async {
        isWorking = true
        defer { isWorking = false }
        do {
            let item = try await api.uploadAttachment(baseURL: backendURL, fileData: data, filename: filename, mimeType: mimeType)
            uploadedAttachments.append(item)
            append(.assistant, "Attached: \(item.file_name)")
        } catch {
            append(.system, "Attachment upload failed: \(error.localizedDescription)")
        }
    }

    func removeAttachment(_ attachment: UploadedAttachment) {
        uploadedAttachments.removeAll { $0.id == attachment.id }
    }

    func savePendingDraft() async {
        guard let draft = pendingDraft else {
            append(.system, "No draft to save.")
            return
        }

        isWorking = true
        defer { isWorking = false }

        do {
            let txn = try await api.saveTransaction(baseURL: backendURL, draft: draft, attachmentIDs: uploadedAttachments.map(\.id))
            append(.assistant, "Saved voucher \(txn.id.uuidString.prefix(8)) for \(txn.date).")
            pendingDraft = nil
            uploadedAttachments = []
        } catch {
            append(.system, "Save failed: \(error.localizedDescription)")
        }
    }

    func applyVoiceTranscript() {
        let text = voice.transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        if composerText.isEmpty {
            composerText = text
        } else {
            composerText += " " + text
        }
        voice.transcript = ""
    }

    private func historyForBackend() -> [BackendChatMessage] {
        let tail = messages.suffix(18)
        return tail.compactMap { entry in
            switch entry.actor {
            case .user:
                return BackendChatMessage(role: "user", content: entry.text)
            case .assistant:
                return BackendChatMessage(role: "assistant", content: entry.text)
            case .system:
                return nil
            }
        }
    }

    private func handleCommand(_ text: String) async -> Bool {
        let lower = text.lowercased()
        if isSaveCommand(lower) {
            await savePendingDraft()
            return true
        }
        if isDashboardCommand(lower) {
            await loadDashboardCard()
            return true
        }
        if isLedgerCommand(lower) {
            await loadLedgerCard()
            return true
        }
        if isMissingRefCommand(lower) {
            await loadMissingReferencesCard()
            return true
        }
        if isInvoicesCommand(lower) {
            await loadInvoicesCard()
            return true
        }
        if isTransactionQueryCommand(lower) {
            await loadTransactionQuery(query: text)
            return true
        }
        if isHistoryQueryCommand(lower) {
            await loadHistoryQuery(query: text)
            return true
        }
        return false
    }

    private func loadDashboardCard() async {
        isWorking = true
        defer { isWorking = false }
        do {
            let dashboard = try await api.ownerDashboard(baseURL: backendURL)
            messages.append(ChatEntry(actor: .assistant, text: "Owner dashboard snapshot", payload: .dashboard(dashboard)))
        } catch {
            append(.system, "Dashboard failed: \(friendlyNetworkHint(for: error))")
        }
    }

    private func loadLedgerCard() async {
        isWorking = true
        defer { isWorking = false }
        do {
            let ledger = try await api.ledgerSummary(baseURL: backendURL)
            messages.append(ChatEntry(actor: .assistant, text: "Ledger top movement accounts", payload: .ledger(ledger)))
        } catch {
            append(.system, "Ledger failed: \(friendlyNetworkHint(for: error))")
        }
    }

    private func loadMissingReferencesCard() async {
        isWorking = true
        defer { isWorking = false }
        do {
            let refs = try await api.missingReferences(baseURL: backendURL)
            messages.append(ChatEntry(actor: .assistant, text: "Missing references that need fixing", payload: .missingReferences(refs.items)))
        } catch {
            append(.system, "Missing references failed: \(friendlyNetworkHint(for: error))")
        }
    }

    private func loadInvoicesCard() async {
        isWorking = true
        defer { isWorking = false }
        do {
            let list = try await api.invoices(baseURL: backendURL)
            messages.append(ChatEntry(actor: .assistant, text: "Recent invoices", payload: .invoices(list)))
        } catch {
            append(.system, "Invoices failed: \(friendlyNetworkHint(for: error))")
        }
    }

    private func loadTransactionQuery(query: String) async {
        isWorking = true
        defer { isWorking = false }

        do {
            let lower = query.lowercased()
            let entities = try await loadEntities()
            let matchedEntity = matchEntity(in: lower, entities: entities)
            let hints = typeHints(in: lower)
            let window = parseTimeWindow(in: lower)
            let requestedLimit = parseLimit(in: lower) ?? 10

            var txns = try await loadTransactionsSource(entity: matchedEntity, requestedLimit: max(requestedLimit * 4, 80))
            txns = sortTransactionsDescending(txns)
            txns = filterTransactions(txns, window: window)

            // Fallback: some historical rows may not be linked to entities.
            // If entity endpoint returns nothing, match by text over recent global transactions.
            if txns.isEmpty, let entity = matchedEntity {
                var globalTxns = try await api.transactions(baseURL: backendURL, limit: 200)
                globalTxns = sortTransactionsDescending(globalTxns)
                globalTxns = filterTransactions(globalTxns, window: window)
                txns = filterTransactionsByEntityNameHeuristic(globalTxns, entityName: entity.name)
            }

            if matchedEntity == nil, !hints.isEmpty {
                txns = filterTransactionsByEntityType(txns, hints: hints)
            }

            let limited = Array(txns.prefix(min(requestedLimit, 100)))
            let title: String
            if let entity = matchedEntity {
                title = "Transactions for \(entity.name)"
            } else if let hinted = hints.first {
                title = "\(hinted.capitalized) transactions"
            } else {
                title = "Transactions"
            }
            let subtitle = "\(window.label) • \(limited.count) of \(txns.count)"
            let card = TransactionQueryCard(title: title, subtitle: subtitle, items: limited)
            messages.append(ChatEntry(actor: .assistant, text: "Here are the transactions I found.", payload: .transactions(card)))
            if limited.isEmpty {
                append(.assistant, "No transactions matched this filter. Try a wider period, for example: \"last 90 days\".")
            }
        } catch {
            append(.system, "Transaction lookup failed: \(friendlyNetworkHint(for: error))")
        }
    }

    private func loadHistoryQuery(query: String) async {
        isWorking = true
        defer { isWorking = false }

        do {
            let lower = query.lowercased()
            let entities = try await loadEntities()
            let matchedEntity = matchEntity(in: lower, entities: entities)
            let window = parseTimeWindow(in: lower)
            let asksCurrentBalance = lower.contains("how much") || lower.contains("right now") || lower.contains("current balance")

            let wantsExpense = lower.contains("expense") || lower.contains("expenses") || lower.contains("spend") || lower.contains("cost")
            let wantsBalanceWord = lower.contains("balance") || lower.contains("cash")
            let wantsTrendWords = lower.contains("chart") || lower.contains("history") || lower.contains("historical") || lower.contains("trend")
            let wantsBalance = asksCurrentBalance || wantsBalanceWord || (!wantsExpense && wantsTrendWords)

            var emitted = 0

            if wantsExpense {
                var txns = try await loadTransactionsSource(entity: matchedEntity, requestedLimit: 200)
                txns = filterTransactions(txns, window: window)
                let points = expenseSeries(from: txns)
                if !points.isEmpty {
                    let label = matchedEntity?.name ?? "all entities"
                    let card = HistoryChartCardData(
                        title: "Expense history",
                        subtitle: "\(label) • \(window.label)",
                        metricLabel: "Expense",
                        points: points
                    )
                    messages.append(ChatEntry(actor: .assistant, text: "Expense trend", payload: .history(card)))
                    emitted += 1
                }
            }

            if wantsBalance {
                let points: [HistoryChartPoint]
                let subtitle: String
                if let entity = matchedEntity, entity.type.lowercased() == "bank" {
                    var txns = try await api.entityTransactions(baseURL: backendURL, entityID: entity.id)
                    if txns.isEmpty {
                        var globalTxns = try await api.transactions(baseURL: backendURL, limit: 200)
                        globalTxns = sortTransactionsDescending(globalTxns)
                        txns = filterTransactionsByEntityNameHeuristic(globalTxns, entityName: entity.name)
                    }
                    let allPoints = bankBalanceSeriesFromTransactions(txns)
                    points = historyPoints(in: allPoints, window: window, includeLatestPriorToWindow: true)
                    subtitle = "\(entity.name) • \(window.label)"
                } else {
                    let detail = try await api.accountDetail(baseURL: backendURL, accountCode: "1110")
                    let allPoints = bankBalanceSeriesFromAccount(detail)
                    points = historyPoints(in: allPoints, window: window, includeLatestPriorToWindow: true)
                    subtitle = "Account 1110 • \(window.label)"
                }
                if !points.isEmpty {
                    let card = HistoryChartCardData(
                        title: "Bank balance history",
                        subtitle: subtitle,
                        metricLabel: "Balance",
                        points: points
                    )
                    messages.append(ChatEntry(actor: .assistant, text: "Balance trend", payload: .history(card)))
                    if asksCurrentBalance, let last = points.last {
                        append(.assistant, "Current balance is \(CurrencyFormatter.format(last.value)) IRR (as of \(last.label)).")
                    }
                    emitted += 1
                }
            }

            if emitted == 0 {
                append(.assistant, "I could not build a history chart for that filter. Try: \"expense trend last 6 months\" or \"bank balance history for Melli this year\".")
            }
        } catch {
            append(.system, "History lookup failed: \(friendlyNetworkHint(for: error))")
        }
    }

    private func loadEntities() async throws -> [EntityRead] {
        if let t = entitiesCacheTime, Date().timeIntervalSince(t) < 90, !cachedEntities.isEmpty {
            return cachedEntities
        }
        let entities = try await api.entities(baseURL: backendURL)
        cachedEntities = entities
        entitiesCacheTime = Date()
        return entities
    }

    private func loadTransactionsSource(entity: EntityRead?, requestedLimit: Int) async throws -> [BackendTransactionRead] {
        if let entity {
            return try await api.entityTransactions(baseURL: backendURL, entityID: entity.id)
        }
        return try await api.transactions(baseURL: backendURL, limit: min(max(requestedLimit, 1), 200))
    }

    private func matchEntity(in lowerQuery: String, entities: [EntityRead]) -> EntityRead? {
        let hints = typeHints(in: lowerQuery)
        let candidates = entities.filter { lowerQuery.contains($0.name.lowercased()) }
        guard !candidates.isEmpty else { return nil }

        let sortedByNameLength = candidates.sorted { a, b in
            if a.name.count != b.name.count { return a.name.count > b.name.count }
            return a.name < b.name
        }
        if let preferred = sortedByNameLength.first(where: { hints.contains($0.type.lowercased()) }) {
            return preferred
        }
        return sortedByNameLength.first
    }

    private func typeHints(in lowerQuery: String) -> Set<String> {
        var hints = Set<String>()
        if lowerQuery.contains("bank") { hints.insert("bank") }
        if lowerQuery.contains("client") || lowerQuery.contains("customer") { hints.insert("client") }
        if lowerQuery.contains("supplier") || lowerQuery.contains("vendor") { hints.insert("supplier") }
        if lowerQuery.contains("employee") || lowerQuery.contains("staff") || lowerQuery.contains("payee") { hints.insert("employee") }
        return hints
    }

    private func sortTransactionsDescending(_ txns: [BackendTransactionRead]) -> [BackendTransactionRead] {
        txns.sorted { left, right in
            let ld = parseISODate(left.date) ?? .distantPast
            let rd = parseISODate(right.date) ?? .distantPast
            if ld != rd { return ld > rd }
            return left.id.uuidString > right.id.uuidString
        }
    }

    private func filterTransactions(_ txns: [BackendTransactionRead], window: TimeWindow) -> [BackendTransactionRead] {
        txns.filter { txn in
            guard let d = parseISODate(txn.date) else { return true }
            if let start = window.start, d < start { return false }
            if let end = window.end, d > end { return false }
            return true
        }
    }

    private func filterTransactionsByEntityType(_ txns: [BackendTransactionRead], hints: Set<String>) -> [BackendTransactionRead] {
        guard !hints.isEmpty else { return txns }
        return txns.filter { txn in
            guard let links = txn.entity_links else { return false }
            return links.contains { link in
                let role = link.role.lowercased()
                let type = (link.entity_type ?? "").lowercased()
                return hints.contains(role) || hints.contains(type)
            }
        }
    }

    private func filterTransactionsByEntityNameHeuristic(_ txns: [BackendTransactionRead], entityName: String) -> [BackendTransactionRead] {
        let needle = entityName.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !needle.isEmpty else { return txns }
        return txns.filter { txn in
            if (txn.description ?? "").lowercased().contains(needle) {
                return true
            }
            if (txn.reference ?? "").lowercased().contains(needle) {
                return true
            }
            if let links = txn.entity_links {
                if links.contains(where: { ($0.entity_name ?? "").lowercased().contains(needle) }) {
                    return true
                }
            }
            return false
        }
    }

    private func parseLimit(in lowerQuery: String) -> Int? {
        if lowerQuery.contains("all transactions") || lowerQuery.contains("show all") {
            return 100
        }
        let patterns = [
            #"last\s+(\d{1,3})"#,
            #"(\d{1,3})\s+transactions"#,
            #"top\s+(\d{1,3})"#
        ]
        for p in patterns {
            if let first = firstRegexGroup(pattern: p, in: lowerQuery), let value = Int(first) {
                return min(max(value, 1), 100)
            }
        }
        return nil
    }

    private func parseTimeWindow(in lowerQuery: String) -> TimeWindow {
        let now = Date()

        if let groups = firstRegexGroups(pattern: #"from\s+(\d{4}-\d{2}-\d{2})\s+(?:to|until|through)\s+(\d{4}-\d{2}-\d{2})"#, in: lowerQuery),
           groups.count == 2,
           let start = parseISODate(groups[0]),
           let end = parseISODate(groups[1]) {
            return TimeWindow(start: startOfDay(start), end: endOfDay(end), label: "\(groups[0]) to \(groups[1])")
        }
        if let groups = firstRegexGroups(pattern: #"between\s+(\d{4}-\d{2}-\d{2})\s+and\s+(\d{4}-\d{2}-\d{2})"#, in: lowerQuery),
           groups.count == 2,
           let start = parseISODate(groups[0]),
           let end = parseISODate(groups[1]) {
            return TimeWindow(start: startOfDay(start), end: endOfDay(end), label: "\(groups[0]) to \(groups[1])")
        }
        if let groups = firstRegexGroups(pattern: #"past\s+(\d{1,3})\s+(day|days|week|weeks|month|months|year|years)"#, in: lowerQuery),
           groups.count == 2,
           let value = Int(groups[0]) {
            let unit = groups[1]
            let start: Date
            switch unit {
            case "day", "days":
                start = calendar.date(byAdding: .day, value: -value, to: now) ?? now
            case "week", "weeks":
                start = calendar.date(byAdding: .day, value: -(value * 7), to: now) ?? now
            case "month", "months":
                start = calendar.date(byAdding: .month, value: -value, to: now) ?? now
            default:
                start = calendar.date(byAdding: .year, value: -value, to: now) ?? now
            }
            return TimeWindow(start: startOfDay(start), end: endOfDay(now), label: "past \(value) \(unit)")
        }
        if lowerQuery.contains("today") {
            return TimeWindow(start: startOfDay(now), end: endOfDay(now), label: "today")
        }
        if lowerQuery.contains("yesterday"), let yesterday = calendar.date(byAdding: .day, value: -1, to: now) {
            return TimeWindow(start: startOfDay(yesterday), end: endOfDay(yesterday), label: "yesterday")
        }
        if lowerQuery.contains("this week") {
            return TimeWindow(start: startOfWeek(now), end: endOfDay(now), label: "this week")
        }
        if lowerQuery.contains("last week") {
            // Natural-language expectation in chat is usually rolling 7 days.
            let start = calendar.date(byAdding: .day, value: -7, to: now) ?? now
            return TimeWindow(start: startOfDay(start), end: endOfDay(now), label: "last 7 days")
        }
        if lowerQuery.contains("this month") {
            return TimeWindow(start: startOfMonth(now), end: endOfDay(now), label: "this month")
        }
        if lowerQuery.contains("last month"), let lastMonth = calendar.date(byAdding: .month, value: -1, to: now) {
            let start = startOfMonth(lastMonth)
            let end = calendar.date(byAdding: DateComponents(month: 1, day: -1), to: start).map(endOfDay) ?? endOfDay(now)
            return TimeWindow(start: start, end: end, label: "last month")
        }
        if lowerQuery.contains("this year") {
            return TimeWindow(start: startOfYear(now), end: endOfDay(now), label: "this year")
        }
        if lowerQuery.contains("last year"), let lastYear = calendar.date(byAdding: .year, value: -1, to: now) {
            let start = startOfYear(lastYear)
            let end = calendar.date(byAdding: DateComponents(year: 1, day: -1), to: start).map(endOfDay) ?? endOfDay(now)
            return TimeWindow(start: start, end: end, label: "last year")
        }
        return TimeWindow(start: nil, end: nil, label: "all time")
    }

    private func expenseSeries(from txns: [BackendTransactionRead]) -> [HistoryChartPoint] {
        var byMonth: [String: Int] = [:]
        for txn in txns {
            let month = String(txn.date.prefix(7))
            guard month.count == 7 else { continue }
            let expense = (txn.lines ?? []).reduce(0) { partial, line in
                line.account_code.hasPrefix("6") ? (partial + line.debit) : partial
            }
            if expense > 0 {
                byMonth[month, default: 0] += expense
            }
        }
        return byMonth.keys.sorted().map { HistoryChartPoint(label: $0, value: byMonth[$0] ?? 0) }
    }

    private func bankBalanceSeriesFromAccount(_ detail: AccountDetailResponse) -> [HistoryChartPoint] {
        var dailyDelta: [String: Int] = [:]
        for line in detail.lines {
            dailyDelta[line.transaction_date, default: 0] += (line.debit - line.credit)
        }

        var running = 0
        var points: [HistoryChartPoint] = []
        for day in dailyDelta.keys.sorted() {
            running += (dailyDelta[day] ?? 0)
            points.append(HistoryChartPoint(label: day, value: running))
        }
        return points
    }

    private func bankBalanceSeriesFromTransactions(_ txns: [BackendTransactionRead]) -> [HistoryChartPoint] {
        var dailyDelta: [String: Int] = [:]
        for txn in txns {
            let delta = (txn.lines ?? []).reduce(0) { partial, line in
                line.account_code.hasPrefix("111") ? (partial + line.debit - line.credit) : partial
            }
            if delta != 0 {
                dailyDelta[txn.date, default: 0] += delta
            }
        }

        var running = 0
        var points: [HistoryChartPoint] = []
        for day in dailyDelta.keys.sorted() {
            running += (dailyDelta[day] ?? 0)
            points.append(HistoryChartPoint(label: day, value: running))
        }
        return points
    }

    private func historyPoints(in points: [HistoryChartPoint], window: TimeWindow, includeLatestPriorToWindow: Bool) -> [HistoryChartPoint] {
        let filtered = points.filter { point in
            guard let d = parseISODate(point.label) else { return false }
            if let start = window.start, d < start { return false }
            if let end = window.end, d > end { return false }
            return true
        }
        if !filtered.isEmpty {
            return filtered
        }
        guard includeLatestPriorToWindow else { return filtered }
        guard let end = window.end else { return filtered }
        let prior = points.filter { point in
            guard let d = parseISODate(point.label) else { return false }
            return d <= end
        }
        if let latest = prior.last {
            return [latest]
        }
        return filtered
    }

    private func parseISODate(_ raw: String) -> Date? {
        let clean = String(raw.prefix(10))
        return Self.isoDayFormatter.date(from: clean)
    }

    private func startOfDay(_ date: Date) -> Date {
        calendar.startOfDay(for: date)
    }

    private func endOfDay(_ date: Date) -> Date {
        let start = calendar.startOfDay(for: date)
        return calendar.date(byAdding: DateComponents(day: 1, second: -1), to: start) ?? date
    }

    private func startOfWeek(_ date: Date) -> Date {
        let c = calendar.dateComponents([.yearForWeekOfYear, .weekOfYear], from: date)
        return calendar.date(from: c) ?? calendar.startOfDay(for: date)
    }

    private func startOfMonth(_ date: Date) -> Date {
        let c = calendar.dateComponents([.year, .month], from: date)
        return calendar.date(from: c) ?? calendar.startOfDay(for: date)
    }

    private func startOfYear(_ date: Date) -> Date {
        let c = calendar.dateComponents([.year], from: date)
        return calendar.date(from: c) ?? calendar.startOfDay(for: date)
    }

    private func firstRegexGroup(pattern: String, in text: String) -> String? {
        firstRegexGroups(pattern: pattern, in: text)?.first
    }

    private func firstRegexGroups(pattern: String, in text: String) -> [String]? {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else { return nil }
        guard let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) else { return nil }
        var values: [String] = []
        for idx in 1..<match.numberOfRanges {
            let ns = match.range(at: idx)
            guard ns.location != NSNotFound, let range = Range(ns, in: text) else { continue }
            values.append(String(text[range]))
        }
        return values.isEmpty ? nil : values
    }

    private func append(_ actor: ChatActor, _ text: String) {
        messages.append(ChatEntry(actor: actor, text: text))
    }

    private func friendlyNetworkHint(for error: Error) -> String {
        let text = error.localizedDescription.lowercased()
        if text.contains("could not connect") || text.contains("network") || text.contains("server") {
            return "\(error.localizedDescription). Current backend: \(backendURL). Try http://localhost:8000 or LAN-IP:8000."
        }
        return error.localizedDescription
    }

    private static func normalizeBackendURL(_ raw: String) -> String {
        var value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.isEmpty { return AppConfig.defaultBackendURL }
        if value == AppConfig.legacyBackendURL { return AppConfig.defaultBackendURL }
        if !(value.hasPrefix("http://") || value.hasPrefix("https://")) {
            value = "http://" + value
        }
        if value.hasSuffix(":1234") {
            value = String(value.dropLast(":1234".count)) + ":8000"
        }
        return value
    }

    private func isDashboardCommand(_ text: String) -> Bool {
        text.hasPrefix("/dashboard") || text.contains("show dashboard") || text == "dashboard"
    }

    private func isLedgerCommand(_ text: String) -> Bool {
        text.hasPrefix("/ledger") || text.contains("show ledger") || text == "ledger"
    }

    private func isMissingRefCommand(_ text: String) -> Bool {
        text.hasPrefix("/missing") || text.contains("missing reference")
    }

    private func isInvoicesCommand(_ text: String) -> Bool {
        text.hasPrefix("/invoices") || text.contains("show invoices") || text == "invoices"
    }

    private func isTransactionQueryCommand(_ text: String) -> Bool {
        if text.hasPrefix("/transactions") || text.hasPrefix("/tx") {
            return true
        }
        let hasSubject =
            text.contains("transaction") ||
            text.contains("transactions") ||
            text.contains("transact") ||
            text.contains("voucher") ||
            text.contains("entry") ||
            text.contains("entries")
        let hasVerb =
            text.contains("show") ||
            text.contains("list") ||
            text.contains("get") ||
            text.contains("find") ||
            text.contains("recent") ||
            text.contains("last")
        let hasEntityHint =
            text.contains("bank") ||
            text.contains("client") ||
            text.contains("customer") ||
            text.contains("supplier") ||
            text.contains("vendor") ||
            text.contains("employee") ||
            text.contains("payee")
        let hasTimeHint =
            text.contains("last week") ||
            text.contains("this week") ||
            text.contains("last month") ||
            text.contains("this month") ||
            text.contains("last year") ||
            text.contains("this year") ||
            text.contains("today") ||
            text.contains("yesterday") ||
            text.contains("past ")
        return (hasSubject && hasVerb) || (hasVerb && hasEntityHint && hasTimeHint)
    }

    private func isHistoryQueryCommand(_ text: String) -> Bool {
        if text.hasPrefix("/history") || text.hasPrefix("/chart") {
            return true
        }
        let asksBalanceQuestion =
            (text.contains("how much") || text.contains("tell me")) &&
            (text.contains("money") || text.contains("balance") || text.contains("cash")) &&
            (text.contains("bank") || text.contains("account"))
        if asksBalanceQuestion {
            return true
        }
        let markers = [
            "history",
            "historical",
            "trend",
            "chart",
            "balance",
            "bank account balance",
            "current balance",
            "expense trend",
            "expense history",
            "spend trend",
            "cash trend",
            "bank balance",
        ]
        return markers.contains { text.contains($0) }
    }

    private func isSaveCommand(_ text: String) -> Bool {
        text.hasPrefix("/save") || text.contains("save voucher") || text == "save"
    }

    private func isBackendFallbackMessage(_ message: String) -> Bool {
        let m = message.lowercased()
        return m.contains("i didn't understand") ||
            m.contains("please say again") ||
            m.contains("could not understand")
    }
}
