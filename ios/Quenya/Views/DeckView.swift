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
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Button { state.picking = true } label: {
                            HStack(spacing: 8) {
                                Text(state.activeBoard?.label ?? "Board")
                                    .font(.system(size: 14, weight: .semibold))
                                if let version = state.currentVersion {
                                    Text("v\(version)").font(.system(size: 11)).foregroundStyle(QuenyaTheme.muted)
                                }
                                Text("Change").font(.system(size: 11)).foregroundStyle(QuenyaTheme.rust)
                            }
                        }
                        .buttonStyle(.plain)
                        Spacer()
                        Text("\(seen) swiped")
                            .font(.system(size: 12))
                            .foregroundStyle(QuenyaTheme.muted)
                    }
                    GeometryReader { geometry in
                        ZStack(alignment: .leading) {
                            Capsule().fill(QuenyaTheme.line)
                            Capsule().fill(QuenyaTheme.rust)
                                .frame(width: geometry.size.width * CGFloat(min(10, seen % 10)) / 10)
                        }
                    }
                    .frame(height: 3)
                    .padding(.bottom, 2)

                    GeometryReader { geometry in
                        ZStack {
                            if loading {
                                LoadingLabel(title: "Picking pieces for you")
                            } else if let error {
                                ErrorPanel(message: error) { Task { await load() } }
                                    .padding(.horizontal, 12)
                            } else if items.isEmpty {
                                VStack(spacing: 14) {
                                    H3(text: "Enough to go on.")
                                    Text("Your swipes are in. Build outfits from them, or keep calibrating.")
                                        .font(.system(size: 14))
                                        .foregroundStyle(QuenyaTheme.muted)
                                        .multilineTextAlignment(.center)
                                    PillButton(title: "Build outfits", primary: true) {
                                        Task { await state.deckDone() }
                                    }
                                    PillButton(title: "10 more cards") { Task { await load() } }
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
                                    .padding(.horizontal, 8)
                                    .padding(.top, CGFloat(depth) * 8)
                                    .scaleEffect(depth == 0 ? 1 : (depth == 1 ? 0.97 : 0.94))
                                    .offset(y: CGFloat(depth) * 4)
                                    .zIndex(Double(3 - depth))
                                }
                            }
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                    .frame(height: min(520, UIScreen.main.bounds.height * 0.57))

                    if !items.isEmpty && !loading {
                        HStack(spacing: 12) {
                            PillButton(title: "Pass") {
                                if let item = items.first {
                                    swipeCommand = SwipeCommand(itemId: item.id, liked: false, generation: seen)
                                }
                            }
                            PillButton(title: "Keep", primary: true) {
                                if let item = items.first {
                                    swipeCommand = SwipeCommand(itemId: item.id, liked: true, generation: seen)
                                }
                            }
                        }
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        FieldLabel(text: "Learned so far", detail: state.currentVersion.map { "taste v\($0)" })
                        FlowLayout(spacing: 7) {
                            if liked.isEmpty && avoided.isEmpty {
                                Text("Every swipe is a label. Your taste updates every 8 swipes.")
                                    .font(.system(size: 12))
                                    .foregroundStyle(QuenyaTheme.muted)
                            }
                            ForEach(liked, id: \.0) { key, _ in
                                ChoicePill(title: "+ \(pretty(key))", selected: false) {}
                            }
                            ForEach(avoided, id: \.0) { key, _ in
                                ChoicePill(title: "− \(pretty(key))", selected: false) {}
                            }
                        }
                    }
                    .padding(.top, 10)
                }
                .padding(18)
            }
        }
        .background(QuenyaTheme.cream)
        .task(id: retryKey) { await load() }
        .sheet(item: $selectedItem) { ItemDetailSheet(item: $0) }
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
            if seen == 0 { seen = state.activeBoard?.counts.swipes ?? 0 }
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

    private var opacity: Double { min(1, Double(abs(dragX) / 100)) }

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .bottomLeading) {
                RemoteImage(path: item.imagePath)
                LinearGradient(
                    colors: [.clear, .black.opacity(0.05), .black.opacity(0.75)],
                    startPoint: .center,
                    endPoint: .bottom
                )
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(item.brand ?? "Unbranded").font(.system(size: 12, weight: .semibold))
                        Spacer()
                        Text(gbp(item.priceGbp)).font(.system(size: 15, weight: .semibold))
                    }
                    Text(item.title)
                        .font(.system(size: 22, weight: .semibold, design: .serif))
                        .lineLimit(2)
                    Text(provenance)
                        .font(.system(size: 11))
                        .foregroundStyle(.white.opacity(0.8))
                    if let tags = item.tags {
                        let labels = ([tags.category] + Array(tags.aesthetics.prefix(2)) + (tags.era == "unknown" ? [] : [tags.era])).filter { !$0.isEmpty }
                        HStack(spacing: 6) {
                            ForEach(Array(labels.prefix(3)), id: \.self) { tag in
                                Text(tag)
                                    .font(.system(size: 10, weight: .medium))
                                    .lineLimit(1)
                                    .fixedSize()
                                    .padding(.horizontal, 8)
                                    .padding(.vertical, 5)
                                    .background(.white.opacity(0.18), in: Capsule())
                            }
                        }
                    }
                }
                .foregroundStyle(.white)
                .padding(16)
                VStack {
                    HStack {
                        stamp("KEEP", color: QuenyaTheme.rust, leading: true)
                        Spacer()
                        stamp("PASS", color: QuenyaTheme.ink, leading: false)
                    }
                    Spacer()
                }
                .padding(16)
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
            .clipShape(RoundedRectangle(cornerRadius: 22))
            .overlay(RoundedRectangle(cornerRadius: 22).stroke(QuenyaTheme.line, lineWidth: 1))
            .overlay {
                RoundedRectangle(cornerRadius: 22)
                    .stroke((dragX >= 0 ? QuenyaTheme.rust : QuenyaTheme.ink).opacity(opacity * 0.35), lineWidth: 4)
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
                        if abs(dx) > 100 || abs(value.predictedEndTranslation.width) > 250 {
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
                .font(.system(size: 15, weight: .heavy))
                .tracking(1.4)
                .foregroundStyle(color)
                .padding(.horizontal, 12)
                .padding(.vertical, 7)
                .overlay(RoundedRectangle(cornerRadius: 5).stroke(color, lineWidth: 2))
                .opacity(Double(dragX > 0 ? (leading ? opacity : 0) : (leading ? 0 : opacity)))
                .scaleEffect(0.85 + opacity * 0.15)
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
