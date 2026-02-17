import SwiftUI

struct WatchChatRootView: View {
    @StateObject private var vm = ChatViewModel()

    var body: some View {
        VStack(spacing: 8) {
            HStack {
                Text("Assistant")
                    .font(.headline)
                Spacer()
                if vm.isWorking {
                    ProgressView()
                        .controlSize(.small)
                }
            }

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 6) {
                    ForEach(vm.messages.suffix(14)) { entry in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(entry.text)
                                .font(.caption2)
                                .foregroundStyle(entry.actor == .user ? Color(hex: 0x0F766E) : .primary)
                            if let payload = entry.payload {
                                watchPayload(payload)
                            }
                        }
                        .padding(6)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.white.opacity(0.12), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    }
                }
            }

            HStack(spacing: 6) {
                Button("Dash") {
                    Task { await vm.send(text: "/dashboard") }
                }
                .buttonStyle(.bordered)
                .font(.caption2)
                Button("Save") {
                    Task { await vm.savePendingDraft() }
                }
                .buttonStyle(.bordered)
                .font(.caption2)
            }

            HStack(spacing: 6) {
                TextField("Message", text: $vm.composerText)
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 5)
                    .background(Color.white.opacity(0.15), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .onSubmit {
                        Task { await vm.sendComposerText() }
                    }
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
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                Button {
                    Task { await vm.sendComposerText() }
                } label: {
                    Image(systemName: "paperplane.fill")
                        .font(.caption)
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(8)
    }

    @ViewBuilder
    private func watchPayload(_ payload: ChatCardPayload) -> some View {
        switch payload {
        case .dashboard(let data):
            Text("KPI \(data.kpis.count), Forecast \(data.forecast_13_weeks.count)w")
                .font(.caption2)
                .foregroundStyle(.secondary)
        case .ledger(let data):
            Text("Ledger rows: \(data.rows.count)")
                .font(.caption2)
                .foregroundStyle(.secondary)
        case .invoices(let list):
            Text("Invoices: \(list.count)")
                .font(.caption2)
                .foregroundStyle(.secondary)
        case .missingReferences(let list):
            Text("Missing refs: \(list.count)")
                .font(.caption2)
                .foregroundStyle(.secondary)
        case .draft(let draft):
            VStack(alignment: .leading, spacing: 2) {
                Text("Draft ready")
                    .font(.caption2.weight(.semibold))
                Text(draft.suggestion.description ?? "")
                    .font(.caption2)
                    .lineLimit(2)
            }
        case .transactions(let card):
            Text("Transactions: \(card.items.count)")
                .font(.caption2)
                .foregroundStyle(.secondary)
        case .history(let chart):
            Text("\(chart.title): \(chart.points.count) points")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }
}
