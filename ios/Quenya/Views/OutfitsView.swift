import SwiftUI

struct OutfitsView: View {
    @Environment(AppState.self) private var state
    @State private var selectedItem: Item?
    @State private var safari: SafariTarget?
    @State private var checkoutSelection: CheckoutSelection?
    @State private var tapCount = 0

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
                    SlowHint()
                    OutfitSkeletonCard()
                    OutfitSkeletonCard()
                    PillButton(
                        title: state.outfits.isEmpty ? "Build outfits" : "New set from taste v\(state.currentVersion.map(String.init) ?? "–")",
                        primary: state.outfits.isEmpty,
                        dark: true,
                        busy: state.busy.isBusy("outfits.build"),
                        action: {}
                    )
                    .frame(maxWidth: .infinity)
                } else if let error = state.outfitError {
                    ErrorPanel(message: error) { Task { await state.buildOutfits() } }
                } else if state.outfits.isEmpty {
                    VStack(alignment: .leading, spacing: 14) {
                        Text("No outfits yet. Swipe a few cards first, then build.")
                            .font(.system(size: 14))
                            .foregroundStyle(QuenyaTheme.webMuted)
                    PillButton(title: "Build outfits", primary: true, dark: true, busy: state.busy.isBusy("outfits.build")) {
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
                    PillButton(title: "New set from taste v\(state.currentVersion.map(String.init) ?? "–")", dark: true, busy: state.busy.isBusy("outfits.build")) {
                        Task { await state.buildOutfits() }
                    }
                    .frame(maxWidth: .infinity)
                    if state.busy.isBusy("outfits.build") { SlowHint() }
                }
            }
            .quenyaPage()
            .padding(.horizontal, 12)
            .padding(.vertical, 18)
            .padding(.bottom, 24)
        }
        .background(QuenyaTheme.webPhone)
        .sheet(item: $selectedItem) { ItemDetailSheet(item: $0) }
        .sheet(item: $checkoutSelection) { selection in
            CheckoutSheetView(outfit: selection.outfit, boardId: selection.boardId)
        }
        .fullScreenCover(item: $safari) { target in SafariView(url: target.url) }
    }

    @ViewBuilder
    private func outfitCard(_ outfit: Outfit) -> some View {
        let keep = state.locked[outfit.id] ?? []
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
                if state.busy.isBusy(rebuildKey(outfit)) {
                    piecesGrid(count: major.count) { _, wide in
                        SkeletonBlock(cornerRadius: 16)
                            .aspectRatio(wide ? 4.0 / 3.0 : 3.0 / 4.0, contentMode: .fit)
                        }
                    .padding(.horizontal, -12)
                    .skeletonPulse()
                    .accessibilityHidden(true)
                    SlowHint()
                } else {
                    piecesGrid(count: major.count) { index, wide in
                        pieceCard(
                            major[index],
                            outfit: outfit,
                            isLocked: keep.contains(major[index].id),
                            aspect: wide ? 4.0 / 3.0 : 3.0 / 4.0
                        )
                    }
                    .padding(.horizontal, -12)
                }
            }
            if let message = state.busy.error(rebuildKey(outfit)) {
                ErrorPanel(message: message) {
                    guard let styleId = state.activeBoard?.id else { return }
                    Task { await state.rebuildOutfit(outfit, styleId: styleId) }
                }
            } else if let failedItem = outfit.pieces.first(where: { state.busy.error(swapKey(outfit, $0)) != nil }),
                      let message = state.busy.error(swapKey(outfit, failedItem)) {
                ErrorPanel(message: message) {
                    guard let styleId = state.activeBoard?.id else { return }
                    Task { await state.swapOutfit(outfit, item: failedItem, styleId: styleId) }
                }
            }
            if let message = state.busy.error("verdict.\(outfit.id)"),
               !rejectSheetIsPresented(for: outfit) {
                ErrorPanel(message: message) { Task { await state.retryVerdict(outfit) } }
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
            HStack(spacing: 7) {
                if outfit.verdict == "reject" {
                    verdictLabel("Not for me · \(outfit.reason ?? "Rejected")", color: QuenyaTheme.webMuted)
                } else {
                    compactButton("Not for me", disabled: outfitActionBusy(outfit)) {
                        guard let boardId = state.activeBoard?.id else { return }
                        state.presentedSheet = .reject(outfit, boardId: boardId)
                    }
                }
                compactButton(
                    keep.isEmpty ? "Rebuild" : "Rebuild around locks",
                    disabled: outfitActionBusy(outfit),
                    busy: state.busy.isBusy(rebuildKey(outfit))
                ) {
                    guard let styleId = state.activeBoard?.id else { return }
                    Task { await state.rebuildOutfit(outfit, styleId: styleId) }
                }
                if outfit.verdict == "like" {
                    verdictLabel("Saved", color: QuenyaTheme.webText, outlined: true)
                } else {
                    compactButton("Love it", primary: true, disabled: outfitActionBusy(outfit), busy: state.busy.isBusy("verdict.\(outfit.id)")) {
                        guard let styleId = state.activeBoard?.id else { return }
                        Task { await state.verdict(outfit, like: true, styleId: styleId) }
                    }
                }
            }
            Button {
                guard let boardId = state.activeBoard?.id else { return }
                checkoutSelection = CheckoutSelection(outfit: outfit, boardId: boardId)
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

    private func pieceCard(_ item: Item, outfit: Outfit, isLocked: Bool, aspect: CGFloat) -> some View {
        let swapping = state.busy.isBusy(swapKey(outfit, item))
        return VStack(alignment: .leading, spacing: 6) {
            GeometryReader { geometry in
                Button { selectedItem = item } label: {
                    RemoteImage(path: item.imagePath, preview: item.imagePreview)
                        .frame(width: geometry.size.width, height: geometry.size.height)
                }
                .buttonStyle(.plain)
                .overlay(alignment: .top) {
                    HStack {
                        pieceActionButton(isLocked ? "Locked" : "Lock", locked: isLocked) {
                            toggleLock(outfit, item.id)
                        }
                        Spacer()
                        pieceActionButton(
                            "Swap",
                            disabled: outfitActionBusy(outfit),
                            busy: swapping
                        ) {
                            guard let styleId = state.activeBoard?.id else { return }
                            Task { await state.swapOutfit(outfit, item: item, styleId: styleId) }
                        }
                    }
                    .padding(7)
                }
            }
            .aspectRatio(aspect, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .overlay {
                RoundedRectangle(cornerRadius: 16)
                    .stroke(isLocked ? QuenyaTheme.sage : .clear, lineWidth: 2)
                    .allowsHitTesting(false)
            }

            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(item.brand.map { "\($0) · \(item.title)" } ?? item.title)
                    .font(.system(size: 12))
                    .foregroundStyle(QuenyaTheme.webText)
                    .lineLimit(1)
                    .truncationMode(.tail)
                Spacer(minLength: 4)
                Text(gbp(item.priceGbp))
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(QuenyaTheme.webSoft)
                    .fixedSize()
                    .layoutPriority(1)
            }
            .padding(.horizontal, 6)
        }
        .opacity(swapping ? 0.45 : 1)
        .skeletonPulse(enabled: swapping)
    }

    private func minorRow(_ item: Item, outfit: Outfit, isLocked: Bool) -> some View {
        let swapping = state.busy.isBusy(swapKey(outfit, item))
        return HStack(spacing: 9) {
            Button { selectedItem = item } label: {
                RemoteImage(path: item.imagePath, preview: item.imagePreview).frame(width: 56, height: 56).clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)
            Button { selectedItem = item } label: {
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(item.brand.map { "\($0) · " } ?? "")\(item.title)")
                        .font(.system(size: 12, weight: .medium))
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Text(gbp(item.priceGbp))
                        .font(.system(size: 11))
                        .foregroundStyle(QuenyaTheme.webMuted)
                        .fixedSize()
                        .layoutPriority(1)
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
                    "Swap",
                    disabled: outfitActionBusy(outfit),
                    busy: swapping,
                    onPhotoTile: false
                ) {
                    guard let styleId = state.activeBoard?.id else { return }
                    Task { await state.swapOutfit(outfit, item: item, styleId: styleId) }
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
        .opacity(swapping ? 0.45 : 1)
        .skeletonPulse(enabled: swapping)
    }

    private func pieceActionButton(
        _ title: String,
        locked: Bool = false,
        disabled: Bool = false,
        busy: Bool = false,
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

        return Button {
            tapCount += 1
            action()
        } label: {
            ZStack {
                Text(title.uppercased())
                    .font(.system(size: 10, weight: .medium))
                    .tracking(0.6)
                    .foregroundStyle(foreground)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                    .opacity(busy ? 0 : 1)
                if busy { ProgressView().controlSize(.mini).tint(foreground) }
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(background, in: Capsule())
            .overlay(Capsule().stroke(border, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .disabled(disabled || busy)
        .opacity(disabled && !busy ? 0.5 : 1)
        .accessibilityLabel(busy ? "Loading" : title)
        .sensoryFeedback(.impact(weight: .light), trigger: tapCount)
    }

    private func compactButton(
        _ title: String,
        primary: Bool = false,
        disabled: Bool = false,
        busy: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button {
            tapCount += 1
            action()
        } label: {
            ZStack {
                Text(title)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(primary ? QuenyaTheme.webPhone : QuenyaTheme.webText)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                    .opacity(busy ? 0 : 1)
                if busy {
                    ProgressView().controlSize(.mini)
                        .tint(primary ? QuenyaTheme.webPhone : QuenyaTheme.webText)
                }
            }
            .frame(maxWidth: .infinity, minHeight: 34)
            .padding(.horizontal, 6)
            .background(primary ? QuenyaTheme.webText : QuenyaTheme.webSurface2, in: Capsule())
            .overlay(Capsule().stroke(primary ? Color.clear : QuenyaTheme.webLine, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .disabled(disabled || busy)
        .opacity(disabled && !busy ? 0.5 : 1)
        .accessibilityLabel(busy ? "Loading" : title)
        .sensoryFeedback(.impact(weight: .light), trigger: tapCount)
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
        var ids = state.locked[outfit.id] ?? []
        if ids.contains(itemId) { ids.removeAll { $0 == itemId } }
        else { ids.append(itemId) }
        state.locked[outfit.id] = ids
    }

    private func outfitActionBusy(_ outfit: Outfit) -> Bool {
        state.busy.isBusy("outfits.build")
            || state.busy.isBusy("verdict.\(outfit.id)")
            || state.busy.isBusy(rebuildKey(outfit))
            || outfit.pieces.contains { state.busy.isBusy(swapKey(outfit, $0)) }
    }

    private func rejectSheetIsPresented(for outfit: Outfit) -> Bool {
        guard case let .some(.reject(rejectedOutfit, boardId: _)) = state.presentedSheet else { return false }
        return rejectedOutfit.id == outfit.id
    }

    private func rebuildKey(_ outfit: Outfit) -> String {
        "outfit.\(outfit.id).rebuild"
    }

    private func swapKey(_ outfit: Outfit, _ item: Item) -> String {
        "outfit.\(outfit.id).swap.\(item.id)"
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
    let boardId: String
    @State private var result: Checkout?
    @State private var error: String?
    @State private var loading = false
    @State private var safari: SafariTarget?

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
                            CheckoutLineRow(
                                line: line,
                                imagePath: outfit.pieces.first { $0.sourceUrl == line.sourceUrl }?.imagePath,
                                imagePreview: outfit.pieces.first { $0.sourceUrl == line.sourceUrl }?.imagePreview,
                                open: {
                                    if let url = URL(string: line.sourceUrl) {
                                        safari = SafariTarget(url: url)
                                    }
                                }
                            )
                            if line.id != result.lines.last?.id {
                                Rectangle().fill(QuenyaTheme.webLine).frame(height: 1)
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
                        ErrorPanel(message: error) { Task { await loadCheckout() } }
                    } else {
                        LoadingLabel(title: "Preparing your bundle", dark: true)
                        SlowHint()
                        ForEach(outfit.pieces) { _ in CheckoutSkeletonRow() }
                        Rectangle().fill(QuenyaTheme.webLine).frame(height: 1)
                        HStack {
                            SkeletonBlock(cornerRadius: 6).frame(width: 42, height: 13)
                            Spacer()
                            SkeletonBlock(cornerRadius: 6).frame(width: 72, height: 15)
                        }
                        .skeletonPulse()
                        .accessibilityHidden(true)
                    }
                }
                .quenyaPage()
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
        .quenyaSheet()
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .fullScreenCover(item: $safari) { target in SafariView(url: target.url) }
        .task {
            if result == nil { await loadCheckout() }
        }
    }

    private func loadCheckout() async {
        guard !loading else { return }
        loading = true
        error = nil
        defer { loading = false }
        do {
            let response = try await state.api.checkout(outfitId: outfit.id, boardId: boardId)
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

private struct CheckoutSelection: Identifiable {
    let outfit: Outfit
    let boardId: String

    var id: String { outfit.id }
}

private struct CheckoutSkeletonRow: View {
    var body: some View {
        HStack(spacing: 12) {
            SkeletonBlock(cornerRadius: 8)
                .frame(width: 48, height: 48)
            GeometryReader { geometry in
                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 7) {
                        SkeletonBlock(cornerRadius: 5)
                            .frame(width: geometry.size.width * 0.62, height: 12)
                        SkeletonBlock(cornerRadius: 5)
                            .frame(width: geometry.size.width * 0.27, height: 10)
                    }
                    Spacer(minLength: 0)
                    SkeletonBlock(cornerRadius: 14)
                        .frame(width: 70, height: 27)
                }
            }
            .frame(height: 48)
        }
        .skeletonPulse()
        .accessibilityHidden(true)
    }
}

private struct CheckoutLineRow: View {
    let line: CheckoutLine
    let imagePath: String?
    let imagePreview: String?
    let open: () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            Group {
                if let imagePath {
                    RemoteImage(path: imagePath, preview: imagePreview)
                } else {
                    RoundedRectangle(cornerRadius: 8).fill(QuenyaTheme.webLine)
                }
            }
            .frame(width: 48, height: 48)
            .clipShape(RoundedRectangle(cornerRadius: 8))

            VStack(alignment: .leading, spacing: 6) {
                Text(line.title)
                    .font(.system(size: 13))
                    .foregroundStyle(QuenyaTheme.webText)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    Button(action: open) {
                        Text("Shop · \(line.source) ↗")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(QuenyaTheme.webSoft)
                            .padding(.horizontal, 9)
                            .padding(.vertical, 5)
                            .background(QuenyaTheme.webSurface2, in: Capsule())
                    }
                    .buttonStyle(.plain)
                    Spacer(minLength: 0)
                    Text(gbp(line.priceGbp))
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(QuenyaTheme.webSoft)
                        .fixedSize()
                }
            }
        }
    }
}

struct SafariTarget: Identifiable {
    let id = UUID()
    let url: URL
}

private struct OutfitSkeletonCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            GeometryReader { geometry in
                HStack(alignment: .top, spacing: 8) {
                    VStack(alignment: .leading, spacing: 4) {
                        block(width: geometry.size.width * 0.55, height: 18)
                        block(width: geometry.size.width * 0.35, height: 12)
                    }
                    Spacer(minLength: 0)
                    block(width: 48, height: 14)
                }
            }
            .frame(height: 34)

            piecesGrid(count: 4) { _, wide in
                VStack(alignment: .leading, spacing: 6) {
                    SkeletonBlock(cornerRadius: 16)
                        .aspectRatio(wide ? 4.0 / 3.0 : 3.0 / 4.0, contentMode: .fit)
                    SkeletonBlock(cornerRadius: 3)
                        .frame(width: 72, height: 6)
                }
            }
            .padding(.horizontal, -12)
        }
        .padding(15)
        .background(QuenyaTheme.webSurface, in: RoundedRectangle(cornerRadius: 22))
        .overlay(RoundedRectangle(cornerRadius: 22).stroke(QuenyaTheme.webLine, lineWidth: 1))
        .skeletonPulse()
        .accessibilityHidden(true)
    }

    private func block(width: CGFloat, height: CGFloat) -> some View {
        SkeletonBlock(cornerRadius: height / 2)
            .frame(width: width, height: height)
    }
}

@ViewBuilder
private func piecesGrid<Content: View>(
    count: Int,
    @ViewBuilder tile: @escaping (Int, Bool) -> Content
) -> some View {
    let columns = [
        GridItem(.flexible(), spacing: 3),
        GridItem(.flexible(), spacing: 3)
    ]
    if count > 0 && count.isMultiple(of: 2) == false {
        VStack(spacing: 8) {
            tile(0, true)
            if count > 1 {
                LazyVGrid(columns: columns, spacing: 8) {
                    ForEach(1..<count, id: \.self) { index in
                        tile(index, false)
                    }
                }
            }
        }
    } else {
        LazyVGrid(columns: columns, spacing: 8) {
            ForEach(0..<count, id: \.self) { index in
                tile(index, false)
            }
        }
    }
}

private func uniqueOrdered(_ values: [String]) -> [String] {
    var seen = Set<String>()
    return values.filter { seen.insert($0).inserted }
}
