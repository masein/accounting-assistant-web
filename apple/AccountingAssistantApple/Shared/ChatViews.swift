import SwiftUI
import Charts
import UniformTypeIdentifiers
#if canImport(PhotosUI)
import PhotosUI
#endif

enum AppSurfaceStyle {
    static let backgroundA = Color(hex: 0x0A3D62)
    static let backgroundB = Color(hex: 0x0E7490)
    static let backgroundC = Color(hex: 0x2F6F9E)
    static let panel = Color.white.opacity(0.16)
    static let bubbleUser = Color(hex: 0x0F766E)
    static let bubbleAssistant = Color(hex: 0xF8FAFC, alpha: 0.98)
    static let chipBackground = Color(hex: 0x0B4B66, alpha: 0.72)
    static let inputBackground = Color.white
    static let textPrimary = Color(hex: 0x0F172A)
    static let textSecondary = Color(hex: 0x475569)
    static let chartGrid = Color(hex: 0xCBD5E1)
    static let chartAxis = Color(hex: 0x334155)
}

struct AnimatedBackdrop: View {
    let compact: Bool
    @State private var phase: Double = 0

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [AppSurfaceStyle.backgroundA, AppSurfaceStyle.backgroundB, AppSurfaceStyle.backgroundC],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            if !compact {
                TimelineView(.animation(minimumInterval: 1.0 / 24.0, paused: false)) { timeline in
                    let t = timeline.date.timeIntervalSinceReferenceDate
                    ZStack {
                        Circle()
                            .fill(Color.white.opacity(0.13))
                            .frame(width: 320, height: 320)
                            .offset(x: cos(t * 0.21) * 130, y: sin(t * 0.27) * 180)
                            .blur(radius: 1)
                        Circle()
                            .fill(Color(hex: 0x5EEAD4).opacity(0.18))
                            .frame(width: 260, height: 260)
                            .offset(x: sin(t * 0.18) * -150, y: cos(t * 0.23) * -170)
                            .blur(radius: 2)
                        Circle()
                            .fill(Color(hex: 0xFDE68A).opacity(0.10))
                            .frame(width: 220, height: 220)
                            .offset(x: sin(t * 0.25) * 180, y: cos(t * 0.16) * 160)
                    }
                    .animation(.easeInOut(duration: 0.7), value: t)
                }
            }
        }
    }
}

struct AppleChatRootView: View {
    @StateObject private var vm = ChatViewModel()
    @State private var showFileImporter = false
    #if os(iOS)
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    #endif

    #if canImport(PhotosUI)
    @State private var selectedPhotoItem: PhotosPickerItem?
    #endif
    
    private var isCompact: Bool {
        #if os(iOS)
        return horizontalSizeClass == .compact
        #else
        return false
        #endif
    }

    var body: some View {
        Group {
            #if os(iOS)
            iosBody
            #else
            desktopBody
            #endif
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .onChange(of: vm.voice.transcript) { _, newValue in
            if !newValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                vm.composerText = newValue
            }
        }
        .onChange(of: vm.voice.lastError) { _, newValue in
            guard let newValue, !newValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
            vm.messages.append(ChatEntry(actor: .system, text: "Voice error: \(newValue)"))
        }
        .fileImporter(
            isPresented: $showFileImporter,
            allowedContentTypes: [.image, .pdf],
            allowsMultipleSelection: false
        ) { result in
            switch result {
            case .success(let urls):
                guard let url = urls.first else { return }
                Task {
                    await importFromFileURL(url)
                }
            case .failure:
                break
            }
        }
    }

    private var desktopBody: some View {
        ZStack {
            AnimatedBackdrop(compact: isCompact)

            VStack(spacing: isCompact ? 9 : 12) {
                header
                quickActions
                attachmentStrip
                chatScroller
                composer
            }
            .padding(.horizontal, isCompact ? 10 : 16)
            .padding(.vertical, isCompact ? 8 : 12)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
    }

    #if os(iOS)
    private var iosBody: some View {
        GeometryReader { proxy in
            ZStack(alignment: .top) {
                AnimatedBackdrop(compact: true)
                VStack(spacing: 10) {
                    iosTopCard
                    attachmentStrip
                    chatScroller
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                .padding(.horizontal, 10)
                .padding(.top, max(proxy.safeAreaInsets.top, 10))
                .padding(.bottom, 8)
                .frame(width: proxy.size.width, height: proxy.size.height, alignment: .top)
            }
            .frame(width: proxy.size.width, height: proxy.size.height, alignment: .top)
        }
        .ignoresSafeArea()
        .safeAreaInset(edge: .bottom, spacing: 0) {
            composer
                .padding(.horizontal, 8)
                .padding(.top, 6)
                .padding(.bottom, 8)
                .background(.ultraThinMaterial)
        }
    }

    private var iosTopCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: "message.and.waveform.fill")
                    .font(.headline.weight(.semibold))
                    .foregroundStyle(Color.white)
                    .padding(8)
                    .background(Color.white.opacity(0.18), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                VStack(alignment: .leading, spacing: 1) {
                    Text("Accounting Assistant")
                        .font(.title3.weight(.bold))
                        .foregroundStyle(Color.white)
                    Text("Text + Image + Voice")
                        .font(.caption)
                        .foregroundStyle(Color.white.opacity(0.88))
                }
                Spacer(minLength: 0)
                if vm.isWorking {
                    ProgressView()
                        .tint(.white)
                }
            }
            HStack(spacing: 8) {
                Image(systemName: "network")
                    .font(.caption)
                    .foregroundStyle(Color.white.opacity(0.9))
                TextField("Backend URL", text: $vm.backendURL)
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(Color.white, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .foregroundStyle(AppSurfaceStyle.textPrimary)
            }
            iosQuickActions
        }
        .padding(12)
        .background(Color.black.opacity(0.16), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(Color.white.opacity(0.14), lineWidth: 1)
        )
    }

    private var iosQuickActions: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                QuickChip(label: "Dashboard", icon: "chart.xyaxis.line") {
                    Task { await vm.send(text: "/dashboard") }
                }
                QuickChip(label: "Ledger", icon: "chart.bar.doc.horizontal") {
                    Task { await vm.send(text: "/ledger") }
                }
                QuickChip(label: "Balance", icon: "banknote") {
                    Task { await vm.send(text: "what is my current balance") }
                }
                QuickChip(label: "Last 10 txns", icon: "clock.arrow.circlepath") {
                    Task { await vm.send(text: "show last 10 transactions") }
                }
                QuickChip(label: "Invoices", icon: "doc.text.image") {
                    Task { await vm.send(text: "/invoices") }
                }
            }
            .padding(.vertical, 2)
            .padding(.horizontal, 1)
        }
    }
    #endif

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: "message.and.waveform.fill")
                    .font((isCompact ? Font.headline : Font.title3).weight(.semibold))
                    .foregroundStyle(Color.white)
                    .padding(isCompact ? 8 : 10)
                    .background(Color.white.opacity(0.18), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Accounting Assistant")
                        .font((isCompact ? Font.title3 : Font.title2).weight(.bold))
                        .foregroundStyle(Color.white)
                    Text("Text + Image + Voice + Charts")
                        .font(isCompact ? .caption : .subheadline)
                        .foregroundStyle(Color.white.opacity(0.86))
                }
                Spacer(minLength: 0)
                if vm.isWorking {
                    ProgressView()
                        .tint(.white)
                }
            }

            HStack(spacing: 8) {
                Text("Backend")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.white.opacity(0.82))
                TextField("Backend URL", text: $vm.backendURL)
                    .textFieldStyle(.plain)
                    .padding(.horizontal, isCompact ? 8 : 10)
                    .padding(.vertical, isCompact ? 7 : 8)
                    .background(AppSurfaceStyle.inputBackground, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .foregroundStyle(AppSurfaceStyle.textPrimary)
            }
        }
    }

    private var quickActions: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                QuickChip(label: "Dashboard", icon: "chart.xyaxis.line") {
                    Task { await vm.send(text: "/dashboard") }
                }
                QuickChip(label: "Ledger", icon: "chart.bar.doc.horizontal") {
                    Task { await vm.send(text: "/ledger") }
                }
                if isCompact {
                    QuickChip(label: "Balance", icon: "banknote") {
                        Task { await vm.send(text: "what is my current balance") }
                    }
                    QuickChip(label: "Last 10 txns", icon: "clock.arrow.circlepath") {
                        Task { await vm.send(text: "show last 10 transactions") }
                    }
                } else {
                    QuickChip(label: "Invoices", icon: "doc.text.image") {
                        Task { await vm.send(text: "/invoices") }
                    }
                    QuickChip(label: "Missing refs", icon: "exclamationmark.triangle") {
                        Task { await vm.send(text: "/missing") }
                    }
                    QuickChip(label: "Last 10 txns", icon: "clock.arrow.circlepath") {
                        Task { await vm.send(text: "show last 10 transactions") }
                    }
                    QuickChip(label: "Expense trend", icon: "chart.line.uptrend.xyaxis") {
                        Task { await vm.send(text: "show expense trend past 6 months") }
                    }
                    QuickChip(label: "Save draft", icon: "checkmark.seal") {
                        Task { await vm.savePendingDraft() }
                    }
                }
            }
            .padding(.vertical, 2)
        }
    }

    @ViewBuilder
    private var attachmentStrip: some View {
        if !vm.uploadedAttachments.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(vm.uploadedAttachments) { item in
                        HStack(spacing: 6) {
                            Image(systemName: item.content_type.contains("pdf") ? "doc.richtext" : "photo")
                            Text(item.file_name)
                                .lineLimit(1)
                                .font(.caption)
                            Button {
                                vm.removeAttachment(item)
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .foregroundStyle(.white.opacity(0.8))
                            }
                            .buttonStyle(.plain)
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 7)
                        .background(Color.white.opacity(0.18), in: Capsule())
                        .foregroundStyle(.white)
                    }
                }
            }
        }
    }

    private var chatScroller: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 10) {
                    ForEach(vm.messages) { entry in
                        messageRow(entry)
                            .id(entry.id)
                            .transition(.move(edge: .bottom).combined(with: .opacity))
                    }
                }
                .padding(.vertical, 8)
            }
            .background(AppSurfaceStyle.panel, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
            .onChange(of: vm.messages.count) { _, _ in
                if let last = vm.messages.last?.id {
                    withAnimation(.spring(response: 0.35, dampingFraction: 0.88)) {
                        proxy.scrollTo(last, anchor: .bottom)
                    }
                }
            }
        }
    }

    private func messageRow(_ entry: ChatEntry) -> some View {
        HStack {
            if entry.actor == .user { Spacer(minLength: isCompact ? 10 : 36) }
            VStack(alignment: .leading, spacing: 8) {
                Text(entry.text)
                    .font(.body)
                    .foregroundStyle(entry.actor == .user ? Color.white : Color(hex: 0x0F172A))
                    .frame(maxWidth: .infinity, alignment: .leading)

                if let payload = entry.payload {
                    payloadView(payload)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(entry.actor == .user ? AppSurfaceStyle.bubbleUser : AppSurfaceStyle.bubbleAssistant)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.white.opacity(entry.actor == .user ? 0.14 : 0.35), lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.09), radius: 10, y: 4)
            .frame(maxWidth: isCompact ? 360 : 640, alignment: .leading)

            if entry.actor != .user { Spacer(minLength: isCompact ? 10 : 36) }
        }
        .padding(.horizontal, isCompact ? 2 : 10)
    }

    @ViewBuilder
    private func payloadView(_ payload: ChatCardPayload) -> some View {
        switch payload {
        case .draft(let draft):
            DraftVoucherCard(draft: draft) {
                Task { await vm.savePendingDraft() }
            }
        case .dashboard(let data):
            DashboardChartCard(data: data)
        case .ledger(let data):
            LedgerChartCard(data: data)
        case .invoices(let list):
            InvoiceListCard(invoices: list)
        case .missingReferences(let list):
            MissingReferencesCard(items: list)
        case .transactions(let card):
            TransactionListCard(card: card)
        case .history(let chart):
            HistorySeriesCard(chart: chart)
        }
    }

    private var composer: some View {
        let iconSize: CGFloat = isCompact ? 30 : 34
        let sendSize: CGFloat = isCompact ? 34 : 38
        return HStack(alignment: .bottom, spacing: 8) {
            TextField("Message the assistant…", text: $vm.composerText)
                .textFieldStyle(.plain)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(AppSurfaceStyle.inputBackground, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .foregroundStyle(AppSurfaceStyle.textPrimary)
                .frame(maxWidth: .infinity)
                .submitLabel(.send)
                .onSubmit {
                    Task { await vm.sendComposerText() }
                }

            HStack(spacing: 6) {
                #if canImport(PhotosUI)
                if #available(iOS 16.0, macOS 13.0, *), !isCompact {
                    PhotosPicker(selection: $selectedPhotoItem, matching: .any(of: [.images, .not(.livePhotos)]), photoLibrary: .shared()) {
                        Image(systemName: "photo")
                            .font(.headline)
                            .frame(width: iconSize, height: iconSize)
                    }
                    .buttonStyle(.plain)
                    .background(Color.white.opacity(0.18), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .onChange(of: selectedPhotoItem) { _, item in
                        guard let item else { return }
                        Task {
                            await importFromPhotosPicker(item)
                        }
                    }
                }
                #endif

                Button {
                    showFileImporter = true
                } label: {
                    Image(systemName: "paperclip")
                        .font(.headline)
                        .frame(width: iconSize, height: iconSize)
                }
                .buttonStyle(.plain)
                .background(Color.white.opacity(0.18), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .foregroundStyle(.white)

                Button {
                    Task {
                        if vm.voice.isRecording {
                            vm.voice.stop()
                            vm.applyVoiceTranscript()
                        } else {
                            await vm.voice.start()
                        }
                    }
                } label: {
                    Image(systemName: vm.voice.isRecording ? "stop.circle.fill" : "mic.fill")
                        .font(.headline)
                        .frame(width: iconSize, height: iconSize)
                }
                .buttonStyle(.plain)
                .background((vm.voice.isRecording ? Color.red.opacity(0.82) : Color.white.opacity(0.18)), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .foregroundStyle(.white)

                Button {
                    Task { await vm.sendComposerText() }
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title3)
                        .frame(width: sendSize, height: sendSize)
                        .foregroundStyle(Color.white)
                        .background(Color(hex: 0x14B8A6), in: Circle())
                        .shadow(color: Color.black.opacity(0.2), radius: 8, y: 4)
                }
                .buttonStyle(.plain)
                .disabled(vm.composerText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(isCompact ? 8 : 10)
        .background(AppSurfaceStyle.panel, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    #if canImport(PhotosUI)
    @available(iOS 16.0, macOS 13.0, *)
    private func importFromPhotosPicker(_ item: PhotosPickerItem) async {
        guard let data = try? await item.loadTransferable(type: Data.self) else {
            return
        }
        let name = "photo-\(Int(Date().timeIntervalSince1970)).jpg"
        await vm.uploadAttachment(data: data, filename: name, mimeType: "image/jpeg")
    }
    #endif

    private func importFromFileURL(_ url: URL) async {
        let hasScopedAccess = url.startAccessingSecurityScopedResource()
        defer {
            if hasScopedAccess { url.stopAccessingSecurityScopedResource() }
        }

        do {
            let data = try Data(contentsOf: url)
            let ext = url.pathExtension.lowercased()
            let mime: String
            switch ext {
            case "jpg", "jpeg": mime = "image/jpeg"
            case "png": mime = "image/png"
            case "webp": mime = "image/webp"
            case "pdf": mime = "application/pdf"
            default: mime = "application/octet-stream"
            }
            await vm.uploadAttachment(data: data, filename: url.lastPathComponent, mimeType: mime)
        } catch {
            vm.messages.append(ChatEntry(actor: .system, text: "File read failed: \(error.localizedDescription)"))
        }
    }
}

struct QuickChip: View {
    let label: String
    let icon: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Image(systemName: icon)
                    .font(.caption.weight(.semibold))
                Text(label)
                    .font(.caption.weight(.semibold))
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(AppSurfaceStyle.chipBackground, in: Capsule())
            .foregroundStyle(.white)
        }
        .buttonStyle(.plain)
    }
}

struct DraftVoucherCard: View {
    let draft: TransactionDraft
    let onSave: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Draft voucher")
                .font(.headline)
            Text(draft.suggestion.description ?? "No description")
                .font(.subheadline)
                .foregroundStyle(AppSurfaceStyle.textSecondary)

            ForEach(Array(draft.suggestion.lines.enumerated()), id: \.offset) { _, line in
                HStack {
                    Text(line.account_code)
                        .font(.caption.weight(.semibold))
                    Spacer()
                    Text("D \(CurrencyFormatter.format(line.debit))")
                        .font(.caption.monospacedDigit())
                    Text("C \(CurrencyFormatter.format(line.credit))")
                        .font(.caption.monospacedDigit())
                }
            }

            Button("Save voucher", action: onSave)
                .buttonStyle(.borderedProminent)
                .tint(Color(hex: 0x0F766E))
        }
        .foregroundStyle(AppSurfaceStyle.textPrimary)
        .padding(10)
        .background(AppSurfaceStyle.inputBackground, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct DashboardChartCard: View {
    let data: OwnerDashboardResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Business snapshot")
                .font(.headline)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 8)], spacing: 8) {
                ForEach(Array(data.kpis.prefix(4))) { kpi in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(kpi.label)
                            .font(.caption)
                            .foregroundStyle(Color(hex: 0x334155))
                        Text(CurrencyFormatter.compact(kpi.value))
                            .font(.headline.monospacedDigit())
                            .foregroundStyle(Color(hex: 0x0F172A))
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
                    .background(Color(hex: 0xECFEFF), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                }
            }

            if !data.monthly_expense_series.isEmpty {
                Chart(data.monthly_expense_series) { point in
                    LineMark(
                        x: .value("Period", point.period),
                        y: .value("Expense", point.value)
                    )
                    .foregroundStyle(Color(hex: 0x0F766E))
                    .interpolationMethod(.catmullRom)
                    AreaMark(
                        x: .value("Period", point.period),
                        y: .value("Expense", point.value)
                    )
                    .foregroundStyle(Color(hex: 0x5EEAD4).opacity(0.22))
                    PointMark(
                        x: .value("Period", point.period),
                        y: .value("Expense", point.value)
                    )
                    .symbolSize(64)
                    .foregroundStyle(Color(hex: 0x0F766E))
                }
                .chartLegend(.hidden)
                .chartPlotStyle { plot in
                    plot.background(Color(hex: 0xF8FAFC))
                }
                .chartXAxis {
                    AxisMarks { _ in
                        AxisGridLine(stroke: StrokeStyle(lineWidth: 0.6))
                            .foregroundStyle(AppSurfaceStyle.chartGrid)
                        AxisTick()
                            .foregroundStyle(AppSurfaceStyle.chartAxis)
                        AxisValueLabel()
                            .font(.caption2)
                            .foregroundStyle(AppSurfaceStyle.chartAxis)
                    }
                }
                .chartYAxis {
                    AxisMarks(position: .leading) { _ in
                        AxisGridLine(stroke: StrokeStyle(lineWidth: 0.6))
                            .foregroundStyle(AppSurfaceStyle.chartGrid)
                        AxisTick()
                            .foregroundStyle(AppSurfaceStyle.chartAxis)
                        AxisValueLabel()
                            .font(.caption2)
                            .foregroundStyle(AppSurfaceStyle.chartAxis)
                    }
                }
                .frame(height: 140)
            }
        }
        .foregroundStyle(Color(hex: 0x0F172A))
        .padding(10)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct LedgerChartCard: View {
    let data: LedgerSummaryResponse

    var topRows: [LedgerRow] {
        data.rows
            .sorted { ($0.debit_turnover + $0.credit_turnover) > ($1.debit_turnover + $1.credit_turnover) }
            .prefix(7)
            .map { $0 }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Ledger movement")
                .font(.headline)
            Chart(topRows) { row in
                BarMark(
                    x: .value("Account", row.account_code),
                    y: .value("Turnover", row.debit_turnover + row.credit_turnover)
                )
                .foregroundStyle(Color(hex: 0x14B8A6).gradient)
            }
            .chartLegend(.hidden)
            .chartPlotStyle { plot in
                plot.background(Color(hex: 0xF8FAFC))
            }
            .chartXAxis {
                AxisMarks { _ in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 0.6))
                        .foregroundStyle(AppSurfaceStyle.chartGrid)
                    AxisTick()
                        .foregroundStyle(AppSurfaceStyle.chartAxis)
                    AxisValueLabel()
                        .font(.caption2)
                        .foregroundStyle(AppSurfaceStyle.chartAxis)
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading) { _ in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 0.6))
                        .foregroundStyle(AppSurfaceStyle.chartGrid)
                    AxisTick()
                        .foregroundStyle(AppSurfaceStyle.chartAxis)
                    AxisValueLabel()
                        .font(.caption2)
                        .foregroundStyle(AppSurfaceStyle.chartAxis)
                }
            }
            .frame(height: 130)
        }
        .foregroundStyle(Color(hex: 0x0F172A))
        .padding(10)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct InvoiceListCard: View {
    let invoices: [InvoiceItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Invoices")
                .font(.headline)
            ForEach(Array(invoices.prefix(6))) { inv in
                HStack {
                    Text(inv.number)
                    Spacer()
                    Text("\(CurrencyFormatter.format(inv.amount)) \(inv.currency)")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(AppSurfaceStyle.textSecondary)
                }
                .font(.caption)
                .padding(.vertical, 3)
            }
            if invoices.isEmpty {
                Text("No invoices yet")
                    .font(.caption)
                    .foregroundStyle(AppSurfaceStyle.textSecondary)
            }
        }
        .foregroundStyle(Color(hex: 0x0F172A))
        .padding(10)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct MissingReferencesCard: View {
    let items: [MissingReferenceItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Missing references")
                .font(.headline)
            Text("\(items.count) items")
                .font(.caption)
                .foregroundStyle(AppSurfaceStyle.textSecondary)

            ForEach(Array(items.prefix(6))) { item in
                Text("• \(item.date) — \((item.description ?? "No description"))")
                    .font(.caption)
                    .lineLimit(2)
            }
        }
        .foregroundStyle(Color(hex: 0x0F172A))
        .padding(10)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct TransactionListCard: View {
    let card: TransactionQueryCard

    private func lineAmount(_ txn: BackendTransactionRead) -> Int {
        let debit = (txn.lines ?? []).reduce(0) { $0 + $1.debit }
        let credit = (txn.lines ?? []).reduce(0) { $0 + $1.credit }
        return max(debit, credit)
    }

    private func linkSummary(_ txn: BackendTransactionRead) -> String {
        let names = (txn.entity_links ?? []).compactMap { $0.entity_name }.prefix(2)
        if names.isEmpty { return "No linked entity" }
        return names.joined(separator: ", ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(card.title)
                .font(.headline)
            Text(card.subtitle)
                .font(.caption)
                .foregroundStyle(AppSurfaceStyle.textSecondary)

            if card.items.isEmpty {
                Text("No transactions found.")
                    .font(.caption)
                    .foregroundStyle(AppSurfaceStyle.textSecondary)
            } else {
                ForEach(card.items, id: \.id) { txn in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(txn.date)
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(AppSurfaceStyle.textSecondary)
                            Spacer()
                            Text(CurrencyFormatter.format(lineAmount(txn)))
                                .font(.caption.monospacedDigit())
                        }
                        Text(txn.description ?? "No description")
                            .font(.subheadline)
                        HStack(spacing: 8) {
                            Text(txn.reference?.isEmpty == false ? "Ref: \(txn.reference ?? "")" : "Ref: -")
                                .font(.caption)
                                .foregroundStyle(AppSurfaceStyle.textSecondary)
                            Text("•")
                                .font(.caption)
                                .foregroundStyle(AppSurfaceStyle.textSecondary)
                            Text(linkSummary(txn))
                                .font(.caption)
                                .foregroundStyle(AppSurfaceStyle.textSecondary)
                        }
                    }
                    .padding(.vertical, 4)
                    if txn.id != card.items.last?.id {
                        Divider()
                    }
                }
            }
        }
        .foregroundStyle(Color(hex: 0x0F172A))
        .padding(10)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct HistorySeriesCard: View {
    let chart: HistoryChartCardData

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(chart.title)
                .font(.headline)
            Text(chart.subtitle)
                .font(.caption)
                .foregroundStyle(AppSurfaceStyle.textSecondary)

            if chart.points.isEmpty {
                Text("No data points for this period.")
                    .font(.caption)
                    .foregroundStyle(AppSurfaceStyle.textSecondary)
            } else {
                if chart.points.count == 1, let only = chart.points.first {
                    HStack(alignment: .firstTextBaseline) {
                        Text("Latest")
                            .font(.caption)
                            .foregroundStyle(AppSurfaceStyle.textSecondary)
                        Spacer()
                        Text("\(CurrencyFormatter.format(only.value)) IRR")
                            .font(.headline.monospacedDigit())
                            .foregroundStyle(Color(hex: 0x0F172A))
                    }
                    .padding(.horizontal, 4)
                }
                Chart(chart.points) { point in
                    LineMark(
                        x: .value("Period", point.label),
                        y: .value(chart.metricLabel, point.value)
                    )
                    .foregroundStyle(Color(hex: 0x0F766E))
                    .interpolationMethod(.catmullRom)

                    AreaMark(
                        x: .value("Period", point.label),
                        y: .value(chart.metricLabel, point.value)
                    )
                    .foregroundStyle(Color(hex: 0x5EEAD4).opacity(0.22))
                    PointMark(
                        x: .value("Period", point.label),
                        y: .value(chart.metricLabel, point.value)
                    )
                    .symbolSize(70)
                    .foregroundStyle(Color(hex: 0x0F766E))
                }
                .chartLegend(.hidden)
                .chartPlotStyle { plot in
                    plot.background(Color(hex: 0xF8FAFC))
                }
                .chartXAxis {
                    AxisMarks(values: .automatic(desiredCount: min(7, max(3, chart.points.count)))) { _ in
                        AxisGridLine(stroke: StrokeStyle(lineWidth: 0.6))
                            .foregroundStyle(AppSurfaceStyle.chartGrid)
                        AxisTick()
                            .foregroundStyle(AppSurfaceStyle.chartAxis)
                        AxisValueLabel()
                            .font(.caption2)
                            .foregroundStyle(AppSurfaceStyle.chartAxis)
                    }
                }
                .chartYAxis {
                    AxisMarks(position: .leading) { _ in
                        AxisGridLine(stroke: StrokeStyle(lineWidth: 0.6))
                            .foregroundStyle(AppSurfaceStyle.chartGrid)
                        AxisTick()
                            .foregroundStyle(AppSurfaceStyle.chartAxis)
                        AxisValueLabel()
                            .font(.caption2)
                            .foregroundStyle(AppSurfaceStyle.chartAxis)
                    }
                }
                .frame(height: 160)
            }
        }
        .foregroundStyle(Color(hex: 0x0F172A))
        .padding(10)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}
