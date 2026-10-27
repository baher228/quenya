import SwiftUI
import UIKit

struct DeckView: View {
    @Environment(AppState.self) private var state
    @State private var items: [Item] = []
    @State private var loading = true
    @State private var error: String?
    @State private var seen = 0
    @State private var selectedItem: Item?
    @State private var retryKey = 0
    @State private var loadGeneration = 0
    @State private var swipeCommand: SwipeCommand?
    @State private var learnedExpanded = false

    private var liked: [(String, Double)] {
        uniqueTasteEntries((state.taste?.current?.profile.likes ?? [:]).sorted { $0.value > $1.value }, limit: 5)
    }

    private var avoided: [(String, Double)] {
        uniqueTasteEntries(
            (state.taste?.current?.profile.avoids ?? [:])
                .map { ($0.key, -$0.value) }
                .sorted { $0.1 < $1.1 },
            limit: 3
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Button { state.picking = true } label: {
                    HStack(spacing: 8) {
                        Text(state.activeBoard?.label ?? "Board")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(QuenyaTheme.webText)
                        if let version = state.currentVersion {
                            Text("v\(version)").font(.system(size: 11)).foregroundStyle(QuenyaTheme.webDim)
                        }
                        Text("Change").font(.system(size: 11)).foregroundStyle(QuenyaTheme.sage)
                    }
                }
                .buttonStyle(.plain)
                Spacer()
                Text("\(seen) swiped")
                    .font(.system(size: 12))
                    .foregroundStyle(QuenyaTheme.webMuted)
            }
            .padding(.bottom, 10)

            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule().fill(QuenyaTheme.webLine)
                    Capsule().fill(QuenyaTheme.sage)
                        .frame(width: geometry.size.width * CGFloat(min(10, seen % 10)) / 10)
                }
            }
            .frame(height: 3)
            .padding(.bottom, 10)

            GeometryReader { geometry in
                ZStack {
                    if loading {
                        LoadingLabel(title: "Picking pieces for you", dark: true)
                    } else if let error {
                        ErrorPanel(message: error) { Task { await load() } }
                            .padding(.horizontal, 12)
                    } else if items.isEmpty {
                        VStack(spacing: 14) {
                            Text("Enough to go on.")
                                .font(.system(size: 20, weight: .semibold))
                                .foregroundStyle(QuenyaTheme.webText)
                            Text("Your swipes are in. Build outfits from them, or keep calibrating.")
                                .font(.system(size: 14))
                                .foregroundStyle(QuenyaTheme.webMuted)
                                .multilineTextAlignment(.center)
                            PillButton(title: "Build outfits", primary: true, dark: true) {
                                Task { await state.deckDone() }
                            }
                            PillButton(title: "10 more cards", dark: true) { Task { await load() } }
                        }
                        .padding(24)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else {
                        ForEach(Array(items.prefix(3).enumerated().reversed()), id: \.element.id) { depth, item in
                            SwipeCard(
                                item: item,
                                depth: depth,
                                command: swipeCommand,
                                onOpen: { selectedItem = item }
                            ) { liked in
                                commit(item, liked: liked)
                            }
                            .scaleEffect(depth == 0 ? 1 : (depth == 1 ? 0.97 : 0.94))
                            .offset(y: CGFloat(depth) * 8)
                            .zIndex(Double(3 - depth))
                        }
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .frame(minHeight: 300)

            if !items.isEmpty && !loading {
                HStack {
                    Text("← Pass")
                    Spacer()
                    Text("Keep →")
                }
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(QuenyaTheme.webDim)
                .padding(.horizontal, 4)
                .padding(.top, 8)
            }

            VStack(alignment: .leading, spacing: 8) {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) { learnedExpanded.toggle() }
                } label: {
                    HStack {
                        Text("LEARNED SO FAR")
                            .font(.system(size: 11, weight: .semibold))
                            .tracking(1.1)
                        Spacer()
                        if let version = state.currentVersion {
                            Text("taste v\(version)")
                                .font(.system(size: 11))
                                .foregroundStyle(QuenyaTheme.webMuted)
                        }
                        Image(systemName: learnedExpanded ? "chevron.up" : "chevron.down")
                            .font(.system(size: 10, weight: .semibold))
                    }
                    .foregroundStyle(QuenyaTheme.webSoft)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                if learnedExpanded {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            if liked.isEmpty && avoided.isEmpty {
                                Text("Every swipe is a label. Your taste updates every 8 swipes.")
                                    .font(.system(size: 12))
                                    .foregroundStyle(QuenyaTheme.webMuted)
                                    .lineLimit(1)
                                    .fixedSize()
                            }
                            ForEach(liked, id: \.0) { key, _ in
                                tasteChip("+ \(pretty(key))", color: QuenyaTheme.sage)
                            }
                            ForEach(avoided, id: \.0) { key, _ in
                                tasteChip("− \(pretty(key))", color: QuenyaTheme.coral)
                            }
                        }
                        .padding(.horizontal, 18)
                    }
                    .padding(.horizontal, -18)
                }
            }
            .padding(.top, 14)
        }
        .padding(.horizontal, 18)
        .padding(.top, 8)
        .padding(.bottom, 12)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(QuenyaTheme.webPhone)
        .task(id: retryKey) { await load() }
        .sheet(item: $selectedItem) { ItemDetailSheet(item: $0) }
    }

    private func tasteChip(_ title: String, color: Color) -> some View {
        Text(title)
            .font(.system(size: 12))
            .foregroundStyle(color)
            .lineLimit(1)
            .fixedSize()
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .background(QuenyaTheme.webSurface2, in: Capsule())
    }

    private func load() async {
        loadGeneration += 1
        let generation = loadGeneration
        loading = true
        error = nil
        do {
            let result = try await state.api.deck(n: 10)
            guard !Task.isCancelled, generation == loadGeneration else { return }
            items = result
            ImageCache.shared.prefetch(items.map(\.imagePath))
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled, generation == loadGeneration else { return }
            self.error = error.localizedDescription
        }
        guard !Task.isCancelled, generation == loadGeneration else { return }
        loading = false
    }

    private func commit(_ item: Item, liked: Bool) {
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        withAnimation(.spring(response: 0.35, dampingFraction: 0.7)) {
            items.removeAll { $0.id == item.id }
            seen += 1
        }
        Task { await state.swipe(item, liked: liked) }
    }
}

private struct SwipeCommand: Equatable {
    let itemId: String
    let liked: Bool
    let generation: Int
}

private struct SwipeCard: View {
    let item: Item
    let depth: Int
    let command: SwipeCommand?
    let onOpen: () -> Void
    let onDecide: (Bool) -> Void
    @State private var dragX: CGFloat = 0
    @State private var dragY: CGFloat = 0
    @State private var committed = false

    private var opacity: Double { min(1, Double(abs(dragX) / 90)) }

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .bottomLeading) {
                RemoteImage(path: item.imagePath)
                LinearGradient(
                    colors: [.clear, .black.opacity(0.12), .black.opacity(0.9)],
                    startPoint: .center,
                    endPoint: .bottom
                )
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(item.brand ?? "Unbranded")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(QuenyaTheme.sage)
                            .lineLimit(1)
                            .truncationMode(.tail)
                        Spacer()
                        Text(gbp(item.priceGbp))
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundStyle(QuenyaTheme.webText)
                            .fixedSize()
                            .layoutPriority(1)
                            .frame(alignment: .trailing)
                    }
                    Text(item.title)
                        .font(.system(size: 19, weight: .semibold))
                        .lineLimit(2)
                        .minimumScaleFactor(0.85)
                        .multilineTextAlignment(.leading)
                    Text(provenance)
                        .font(.system(size: 11))
                        .foregroundStyle(QuenyaTheme.webSoft)
                        .lineLimit(1)
                    if let tags = item.tags {
                        let labels = ([tags.category] + Array(tags.aesthetics.prefix(2)) + (tags.era == "unknown" ? [] : [tags.era])).filter { !$0.isEmpty }
                        HStack(spacing: 6) {
                            ForEach(Array(labels.prefix(4)), id: \.self) { tag in
                                Text(tag)
                                    .font(.system(size: 11, weight: .medium))
                                    .lineLimit(1)
                                    .fixedSize()
                                    .padding(.horizontal, 8)
                                    .padding(.vertical, 3)
                                    .background(QuenyaTheme.webText.opacity(0.12), in: Capsule())
                            }
                        }
                        .frame(width: max(0, geometry.size.width - 32), alignment: .leading)
                        .clipped()
                        .mask(LinearGradient(
                            stops: [
                                .init(color: .black, location: 0.85),
                                .init(color: .clear, location: 1)
                            ],
                            startPoint: .leading,
                            endPoint: .trailing
                        ))
                    }
                }
                .foregroundStyle(QuenyaTheme.webText)
                .frame(width: max(0, geometry.size.width - 32), alignment: .leading)
                .padding(16)
                VStack {
                    HStack {
                        stamp("KEEP", color: QuenyaTheme.sage, leading: true)
                        Spacer()
                        stamp("PASS", color: QuenyaTheme.coral, leading: false)
                    }
                    Spacer()
                }
                .padding(16)
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
            .clipShape(RoundedRectangle(cornerRadius: 24))
            .overlay(RoundedRectangle(cornerRadius: 24).stroke(QuenyaTheme.webLine, lineWidth: 1))
            .overlay {
                let keep = dragX > 0 ? min(1, Double(dragX / 90)) : 0
                let pass = dragX < 0 ? min(1, Double(-dragX / 90)) : 0
                ZStack {
                    RoundedRectangle(cornerRadius: 24)
                        .fill(LinearGradient(
                            colors: [
                                QuenyaTheme.coral.opacity(pass * 0.32),
                                .clear,
                                .clear,
                                QuenyaTheme.sage.opacity(keep * 0.32)
                            ],
                            startPoint: .leading,
                            endPoint: .trailing
                        ))
                    RoundedRectangle(cornerRadius: 24)
                        .stroke(QuenyaTheme.sage.opacity(keep), lineWidth: 2)
                    RoundedRectangle(cornerRadius: 24)
                        .stroke(QuenyaTheme.coral.opacity(pass), lineWidth: 2)
                }
                    .allowsHitTesting(false)
            }
            .offset(x: dragX, y: dragY)
            .rotationEffect(.degrees(Double(dragX / 22)))
            .gesture(
                DragGesture(minimumDistance: 8)
                    .onChanged { value in
                        guard depth == 0 else { return }
                        dragX = value.translation.width
                        dragY = value.translation.height * 0.12
                    }
                    .onEnded { value in
                        guard depth == 0 else { return }
                        let dx = value.translation.width
                        if abs(dx) > 90 || abs(value.predictedEndTranslation.width) > 250 {
                            let direction = dx == 0
                                ? (value.predictedEndTranslation.width.sign == .minus ? -1 : 1)
                                : (dx > 0 ? 1 : -1)
                            flyOut(liked: direction > 0, width: geometry.size.width)
                        } else {
                            withAnimation(.spring(response: 0.35, dampingFraction: 0.7)) {
                                dragX = 0
                                dragY = 0
                            }
                        }
                    }
            )
            .onTapGesture {
                if depth == 0 && abs(dragX) < 5 { onOpen() }
            }
            .onChange(of: command) { _, value in
                guard depth == 0, value?.itemId == item.id, let value else { return }
                flyOut(liked: value.liked, width: geometry.size.width)
            }
        }
    }

    private var provenance: String {
        let source = item.source == "vinted" ? "Vinted" : item.source == "retail" ? "Retail" : item.source
        return [source, item.tags?.condition ?? item.condition, item.size.map { "size \($0)" }]
            .compactMap { $0 }
            .joined(separator: " · ")
    }

    @ViewBuilder
    private func stamp(_ title: String, color: Color, leading: Bool) -> some View {
        if opacity > 0.02 {
            Text(title)
                .font(.system(size: 12, weight: .heavy))
                .tracking(1.2)
        .foregroundStyle(QuenyaTheme.webPhone)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(color, in: Capsule())
                .opacity(Double(dragX > 0 ? (leading ? opacity : 0) : (leading ? 0 : opacity)))
                .scaleEffect(0.85 + opacity * 0.25)
                .rotationEffect(.degrees(leading ? -8 : 8))
        }
    }

    private func flyOut(liked: Bool, width: CGFloat) {
        guard !committed else { return }
        committed = true
        withAnimation(.easeOut(duration: 0.24)) {
            dragX = (liked ? 1 : -1) * width * 1.5
            dragY = -24
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.24) {
            onDecide(liked)
        }
    }
}
