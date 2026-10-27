import SwiftUI

struct TasteView: View {
    @Environment(AppState.self) private var state
    @State private var loading = false

    private var profile: TasteProfile? { state.taste?.current?.profile }

    private var bars: [(String, Double)] {
        guard let profile else { return [] }
        return (Array(profile.likes) + Array(profile.avoids.mapValues { -$0 }))
            .sorted { $0.value > $1.value }
    }

    private var shownBars: [(String, Double)] {
        bars.count > 10 ? Array(bars.prefix(7)) + Array(bars.suffix(3)) : bars
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 15) {
                Eyebrow(text: "\(state.activeBoard?.label ?? "Your taste")\(state.currentVersion.map { " · v\($0)" } ?? "")")
                H1(text: "What we've learned.")
                if loading { LoadingLabel(title: "Loading your profile") }
                if state.taste == nil { LoadingLabel(title: "Loading your profile") }
                if let profile {
                    Text(gbpText(profile.summary))
                        .font(.system(size: 15))
                        .foregroundStyle(QuenyaTheme.softInk)
                        .lineSpacing(3)

                    if let core = state.taste?.core,
                       !core.rules.isEmpty || !core.likes.isEmpty || !core.avoids.isEmpty || !core.palette.isEmpty {
                        panel("Across your boards") {
                            if !core.rules.isEmpty {
                                ForEach(core.rules) { rule in
                                    Text("\(gbpText(rule.rule)) · \(rule.boards.joined(separator: ", "))")
                                        .font(.system(size: 12)).foregroundStyle(QuenyaTheme.softInk)
                                }
                            }
                            FlowLayout(spacing: 7) {
                                ForEach(uniqueTasteEntries(Array(core.likes) + core.avoids.mapValues { -$0 }, limit: 8), id: \.0) { key, value in
                                    ChoicePill(title: "\(value > 0 ? "+" : "−") \(pretty(key))", selected: false) {}
                                }
                                ForEach(core.palette, id: \.self) { colour in
                                    ChoicePill(title: colour, selected: false) {}
                                }
                            }
                        }
                    }

                    panel("This board") {
                        ForEach(Array(shownBars.enumerated()), id: \.offset) { _, entry in
                            TasteBar(name: pretty(entry.0), value: entry.1)
                        }
                    }

                    panel("Rules & palette") {
                        ForEach(profile.rules, id: \.self) { rule in
                            Text(gbpText(rule)).font(.system(size: 13)).foregroundStyle(QuenyaTheme.softInk)
                        }
                        FlowLayout(spacing: 7) {
                            ForEach(profile.palette, id: \.self) { colour in
                                ChoicePill(title: colour, selected: false) {}
                            }
                        }
                        HStack {
                            Text("Budget ceiling")
                            Spacer()
                            Text(gbp(profile.priceCeilingGbp)).fontWeight(.semibold)
                        }
                        .font(.system(size: 13)).foregroundStyle(QuenyaTheme.softInk)
                        HStack {
                            Text("Condition")
                            Spacer()
                            Text(profile.conditionPreference).fontWeight(.semibold)
                        }
                        .font(.system(size: 13)).foregroundStyle(QuenyaTheme.softInk)
                    }
                } else if state.taste != nil {
                    Text("No profile yet. Start with a brief.")
                        .font(.system(size: 14)).foregroundStyle(QuenyaTheme.muted)
                }

                if let history = state.taste?.history, !history.isEmpty {
                    panel("Model updates") {
                        ForEach(history.reversed()) { entry in
                            VStack(alignment: .leading, spacing: 7) {
                                HStack {
                                    Text("v\(entry.version)").font(.system(size: 14, weight: .semibold))
                                    Spacer()
                                    Text(entry.requestId ?? "")
                                        .font(.system(size: 10, design: .monospaced))
                                        .foregroundStyle(QuenyaTheme.muted)
                                }
                                let changes = entry.changes ?? entry.profile?.changes ?? []
                                if changes.isEmpty {
                                    Text("First read of your brief.")
                                        .font(.system(size: 12)).foregroundStyle(QuenyaTheme.muted)
                                } else {
                                    ForEach(Array(changes.enumerated()), id: \.offset) { _, change in
                                        Text(gbpText(change))
                                            .font(.system(size: 12)).foregroundStyle(QuenyaTheme.softInk)
                                    }
                                }
                            }
                            if entry.id != history.first?.id {
                                Divider().overlay(QuenyaTheme.line)
                            }
                        }
                    }
                }
            }
            .padding(18)
        }
        .background(QuenyaTheme.cream)
        .refreshable { await refresh() }
        .task { if state.taste == nil { await refresh() } }
    }

    private func panel<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            FieldLabel(text: title)
            content()
        }
        .padding(15)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 17))
    }

    private func refresh() async {
        loading = true
        await state.refreshTaste()
        guard !Task.isCancelled else { return }
        loading = false
    }
}

private struct TasteBar: View {
    let name: String
    let value: Double

    var body: some View {
        HStack(spacing: 8) {
            Text(name).font(.system(size: 12)).frame(width: 100, alignment: .leading).lineLimit(1)
            GeometryReader { geometry in
                ZStack(alignment: value < 0 ? .trailing : .leading) {
                    Rectangle().fill(QuenyaTheme.cream)
                    Rectangle()
                        .fill(value < 0 ? QuenyaTheme.rust : QuenyaTheme.ink)
                        .frame(width: geometry.size.width * min(1, abs(value)))
                }
            }
            .frame(height: 6)
            Text("\(value > 0 ? "+" : "−")\(abs(value), specifier: "%.1f")")
                .font(.system(size: 10, design: .monospaced))
                .frame(width: 36, alignment: .trailing)
        }
        .foregroundStyle(QuenyaTheme.softInk)
    }
}
