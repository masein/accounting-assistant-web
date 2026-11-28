import Charts
import SwiftUI

/// What a card's buttons do: the conversation's, or the approvals list's (both are the chat's).
struct CardActions {
    var confirm: (String) -> Void = { _ in }
    var cancel: (String) -> Void = { _ in }
    var undo: (String) -> Void = { _ in }
    var approve: (String) -> Void = { _ in }
    /// asks for the reason first (ConversationView's prompt)
    var reject: (String) -> Void = { _ in }
    var withdraw: (String) -> Void = { _ in }
    var openFile: (FileRef) -> Void = { _ in }

    @MainActor static func of(_ chat: ChatModel, prompts: CardPrompts) -> CardActions {
        CardActions(
            confirm: { token in Task { await chat.confirm(token) } },
            cancel: { token in Task { await chat.cancel(token) } },
            undo: { id in Task { await chat.undo(id) } },
            approve: { token in Task { await chat.approve(token) } },
            reject: { token in prompts.rejecting = token; prompts.note = "" },
            withdraw: { token in Task { await chat.withdraw(token) } },
            openFile: { file in Task { if let url = await chat.document(file) { prompts.preview = url } } })
    }
}

/// The prompts a card opens: the reason for a rejection, a document's preview (an object rather than `@State`).
final class CardPrompts: ObservableObject {
    @Published var rejecting: String?
    @Published var note = ""
    @Published var preview: URL?
}

/// One block of an answer (docs/contracts/mobile-blocks.schema.json): text, a proposal or approval card, "recorded"
/// with its undo, a figure, a table, a chart, a review, a document — and any other kind as its own sentence.
struct BlockView: View {
    let block: Block
    @ObservedObject var chat: ChatModel
    let l10n: L10n
    let actions: CardActions
    /// a "recorded" note right under its own card: the card says where it stands
    var underCard = false

    var body: some View {
        switch block {
        case .text(_, let text):
            AssistantBubble(text: text)
        case .proposal(_, let p), .approval(_, let p):
            ProposalCard(proposal: p, l10n: l10n, working: chat.working.contains(p.token), actions: actions)
        case .posted(_, let p, let fallback):
            VStack(alignment: .leading, spacing: 8) {
                PostedNote(posted: p, fallback: fallback, l10n: l10n,
                           working: p.auditLogId.map { chat.working.contains($0) } ?? false, underCard: underCard,
                           onUndo: { if let id = p.auditLogId { actions.undo(id) } })
                if let file = p.file, file.isPhoneDocument {
                    FileCard(file: file, chat: chat, l10n: l10n, onOpen: { actions.openFile(file) })
                }
            }
        case .figure(_, let f, _):
            FigureCard(figure: f, l10n: l10n)
        case .table(_, let t, let fallback):
            if t.rows.isEmpty { AssistantBubble(text: fallback) } else { TableCard(table: t, l10n: l10n) }
        case .chart(_, let c, let fallback):
            ChartCard(chart: c, fallback: fallback)
        case .review(_, let r, _):
            ReviewCard(review: r)
        case .intake(_, let i, _):
            IntakeCard(intake: i)
        case .file(_, let f, _):
            FileCard(file: f, chat: chat, l10n: l10n, onOpen: { actions.openFile(f) })
        case .other(_, _, let fallback):
            AssistantBubble(text: fallback)
        }
    }
}

struct PostedNote: View {
    let posted: Posted
    let fallback: String
    let l10n: L10n
    let working: Bool
    let underCard: Bool
    let onUndo: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            if posted.undone == true {
                // under its card, the card says it was undone
                if !underCard {
                    Label(l10n[.undone], systemImage: "arrow.uturn.backward.circle").foregroundStyle(.secondary)
                }
            } else {
                // the card above already says "Recorded": this line is the voucher, the date and the undo
                let detail = [posted.voucher, posted.date?.display].compactMap { $0 }.joined(separator: " · ")
                if !detail.isEmpty {
                    Label(detail, systemImage: "doc.text").foregroundStyle(.secondary)
                } else if !underCard {
                    Label(l10n[.posted], systemImage: "checkmark.circle.fill").foregroundStyle(Theme.primary)
                }
                if posted.canUndo {
                    Button(l10n[.undo], action: onUndo).buttonStyle(.borderless).disabled(working)
                        .accessibilityIdentifier("undo")
                }
                if working { ProgressView().controlSize(.small) }
            }
        }
        .font(.callout)
        .accessibilityIdentifier("posted")
    }
}

struct FigureCard: View {
    let figure: Figure
    let l10n: L10n

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(figure.label).font(.subheadline).foregroundStyle(.secondary)
            Text(Format.money(Money(value: figure.value, currency: figure.currency), l10n: l10n))
                .font(.title2.bold().monospacedDigit())
            if let asOf = figure.asOf {
                Text(l10n(.asOf, asOf)).font(.caption).foregroundStyle(.secondary)
            }
            ForEach(Array((figure.breakdown ?? []).enumerated()), id: \.offset) { _, part in
                HStack {
                    Text(part.label ?? "—").font(.subheadline)
                    Spacer()
                    Text(Format.money(Money(value: part.value, currency: figure.currency), l10n: l10n))
                        .font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: 420, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16).fill(Theme.card))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(.quaternary))
        .environment(\.layoutDirection, figure.label.isRTLScript ? .rightToLeft : .leftToRight)
        .accessibilityIdentifier("figure")
    }
}

/// A list from the books: spending, invoices, budgets, pay runs, an employee's pay, holdings. A table all in one
/// currency names it once above the rows; a table in several names each row's.
struct TableCard: View {
    let table: TableData
    let l10n: L10n

    private var single: String? { table.currencies.count == 1 ? table.currencies[0] : nil }
    private var mixed: Bool { table.currencies.count > 1 }
    private var heading: String? {
        let caption = single.map { Format.currencyName($0, l10n: l10n) }
        let parts = [table.title, caption].compactMap { $0 }.filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: " — ")
    }
    private var rtl: Bool { (table.rows.first?.label ?? table.title ?? "").isRTLScript }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let heading {
                Text(heading).font(.caption).foregroundStyle(.secondary).padding(.bottom, 4)
            }
            ForEach(Array(table.rows.enumerated()), id: \.offset) { index, row in
                if index > 0 { Divider() }
                TableRowView(row: row, kind: table.kind, mixed: mixed, l10n: l10n)
                    .padding(.vertical, 7)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .frame(maxWidth: 560, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16).fill(Theme.card))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(.quaternary))
        .environment(\.layoutDirection, rtl ? .rightToLeft : .leftToRight)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("table")
    }
}

struct TableRowView: View {
    let row: TableData.Row
    let kind: String?
    let mixed: Bool
    let l10n: L10n

    private var amount: String {
        mixed ? Format.money(Money(value: row.value, currency: row.currency), l10n: l10n)
              : Format.amount(row.value, l10n: l10n, currency: row.currency)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.label ?? "—").font(.subheadline)
                    let notes = [row.sub, row.dueDate.map { l10n(.due, $0) }].compactMap { $0 }.filter { !$0.isEmpty }
                    if !notes.isEmpty {
                        Text(notes.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 8)
                VStack(alignment: .trailing, spacing: 2) {
                    Text(amount).font(.subheadline.bold().monospacedDigit())
                        .foregroundStyle(row.state == "overdue" || (kind == "budgets" && row.isOver) ? Color.red : .primary)
                    if let gain = row.gain, gain != 0 {
                        Text(Format.signed(gain, l10n: l10n, currency: row.currency)).font(.caption.monospacedDigit())
                            .foregroundStyle(gain > 0 ? Theme.primary : Color.red)
                    }
                    if kind == "budgets", let limit = row.limit {
                        Text(l10n(.budgetOf, Format.amount(row.value, l10n: l10n, currency: row.currency),
                                  Format.amount(limit, l10n: l10n, currency: row.currency)))
                            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                }
            }
            if kind == "budgets", let used = row.usedPct {
                ProgressView(value: min(max(used, 0), 100), total: 100)
                    .tint(row.isOver ? .red : (used >= 80 ? Theme.accent : Theme.primary))
            }
        }
    }
}

/// A document from the books: open it (Quick Look), or share it once fetched.
struct FileCard: View {
    let file: FileRef
    @ObservedObject var chat: ChatModel
    let l10n: L10n
    let onOpen: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Button(action: onOpen) {
                HStack(spacing: 10) {
                    ZStack(alignment: .bottom) {
                        RoundedRectangle(cornerRadius: 6).fill(Color.red.opacity(0.85)).frame(width: 34, height: 42)
                        Text(file.mime == "application/pdf" ? "PDF" : "FILE").font(.system(size: 9, weight: .bold))
                            .foregroundStyle(.white).padding(.bottom, 5)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(file.name).font(.subheadline.weight(.semibold)).lineLimit(1).truncationMode(.middle)
                        Text(chat.working.contains(file.path) ? l10n[.downloading] : l10n[.fileOpen])
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("file-open")
            Spacer(minLength: 8)
            if chat.working.contains(file.path) {
                ProgressView().controlSize(.small)
            } else if let url = chat.documents[file.path] {
                ShareLink(item: url) { Label(l10n[.fileShare], systemImage: "square.and.arrow.up") }
                    .labelStyle(.iconOnly)
                    .accessibilityIdentifier("file-share")
            } else {
                Button { Task { await chat.document(file) } } label: {
                    Label(l10n[.fileShare], systemImage: "square.and.arrow.up")
                }
                .labelStyle(.iconOnly)
                .buttonStyle(.borderless)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .frame(maxWidth: 420, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.card))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(.quaternary))
        .accessibilityIdentifier("file")
    }
}

struct ChartCard: View {
    let chart: ChartData
    let fallback: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(chart.title).font(.headline)
            Chart(Array(chart.points.enumerated()), id: \.offset) { index, point in
                if chart.style == "bar" {
                    BarMark(x: .value("x", point.x ?? "\(index)"), y: .value("value", point.value))
                        .foregroundStyle(point.value < 0 ? Color.red : Theme.primary)
                } else {
                    LineMark(x: .value("x", point.x ?? "\(index)"), y: .value("value", point.value))
                        .foregroundStyle(Theme.primary)
                }
            }
            .chartXAxis(.hidden)
            .frame(height: 160)
            .accessibilityLabel(fallback)
        }
        .padding(14)
        .frame(maxWidth: 560, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16).fill(Theme.card))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(.quaternary))
        .environment(\.layoutDirection, chart.title.isRTLScript ? .rightToLeft : .leftToRight)
        .accessibilityIdentifier("chart")
    }
}

struct IntakeCard: View {
    let intake: Intake

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(intake.bankLabel ?? intake.bankName ?? intake.fileName ?? intake.kind,
                  systemImage: intake.kind == "bank_statement" ? "building.columns" : "tray.and.arrow.down")
                .font(.headline)
            Text(intake.summary).font(.subheadline).foregroundStyle(.secondary)
        }
        .padding(14)
        .frame(maxWidth: 560, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16).fill(Theme.card))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(.quaternary))
        .accessibilityIdentifier("intake")
    }
}

struct ReviewCard: View {
    let review: Review

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                if let emoji = review.emoji { Text(emoji).font(.title2) }
                Text(review.title).font(.headline)
            }
            ForEach(Array((review.facts ?? []).enumerated()), id: \.offset) { _, fact in
                Label(fact, systemImage: "circle.fill").labelStyle(BulletStyle()).font(.subheadline)
            }
            if let note = review.note { Text(note).font(.footnote).foregroundStyle(.secondary) }
        }
        .padding(14)
        .frame(maxWidth: 560, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16).fill(Theme.card))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Theme.accent.opacity(0.6)))
        .environment(\.layoutDirection, review.title.isRTLScript ? .rightToLeft : .leftToRight)
        .accessibilityIdentifier("review")
    }
}

private struct BulletStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            configuration.icon.font(.system(size: 5)).foregroundStyle(.secondary)
            configuration.title
        }
    }
}

struct AssistantBubble: View {
    let text: String

    var body: some View {
        Text(text)
            .textSelection(.enabled)
            .multilineTextAlignment(text.isRTLScript ? .trailing : .leading)
            .environment(\.layoutDirection, text.isRTLScript ? .rightToLeft : .leftToRight)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 16).fill(Theme.assistantBubble))
            .frame(maxWidth: 560, alignment: .leading)
            .accessibilityIdentifier("assistant-text")
    }
}

struct UserBubble: View {
    let text: String
    var files: [SentFile] = []

    var body: some View {
        VStack(alignment: .trailing, spacing: 6) {
            ForEach(Array(files.enumerated()), id: \.offset) { _, file in
                Label(file.name ?? "—", systemImage: (file.contentType ?? "").hasPrefix("image/") ? "photo" : "doc")
                    .font(.caption)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background(Capsule().fill(Theme.userBubble.opacity(0.15)))
                    .accessibilityIdentifier("sent-file")
            }
            if !text.isEmpty {
                Text(text)
                    .textSelection(.enabled)
                    .foregroundStyle(.white)
                    .environment(\.layoutDirection, text.isRTLScript ? .rightToLeft : .leftToRight)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(RoundedRectangle(cornerRadius: 16).fill(Theme.userBubble))
                    .accessibilityIdentifier("user-text")
            }
        }
        .frame(maxWidth: 480, alignment: .trailing)
    }
}

/// What the assistant would record — nothing is written until Confirm, exactly as on the web. The same voucher,
/// drawn for the second person who decides on it, says who asked and offers Approve / Reject.
struct ProposalCard: View {
    let proposal: Proposal
    let l10n: L10n
    let working: Bool
    let actions: CardActions

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if proposal.isApproval && proposal.isPending {
                let who = proposal.mine == true ? l10n[.yourRequest] : l10n(.askedBy, proposal.requestedBy ?? "—")
                Label([who, proposal.requestedAt?.display].compactMap { $0 }.joined(separator: " · "),
                      systemImage: "person.2.badge.gearshape")
                    .font(.caption).foregroundStyle(.orange)
                    .accessibilityIdentifier("approval-asker")
            }
            HStack(alignment: .firstTextBaseline) {
                Text(proposal.title ?? proposal.tool ?? "")
                    .font(.headline)
                    .environment(\.layoutDirection, (proposal.title ?? "").isRTLScript ? .rightToLeft : .leftToRight)
                Spacer(minLength: 8)
                if let amount = proposal.amount {
                    Text(Format.money(amount, l10n: l10n)).font(.headline.monospacedDigit())
                }
            }
            if let date = proposal.date?.display {
                Label(date, systemImage: "calendar").font(.subheadline).foregroundStyle(.secondary)
            }
            if let lines = proposal.lines, !lines.isEmpty {
                VStack(spacing: 6) {
                    ForEach(lines) { line in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(line.account ?? "").font(.caption.monospaced()).foregroundStyle(.secondary)
                            Text(line.name ?? "").font(.subheadline).lineLimit(2)
                            Spacer(minLength: 8)
                            Text(line.debit > 0 ? "\(l10n[.debit]) \(Format.amount(line.debit, l10n: l10n))"
                                                : "\(l10n[.credit]) \(Format.amount(line.credit, l10n: l10n))")
                                .font(.subheadline.monospacedDigit())
                                .foregroundStyle(line.debit > 0 ? .primary : .secondary)
                        }
                    }
                }
                .padding(10)
                .background(RoundedRectangle(cornerRadius: 10).fill(Theme.assistantBubble))
            } else if let summary = proposal.summary {
                Text(summary).font(.subheadline).foregroundStyle(.secondary)
            }
            if let said = proposal.userMessage, proposal.isApproval, !said.isEmpty {
                Text("“\(said)”").font(.footnote).foregroundStyle(.secondary).lineLimit(3)
            }
            if proposal.needsApproval == true && proposal.isPending && !proposal.isApproval {
                Label(l10n[.needsApproval], systemImage: "person.2.badge.gearshape").font(.caption).foregroundStyle(.orange)
            }
            footer
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 16).fill(Theme.card))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke((proposal.isApproval ? Color.orange : Theme.primary).opacity(0.35)))
        .frame(maxWidth: 560, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(proposal.isApproval ? "approval-card" : "proposal-card")
    }

    @ViewBuilder private var footer: some View {
        switch proposal.phase {
        case .draft:
            HStack(spacing: 10) {
                if proposal.canConfirm {
                    button(l10n[.confirm], "checkmark", prominent: true, id: "confirm") { actions.confirm(proposal.token) }
                }
                if proposal.canApprove {
                    button(l10n[.approve], "checkmark", prominent: true, id: "approve") { actions.approve(proposal.token) }
                }
                if proposal.canReject {
                    button(l10n[.reject], "xmark", prominent: false, id: "reject") { actions.reject(proposal.token) }
                }
                if proposal.canWithdraw {
                    button(l10n[.withdraw], "arrow.uturn.backward", prominent: false, id: "withdraw") {
                        actions.withdraw(proposal.token)
                    }
                }
                if proposal.canCancel {
                    button(l10n[.cancel], "xmark", prominent: false, id: "cancel") { actions.cancel(proposal.token) }
                }
                if working { ProgressView().controlSize(.small) }
            }
            .disabled(working)
        case .posted:
            let seal = [proposal.posted?.voucher, proposal.posted?.date?.display].compactMap { $0 }.filter { !$0.isEmpty }
            Label(([l10n[.posted]] + seal).joined(separator: " · "), systemImage: "checkmark.circle.fill")
                .foregroundStyle(Theme.primary).font(.callout.bold())
                .accessibilityIdentifier("state-posted")
        case .waiting:
            Label(l10n.phase(.waiting), systemImage: "hourglass").foregroundStyle(.orange).font(.callout)
                .accessibilityIdentifier("state-waiting")
        case .undone:
            Label(l10n.phase(.undone), systemImage: "arrow.uturn.backward.circle").foregroundStyle(.secondary).font(.callout)
                .accessibilityIdentifier("state-undone")
        case .cancelled:
            Label(l10n.phase(.cancelled), systemImage: "xmark.circle").foregroundStyle(.secondary).font(.callout)
        case .rejected:
            Label(l10n.phase(.rejected), systemImage: "hand.raised").foregroundStyle(.red).font(.callout)
                .accessibilityIdentifier("state-rejected")
        case .expired:
            Label(l10n.phase(.expired), systemImage: "clock.badge.exclamationmark").foregroundStyle(.orange).font(.callout)
        case .replaced:
            Label(l10n.phase(.replaced), systemImage: "arrow.down.circle").foregroundStyle(.secondary).font(.callout)
        case .closed:
            Label(l10n.phase(.closed), systemImage: "lock").foregroundStyle(.secondary).font(.callout)
        }
    }

    @ViewBuilder
    private func button(_ title: String, _ icon: String, prominent: Bool, id: String,
                        _ action: @escaping () -> Void) -> some View {
        if prominent {
            Button(action: action) { Label(title, systemImage: icon).frame(maxWidth: .infinity) }
                .buttonStyle(.borderedProminent)
                .tint(Theme.primary)
                .accessibilityIdentifier(id)
        } else {
            Button(action: action) { Label(title, systemImage: icon).frame(maxWidth: .infinity) }
                .buttonStyle(.bordered)
                .accessibilityIdentifier(id)
        }
    }
}
