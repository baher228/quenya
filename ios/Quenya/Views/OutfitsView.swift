import SwiftUI

struct OutfitsView: View {
    @Environment(AppState.self) private var state
    @State private var locked: [String: [String]] = [:]
    @State private var seen: [String: [String]] = [:]
    @State private var busy: String?
    @State private var rowErrors: [String: String] = [:]
    @State private var selectedItem: Item?
    @State private var safari: SafariTarget?
    @State private var checkoutOutfit: Outfit?

    private let minorSlots: Set<String> = ["shoes", "bag", "accessory"]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(state.outfits.isEmpty ? "Outfits" : "\(state.outfits.count) outfits\(state.currentVersion.map { " · taste v\($0)" } ?? "")")
                        .font(.system(size: 12))
                        .foregroundStyle(QuenyaTheme.sage)
                        .padding(.top, 12)
                    Text("Put together for you.")
                        .font(.system(size: 30, weight: .semibold))
                        .tracking(-0.5)
                        .foregroundStyle(QuenyaTheme.webText)
                }
                if state.outfitLoading {
                    LoadingLabel(title: "Styling outfits from your pool", dark: true)
                } else if let error = state.outfitError {
                    ErrorPanel(message: error) { Task { await state.buildOutfits() } }
                } else if state.outfits.isEmpty {
                    VStack(alignment: .leading, spacing: 14) {
                        Text("No outfits yet. Swipe a few cards first, then build.")
                            .font(.system(size: 14))
                            .foregroundStyle(QuenyaTheme.webMuted)
                        PillButton(title: "Build outfits", primary: true, dark: true) {
                            Task { await state.buildOutfits() }
                        }
                    }
                    .padding(18)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(QuenyaTheme.webSurface, in: RoundedRectangle(cornerRadius: 20))
                }

                ForEach(state.outfits) { outfit in
                    outfitCard(outfit)
                }

                if !state.outfits.isEmpty && !state.outfitLoading {
                    PillButton(title: "New set from taste v\(state.currentVersion.map(String.init) ?? "–")", dark: true) {
                        Task { await state.buildOutfits() }
                    }
                    .frame(maxWidth: .infinity)
                }
            }
            .padding(18)
            .padding(.bottom, 112)
        }
        .background(QuenyaTheme.webPhone)
        .sheet(item: $selectedItem) { ItemDetailSheet(item: $0) }
        .sheet(item: $checkoutOutfit) { outfit in
            CheckoutSheetView(outfit: outfit)
        }
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
                        .font(.system(size: 19, weight: .semibold))
                        .foregroundStyle(QuenyaTheme.webText)
                    Text(outfit.mood).font(.system(size: 13)).foregroundStyle(QuenyaTheme.webMuted)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 4) {
                    Text(gbp(outfit.totalGbp)).font(.system(size: 17, weight: .semibold)).foregroundStyle(QuenyaTheme.webText)
                    Text("\(outfit.pieces.count) pieces").font(.system(size: 11)).foregroundStyle(QuenyaTheme.webMuted)
                }
            }
            if !major.isEmpty {
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 3), GridItem(.flexible(), spacing: 3)], spacing: 3) {
                    ForEach(Array(major.enumerated()), id: \.element.id) { index, item in
                        pieceCard(
                            item,
                            outfit: outfit,
                            isLocked: keep.contains(item.id),
                            isWide: major.count % 2 == 1 && index == 0
                        )
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
                Text("WHY THIS").font(.system(size: 10, weight: .semibold)).tracking(1.2).foregroundStyle(QuenyaTheme.sage)
                Text(outfit.why).font(.system(size: 13)).foregroundStyle(QuenyaTheme.webSoft)
            }
            VStack(alignment: .leading, spacing: 3) {
                ForEach(outfit.pieces) { item in
                    Button {
                        if let url = URL(string: item.sourceUrl) { safari = SafariTarget(url: url) }
                    } label: {
                        HStack(alignment: .firstTextBaseline) {
                            Text(item.title).font(.system(size: 12)).lineLimit(1).underline(true, color: QuenyaTheme.webSoft)
                            Spacer(minLength: 6)
                            Text(item.source == "vinted" ? "Vinted" : item.source == "retail" ? "Retail" : item.source)
                                .font(.system(size: 10)).foregroundStyle(QuenyaTheme.webMuted)
                        }
                        .contentShape(Rectangle())
                        .foregroundStyle(QuenyaTheme.webText)
                    }
                    .buttonStyle(.plain)
                }
            }
            if let error = rowErrors[outfit.id] {
                ErrorPanel(message: error)
            }
            HStack(spacing: 7) {
                if outfit.verdict == "reject" {
                    verdictLabel("Not for me · \(outfit.reason ?? "Rejected")", color: QuenyaTheme.webMuted)
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
                    verdictLabel("Saved", color: QuenyaTheme.webText, outlined: true)
                } else {
                    compactButton("Love it", primary: true, disabled: busy != nil) {
                        Task { await state.verdict(outfit, like: true) }
                    }
                }
            }
            Button {
                checkoutOutfit = outfit
            } label: {
                Text("Buy the bundle · \(gbp(outfit.totalGbp))")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(QuenyaTheme.webText)
                    .frame(maxWidth: .infinity, minHeight: 40)
                    .background(QuenyaTheme.webSurface2, in: Capsule())
                    .overlay(Capsule().stroke(QuenyaTheme.webLine, lineWidth: 1))
            }
            .buttonStyle(.plain)
            ShareLink(item: shareText(outfit)) {
                Label("Share outfit", systemImage: "square.and.arrow.up")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(QuenyaTheme.webText)
                    .frame(maxWidth: .infinity, minHeight: 34)
                    .overlay(Capsule().stroke(QuenyaTheme.webLine, lineWidth: 1))
            }
            if outfit.verdict == "reject", let reason = outfit.reason {
                Text("Correction recorded: \(reason). Your taste was updated and the next outfits use it.")
                    .font(.system(size: 12))
                    .foregroundStyle(QuenyaTheme.webMuted)
            }
        }
        .padding(15)
        .background(QuenyaTheme.webSurface, in: RoundedRectangle(cornerRadius: 22))
        .overlay(RoundedRectangle(cornerRadius: 22).stroke(QuenyaTheme.webLine, lineWidth: 1))
    }

    private func pieceCard(_ item: Item, outfit: Outfit, isLocked: Bool, isWide: Bool) -> some View {
        GeometryReader { geometry in
            ZStack {
                Button { selectedItem = item } label: {
                    RemoteImage(path: item.imagePath)
                        .frame(width: geometry.size.width, height: geometry.size.height)
                }
                .buttonStyle(.plain)

                LinearGradient(
                    colors: [.clear, .black.opacity(0.85)],
                    startPoint: .center,
                    endPoint: .bottom
                )
                .allowsHitTesting(false)

                HStack(alignment: .lastTextBaseline) {
                    Text(item.brand ?? item.tags?.category ?? "Piece")
                        .font(.system(size: 11, weight: .semibold))
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                    Spacer(minLength: 6)
                    Text(gbp(item.priceGbp)).font(.system(size: 11))
                }
                .foregroundStyle(QuenyaTheme.cream)
                .padding(.horizontal, 9)
                .padding(.bottom, 7)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
                .allowsHitTesting(false)
            }
            .overlay(alignment: .top) {
                HStack {
                    pieceActionButton(isLocked ? "Locked" : "Lock", locked: isLocked) {
                        toggleLock(outfit, item.id)
                    }
                    Spacer()
                    pieceActionButton(
                        busy == outfit.id + "swap" + item.id ? "…" : "Swap",
                        disabled: busy != nil
                    ) {
                        Task { await swap(outfit, item: item) }
                    }
                }
                .padding(7)
            }
        }
        .aspectRatio(isWide ? 4.0 / 3.0 : 3.0 / 4.0, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay {
            RoundedRectangle(cornerRadius: 16)
                .stroke(isLocked ? QuenyaTheme.sage : .clear, lineWidth: 2)
                .allowsHitTesting(false)
        }
    }

    private func minorRow(_ item: Item, outfit: Outfit, isLocked: Bool) -> some View {
        HStack(spacing: 9) {
            Button { selectedItem = item } label: {
                RemoteImage(path: item.imagePath).frame(width: 56, height: 56).clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)
            Button { selectedItem = item } label: {
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(item.brand.map { "\($0) · " } ?? "")\(item.title)")
                        .font(.system(size: 12, weight: .medium)).lineLimit(1)
                    Text(gbp(item.priceGbp)).font(.system(size: 11)).foregroundStyle(QuenyaTheme.webMuted)
                }
                .foregroundStyle(QuenyaTheme.webText)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain)
            HStack(spacing: 6) {
                pieceActionButton(isLocked ? "Locked" : "Lock", locked: isLocked, onPhotoTile: false) {
                    toggleLock(outfit, item.id)
                }
                pieceActionButton(
                    busy == outfit.id + "swap" + item.id ? "…" : "Swap",
                    disabled: busy != nil,
                    onPhotoTile: false
                ) {
                    Task { await swap(outfit, item: item) }
                }
            }
        }
        .padding(.horizontal, 3)
        .padding(.vertical, 8)
        .overlay(alignment: .top) {
            Rectangle().fill(QuenyaTheme.webLine).frame(height: 1)
        }
        .overlay(alignment: .leading) {
            if isLocked {
                Rectangle().fill(QuenyaTheme.sage).frame(width: 2).padding(.vertical, 1)
            }
        }
        .overlay {
            RoundedRectangle(cornerRadius: 10)
                .stroke(isLocked ? QuenyaTheme.sage.opacity(0.35) : .clear, lineWidth: 1)
                .allowsHitTesting(false)
        }
    }

    private func pieceActionButton(
        _ title: String,
        locked: Bool = false,
        disabled: Bool = false,
        onPhotoTile: Bool = true,
        action: @escaping () -> Void
    ) -> some View {
        let background = locked
            ? QuenyaTheme.sage
            : (onPhotoTile ? QuenyaTheme.ink.opacity(0.82) : QuenyaTheme.webSurface2)
        let foreground = locked
            ? QuenyaTheme.webPhone
            : (onPhotoTile ? QuenyaTheme.cream : QuenyaTheme.webText)
        let border = locked ? QuenyaTheme.sage : (onPhotoTile ? Color.clear : QuenyaTheme.webLine)

        return Button(action: action) {
            Text(title.uppercased())
                .font(.system(size: 10, weight: .medium))
                .tracking(0.6)
                .foregroundStyle(foreground)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(background, in: Capsule())
                .overlay(Capsule().stroke(border, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .opacity(disabled ? 0.5 : 1)
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
                .foregroundStyle(primary ? QuenyaTheme.webPhone : QuenyaTheme.webText)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
                .frame(maxWidth: .infinity, minHeight: 34)
                .padding(.horizontal, 6)
                .background(primary ? QuenyaTheme.webText : QuenyaTheme.webSurface2, in: Capsule())
                .overlay(Capsule().stroke(primary ? Color.clear : QuenyaTheme.webLine, lineWidth: 1))
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
            .overlay(Capsule().stroke(outlined ? QuenyaTheme.webText : QuenyaTheme.webLine, lineWidth: 1))
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

private struct CheckoutSheetView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let outfit: Outfit
    @State private var result: Checkout?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text(outfit.title)
                        .font(.system(size: 22, weight: .semibold))
                        .foregroundStyle(QuenyaTheme.webText)
                    if let result {
                        Text("Demo checkout · no payment taken · \(result.orderRef)")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(QuenyaTheme.sage)
                        Text("For real orders we'd buy each piece from its shop and ship the bundle to you. For now, here's every listing.")
                            .font(.system(size: 13))
                            .foregroundStyle(QuenyaTheme.webMuted)
                        ForEach(result.lines) { line in
                            if let url = URL(string: line.sourceUrl) {
                                Link(destination: url) {
                                    HStack(alignment: .top) {
                                        VStack(alignment: .leading, spacing: 4) {
                                            Text(line.title)
                                                .font(.system(size: 13))
                                                .foregroundStyle(QuenyaTheme.webText)
                                            Text(line.source)
                                                .font(.system(size: 11))
                                                .foregroundStyle(QuenyaTheme.webMuted)
                                        }
                                        Spacer(minLength: 12)
                                        Text(gbp(line.priceGbp))
                                            .font(.system(size: 12, weight: .medium))
                                            .foregroundStyle(QuenyaTheme.webSoft)
                                    }
                                    .contentShape(Rectangle())
                                }
                            }
                        }
                        HStack {
                            Text("Total")
                            Spacer()
                            Text(gbp(result.totalGbp)).fontWeight(.semibold)
                        }
                        .font(.system(size: 14))
                        .foregroundStyle(QuenyaTheme.webText)
                    } else if let error {
                        ErrorPanel(message: error)
                    } else {
                        LoadingLabel(title: "Preparing your bundle", dark: true)
                    }
                }
                .padding(20)
            }
            .background(QuenyaTheme.webPhone)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(QuenyaTheme.webText)
                }
            }
            .toolbarBackground(QuenyaTheme.webPhone, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
        }
        .preferredColorScheme(.dark)
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .task {
            do {
                let response = try await state.api.checkout(outfitId: outfit.id)
                guard !Task.isCancelled else { return }
                result = response
            } catch is CancellationError {
                return
            } catch {
                guard !Task.isCancelled else { return }
                self.error = error.localizedDescription
            }
        }
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
