import SwiftUI

struct OutfitsView: View {
    @Environment(AppState.self) private var state
    @State private var locked: [String: [String]] = [:]
    @State private var seen: [String: [String]] = [:]
    @State private var busy: String?
    @State private var rowErrors: [String: String] = [:]
    @State private var selectedItem: Item?
    @State private var safari: SafariTarget?

    private let minorSlots: Set<String> = ["shoes", "bag", "accessory"]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Eyebrow(text: state.outfits.isEmpty ? "Outfits" : "\(state.outfits.count) outfits\(state.currentVersion.map { " · taste v\($0)" } ?? "")")
                H1(text: "Put together for you.")
                if state.outfitLoading {
                    LoadingLabel(title: "Styling outfits from your pool")
                } else if let error = state.outfitError {
                    ErrorPanel(message: error) { Task { await state.buildOutfits() } }
                } else if state.outfits.isEmpty {
                    VStack(alignment: .leading, spacing: 14) {
                        Text("No outfits yet. Swipe a few cards first, then build.")
                            .font(.system(size: 14))
                            .foregroundStyle(QuenyaTheme.muted)
                        PillButton(title: "Build outfits", primary: true) {
                            Task { await state.buildOutfits() }
                        }
                    }
                    .padding(18)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 20))
                }

                ForEach(state.outfits) { outfit in
                    outfitCard(outfit)
                }

                if !state.outfits.isEmpty && !state.outfitLoading {
                    PillButton(title: "New set from taste v\(state.currentVersion.map(String.init) ?? "–")") {
                        Task { await state.buildOutfits() }
                    }
                    .frame(maxWidth: .infinity)
                }
            }
            .padding(18)
        }
        .background(QuenyaTheme.cream)
        .sheet(item: $selectedItem) { ItemDetailSheet(item: $0) }
        .fullScreenCover(item: $safari) { target in SafariView(url: target.url) }
    }

    @ViewBuilder
    private func outfitCard(_ outfit: Outfit) -> some View {
        let keep = locked[outfit.id] ?? []
        let major = outfit.pieces.filter { !minorSlots.contains($0.tags?.slot ?? "") }
        let minor = outfit.pieces.filter { minorSlots.contains($0.tags?.slot ?? "") }

        VStack(alignment: .leading, spacing: 15) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(outfit.title)
                        .font(.system(size: 21, weight: .semibold, design: .serif))
                        .foregroundStyle(QuenyaTheme.ink)
                    Text(outfit.mood).font(.system(size: 13)).foregroundStyle(QuenyaTheme.muted)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 4) {
                    Text(gbp(outfit.totalGbp)).font(.system(size: 16, weight: .semibold))
                    Text("\(outfit.pieces.count) pieces").font(.system(size: 11)).foregroundStyle(QuenyaTheme.muted)
                }
            }
            if !major.isEmpty {
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 9), GridItem(.flexible(), spacing: 9)], spacing: 10) {
                    ForEach(Array(major.enumerated()), id: \.element.id) { index, item in
                        pieceCard(item, outfit: outfit, isLocked: keep.contains(item.id))
                            .gridCellColumns(major.count % 2 == 1 && index == 0 ? 2 : 1)
                    }
                }
            }
            if !minor.isEmpty {
                VStack(spacing: 8) {
                    ForEach(minor) { item in
                        minorRow(item, outfit: outfit, isLocked: keep.contains(item.id))
                    }
                }
            }
            VStack(alignment: .leading, spacing: 5) {
                Text("Why this").font(.system(size: 11, weight: .semibold)).tracking(1).foregroundStyle(QuenyaTheme.rust)
                Text(outfit.why).font(.system(size: 13)).foregroundStyle(QuenyaTheme.softInk)
            }
            VStack(alignment: .leading, spacing: 8) {
                ForEach(outfit.pieces) { item in
                    Button {
                        if let url = URL(string: item.sourceUrl) { safari = SafariTarget(url: url) }
                    } label: {
                        HStack(alignment: .firstTextBaseline) {
                            Text(item.title).font(.system(size: 12)).lineLimit(1)
                            Spacer(minLength: 6)
                            Text(item.source == "vinted" ? "Vinted" : item.source == "retail" ? "Retail" : item.source)
                                .font(.system(size: 10)).foregroundStyle(QuenyaTheme.muted)
                        }
                        .contentShape(Rectangle())
                        .foregroundStyle(QuenyaTheme.ink)
                    }
                    .buttonStyle(.plain)
                }
            }
            if let error = rowErrors[outfit.id] {
                ErrorPanel(message: error)
            }
            HStack(spacing: 7) {
                if outfit.verdict == "reject" {
                    verdictLabel("Not for me · \(outfit.reason ?? "Rejected")", color: QuenyaTheme.muted)
                } else {
                    compactButton("Not for me", disabled: busy != nil) {
                        state.presentedSheet = .reject(outfit)
                    }
                }
                compactButton(
                    busy == outfit.id + "rebuild" ? "Rebuilding…" : (keep.isEmpty ? "Rebuild" : "Rebuild around locks"),
                    disabled: busy != nil
                ) { Task { await rebuild(outfit) } }
                if outfit.verdict == "like" {
                    verdictLabel("Kept", color: QuenyaTheme.ink, outlined: true)
                } else {
                    compactButton("Keep", primary: true, disabled: busy != nil) {
                        Task { await state.verdict(outfit, like: true) }
                    }
                }
            }
            ShareLink(item: shareText(outfit)) {
                Label("Share outfit", systemImage: "square.and.arrow.up")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(QuenyaTheme.ink)
                    .frame(maxWidth: .infinity, minHeight: 40)
                    .overlay(Capsule().stroke(QuenyaTheme.line, lineWidth: 1))
            }
            if outfit.verdict == "reject", let reason = outfit.reason {
                Text("Correction recorded: \(reason). Your taste was updated and the next outfits use it.")
                    .font(.system(size: 12))
                    .foregroundStyle(QuenyaTheme.muted)
            }
        }
        .padding(15)
        .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 22))
        .overlay(RoundedRectangle(cornerRadius: 22).stroke(QuenyaTheme.line, lineWidth: 1))
    }

    private func pieceCard(_ item: Item, outfit: Outfit, isLocked: Bool) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Button { selectedItem = item } label: {
                RemoteImage(path: item.imagePath)
                    .frame(height: 150)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .buttonStyle(.plain)
            HStack {
                Text(item.brand ?? item.tags?.category ?? "Piece")
                    .font(.system(size: 11, weight: .semibold)).lineLimit(1)
                Spacer()
                Text(gbp(item.priceGbp)).font(.system(size: 11))
            }
            HStack(spacing: 6) {
                compactButton(isLocked ? "Locked" : "Lock") { toggleLock(outfit, item.id) }
                compactButton(busy == outfit.id + "swap" + item.id ? "…" : "Swap", disabled: busy != nil) {
                    Task { await swap(outfit, item: item) }
                }
            }
        }
        .padding(8)
        .background(isLocked ? QuenyaTheme.rustSoft.opacity(0.55) : QuenyaTheme.cream, in: RoundedRectangle(cornerRadius: 15))
    }

    private func minorRow(_ item: Item, outfit: Outfit, isLocked: Bool) -> some View {
        HStack(spacing: 9) {
            Button { selectedItem = item } label: {
                RemoteImage(path: item.imagePath).frame(width: 52, height: 52).clipShape(RoundedRectangle(cornerRadius: 9))
            }
            .buttonStyle(.plain)
            Button { selectedItem = item } label: {
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(item.brand.map { "\($0) · " } ?? "")\(item.title)")
                        .font(.system(size: 12, weight: .medium)).lineLimit(1)
                    Text(gbp(item.priceGbp)).font(.system(size: 11)).foregroundStyle(QuenyaTheme.muted)
                }
                .foregroundStyle(QuenyaTheme.ink)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain)
            compactButton(isLocked ? "Locked" : "Lock") { toggleLock(outfit, item.id) }
            compactButton(busy == outfit.id + "swap" + item.id ? "…" : "Swap", disabled: busy != nil) {
                Task { await swap(outfit, item: item) }
            }
        }
        .padding(7)
        .background(isLocked ? QuenyaTheme.rustSoft.opacity(0.55) : QuenyaTheme.cream, in: RoundedRectangle(cornerRadius: 13))
    }

    private func compactButton(
        _ title: String,
        primary: Bool = false,
        disabled: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(primary ? QuenyaTheme.cream : QuenyaTheme.ink)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
                .frame(maxWidth: .infinity, minHeight: 34)
                .padding(.horizontal, 6)
                .background(primary ? QuenyaTheme.ink : QuenyaTheme.cream, in: Capsule())
                .overlay(Capsule().stroke(primary ? Color.clear : QuenyaTheme.line, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .opacity(disabled ? 0.5 : 1)
    }

    private func verdictLabel(_ title: String, color: Color, outlined: Bool = false) -> some View {
        Text(title)
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(color)
            .lineLimit(1)
            .minimumScaleFactor(0.65)
            .frame(maxWidth: .infinity, minHeight: 34)
            .padding(.horizontal, 6)
            .overlay(Capsule().stroke(outlined ? QuenyaTheme.ink : QuenyaTheme.line, lineWidth: 1))
    }

    private func toggleLock(_ outfit: Outfit, _ itemId: String) {
        var ids = locked[outfit.id] ?? []
        if ids.contains(itemId) { ids.removeAll { $0 == itemId } }
        else { ids.append(itemId) }
        locked[outfit.id] = ids
    }

    private func swap(_ outfit: Outfit, item: Item) async {
        await run(outfit, key: "swap\(item.id)") {
            let next = try await state.api.swap(
                outfitId: outfit.id,
                itemId: item.id,
                excludeIds: Array((seen[outfit.id] ?? []).suffix(300))
            )
            var locks = locked[outfit.id] ?? []
            if locks.contains(item.id) { locks.removeAll { $0 == item.id } }
            locked[next.id] = locks
            seen[next.id] = uniqueOrdered((seen[outfit.id] ?? []) + outfit.pieces.map(\.id))
            state.replace(outfit, with: next)
        }
    }

    private func rebuild(_ outfit: Outfit) async {
        await run(outfit, key: "rebuild") {
            let keep = locked[outfit.id] ?? []
            let otherPieces = state.outfits.filter { $0.id != outfit.id }.flatMap { $0.pieces.map(\.id) }
            let excluded = outfit.pieces.map(\.id).filter { !keep.contains($0) } + otherPieces
            let response = try await state.api.outfits(count: 1, lockedIds: keep, excludeIds: excluded)
            guard let next = response.outfits.first else {
                throw APIError(status: 409, message: "No outfit fits those locks and your budget. Unlock a piece and retry.", requestIds: [])
            }
            locked[next.id] = keep
            state.replace(outfit, with: next)
        }
    }

    private func run(_ outfit: Outfit, key: String, action: () async throws -> Void) async {
        busy = outfit.id + key
        rowErrors[outfit.id] = nil
        defer { busy = nil }
        do { try await action() }
        catch { rowErrors[outfit.id] = error.localizedDescription }
    }

    private func shareText(_ outfit: Outfit) -> String {
        var lines = [outfit.title, outfit.mood, "Total \(gbp(outfit.totalGbp))"]
        lines += outfit.pieces.map { "\($0.title) (\($0.source)) \(gbp($0.priceGbp)) \($0.sourceUrl)" }
        return lines.joined(separator: "\n")
    }
}

struct SafariTarget: Identifiable {
    let id = UUID()
    let url: URL
}

private func uniqueOrdered(_ values: [String]) -> [String] {
    var seen = Set<String>()
    return values.filter { seen.insert($0).inserted }
}
