import Charts
import SwiftUI

/// One block of an answer (docs/contracts/mobile-blocks.schema.json): text, a proposal card, "recorded" with its
/// undo, a figure, a chart, a review — and any other kind as its own sentence (`fallback_text`).
struct BlockView: View {
    let block: Block
    let l10n: L10n
    let working: Bool
    var onConfirm: (String) -> Void = { _ in }
    var onCancel: (String) -> Void = { _ in }
    var onUndo: (String) -> Void = { _ in }

    var body: some View {
        switch block {
        case .text(_, let text):
            AssistantBubble(text: text)
        case .proposal(_, let p):
            ProposalCard(proposal: p, l10n: l10n, working: working,
                         onConfirm: { onConfirm(p.token) }, onCancel: { onCancel(p.token) })
        case .posted(_, let p, let fallback):
            PostedNote(posted: p, fallback: fallback, l10n: l10n, working: working,
                       onUndo: { if let id = p.auditLogId { onUndo(id) } })
        case .figure(_, let f, _):
            FigureCard(figure: f, l10n: l10n)
        case .chart(_, let c, let fallback):
            ChartCard(chart: c, fallback: fallback)
        case .review(_, let r, _):
            ReviewCard(review: r)
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
    let onUndo: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            if posted.undone == true {
                Label(l10n[.undone], systemImage: "arrow.uturn.backward.circle").foregroundStyle(.secondary)
            } else {
                Label(([l10n[.posted]] + [posted.voucher, posted.date?.display].compactMap { $0 }).joined(separator: " · "),
                      systemImage: "checkmark.seal.fill")
                    .foregroundStyle(Theme.primary)
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
                    Text(part.label).font(.subheadline)
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

    var body: some View {
        Text(text)
            .textSelection(.enabled)
            .foregroundStyle(.white)
            .environment(\.layoutDirection, text.isRTLScript ? .rightToLeft : .leftToRight)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 16).fill(Theme.userBubble))
            .frame(maxWidth: 480, alignment: .trailing)
            .accessibilityIdentifier("user-text")
    }
}

/// What the assistant would record — nothing is written until Confirm, exactly as on the web.
struct ProposalCard: View {
    let proposal: Proposal
    let l10n: L10n
    let working: Bool
    let onConfirm: () -> Void
    let onCancel: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
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
            if proposal.needsApproval == true && proposal.isPending {
                Label(l10n[.needsApproval], systemImage: "person.2.badge.gearshape").font(.caption).foregroundStyle(.orange)
            }
            footer
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 16).fill(Theme.card))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Theme.primary.opacity(0.35)))
        .frame(maxWidth: 560, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("proposal-card")
    }

    @ViewBuilder private var footer: some View {
        switch proposal.state ?? "pending" {
        case "pending":
            HStack(spacing: 10) {
                if proposal.canConfirm {
                    Button(action: onConfirm) {
                        Label(l10n[.confirm], systemImage: "checkmark").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.primary)
                    .accessibilityIdentifier("confirm")
                }
                if proposal.canCancel {
                    Button(action: onCancel) {
                        Label(l10n[.cancel], systemImage: "xmark").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("cancel")
                }
                if working { ProgressView().controlSize(.small) }
            }
            .disabled(working)
        case "posted", "executed":
            Label(l10n[.posted], systemImage: "checkmark.circle.fill").foregroundStyle(Theme.primary).font(.callout.bold())
        case "cancelled":
            Label(l10n[.cancelled], systemImage: "xmark.circle").foregroundStyle(.secondary).font(.callout)
        case "expired":
            Label(l10n[.expired], systemImage: "clock.badge.exclamationmark").foregroundStyle(.orange).font(.callout)
        default:
            Text(proposal.state ?? "").font(.callout).foregroundStyle(.secondary)
        }
    }
}
