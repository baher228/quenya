import PhotosUI
import SwiftUI
import UIKit

struct BriefView: View {
    @Environment(AppState.self) private var state
    @State private var draft = Brief.default
    @State private var photoSelection: [PhotosPickerItem] = []
    @State private var photoData: [Data] = []

    private let slots: [(String, String)] = [
        ("outerwear", "Outerwear"), ("top", "Tops"), ("knitwear", "Knitwear"),
        ("bottom", "Trousers & skirts"), ("dress", "Dresses"), ("shoes", "Shoes"),
        ("bag", "Bags"), ("accessory", "Accessories")
    ]

    private static let budgetSteps: [Int] = {
        Array(stride(from: 50, through: 500, by: 10))
        + Array(stride(from: 525, through: 1000, by: 25))
        + Array(stride(from: 1100, through: 3000, by: 100))
        + Array(stride(from: 3250, through: 10000, by: 250))
    }()

    private var budgetIndex: Double {
        Double(Self.budgetSteps.enumerated().min(by: {
            abs($0.element - Int(draft.budgetGbp)) < abs($1.element - Int(draft.budgetGbp))
        })?.offset ?? 0)
    }

    private var nicheWord: String {
        switch draft.niche {
        case ..<0.3: "mainstream"
        case ..<0.6: "a bit off-piste"
        case ..<0.85: "niche"
        default: "deep cut"
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                Eyebrow(text: state.briefMode == .edit ? "Edit style" : "New style")
                H1(text: state.briefMode == .edit ? (state.activeBoard?.label ?? "Edit style") : "Tell us what you're after.")

                VStack(alignment: .leading, spacing: 8) {
                    FieldLabel(text: "In your words")
                    TextField("e.g. Autumn in London. Workwear but cleaner, Japanese labels, no big logos.", text: $draft.text, axis: .vertical)
                        .lineLimit(3...7)
                        .font(.system(size: 15))
                        .padding(14)
                        .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 14))
                        .onChange(of: draft.text) { _, value in
                            if value.count > 600 { draft.text = String(value.prefix(600)) }
                        }
                    Text("\(draft.text.count)/600")
                        .font(.system(size: 11))
                        .foregroundStyle(QuenyaTheme.muted)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                }

                SegmentedChoice(title: "Dressing", selected: $draft.gender, options: [
                    ("menswear", "Menswear"), ("womenswear", "Womenswear"), ("both", "Both")
                ])
                SegmentedChoice(title: "Looking for", selected: $draft.lookingFor, options: [
                    ("outfits", "Outfits"), ("pieces", "Single pieces")
                ])
                if draft.lookingFor == "outfits" {
                    SegmentedChoice(
                        title: "Shoes",
                        selected: Binding(
                            get: { draft.includeShoes ? "yes" : "no" },
                            set: { draft.includeShoes = $0 == "yes" }
                        ),
                        options: [("yes", "Include shoes"), ("no", "Clothes only")]
                    )
                }
                if draft.lookingFor == "pieces" {
                    VStack(alignment: .leading, spacing: 10) {
                        FieldLabel(text: "Pieces")
                        FlowLayout(spacing: 8) {
                            ForEach(slots, id: \.0) { slot, label in
                                ChoicePill(title: label, selected: draft.pieces.contains(slot)) {
                                    if draft.pieces.contains(slot) {
                                        draft.pieces.removeAll { $0 == slot }
                                    } else {
                                        draft.pieces.append(slot)
                                    }
                                }
                            }
                        }
                    }
                }
                SegmentedChoice(title: "Condition", selected: $draft.condition, options: [
                    ("second-hand", "Second-hand"), ("new", "New"), ("both", "Both")
                ])
                VStack(alignment: .leading, spacing: 8) {
                    FieldLabel(text: "How niche", detail: nicheWord)
                    Slider(value: $draft.niche, in: 0...1)
                        .tint(QuenyaTheme.rust)
                }
                VStack(alignment: .leading, spacing: 8) {
                    FieldLabel(text: "Budget per outfit", detail: "up to £\(Int(draft.budgetGbp).formatted())")
                    Slider(
                        value: Binding(
                            get: { budgetIndex },
                            set: { draft.budgetGbp = Double(Self.budgetSteps[max(0, min(Self.budgetSteps.count - 1, Int($0.rounded())))] ) }
                        ),
                        in: 0...Double(Self.budgetSteps.count - 1),
                        step: 1
                    )
                    .tint(QuenyaTheme.rust)
                }
                VStack(alignment: .leading, spacing: 8) {
                    FieldLabel(text: "Sizes")
                    HStack(spacing: 10) {
                        sizeField("Tops", keyPath: \.tops)
                        sizeField("Waist", keyPath: \.waist)
                        sizeField("Shoes UK", keyPath: \.shoes)
                    }
                }
                VStack(alignment: .leading, spacing: 10) {
                    FieldLabel(text: "Inspiration photos", detail: "optional · up to 3")
                    HStack(spacing: 10) {
                        ForEach(Array(photoData.enumerated()), id: \.offset) { index, data in
                            Button {
                                photoData.remove(at: index)
                                if index < photoSelection.count { photoSelection.remove(at: index) }
                            } label: {
                                Group {
                                    if let image = UIImage(data: data) {
                                        Image(uiImage: image).resizable().scaledToFill()
                                    } else {
                                        Rectangle().fill(QuenyaTheme.line)
                                    }
                                }
                                .frame(width: 70, height: 70)
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                                .overlay(alignment: .topTrailing) {
                                    Image(systemName: "xmark.circle.fill")
                                        .symbolRenderingMode(.palette)
                                        .foregroundStyle(.white, QuenyaTheme.ink)
                                        .padding(4)
                                }
                            }
                            .buttonStyle(.plain)
                        }
                        if photoData.count < 3 {
                            PhotosPicker(selection: $photoSelection, maxSelectionCount: 3, matching: .images) {
                                Text("+ Add")
                                    .font(.system(size: 13, weight: .medium))
                                    .foregroundStyle(QuenyaTheme.ink)
                                    .frame(width: 70, height: 70)
                                    .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 12))
                            }
                        }
                    }
                }

                if let error = state.briefError { ErrorPanel(message: error) }
                PillButton(
                    title: state.creatingBrief
                    ? (state.briefMode == .edit ? "Updating…" : "Creating…")
                    : (state.briefMode == .edit ? "Update style" : "Create style"),
                    primary: true,
                    disabled: state.creatingBrief || draft.text.trimmingCharacters(in: .whitespacesAndNewlines).count < 3
                ) {
                    Task { await state.submitBrief(draft, photos: photoData) }
                }
                .frame(maxWidth: .infinity)
                if state.briefMode == .edit && state.canCreateStyle {
                    Button("+ New style instead") { state.startNewStyle() }
                        .font(.system(size: 13))
                        .foregroundStyle(QuenyaTheme.muted)
                        .frame(maxWidth: .infinity)
                }
            }
            .padding(20)
        }
        .background(QuenyaTheme.cream)
        .onAppear { draft = state.currentBrief.merged() }
        .onChange(of: state.currentBrief) { _, value in draft = value.merged() }
        .onChange(of: photoSelection) { _, items in
            Task {
                var loaded: [Data] = []
                for item in items.prefix(3) {
                    if let data = try? await item.loadTransferable(type: Data.self),
                       let jpeg = jpegData(data) {
                        loaded.append(jpeg)
                    }
                }
                photoData = loaded
            }
        }
    }

    private func sizeField(_ label: String, keyPath: WritableKeyPath<Sizes, String>) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label).font(.system(size: 11)).foregroundStyle(QuenyaTheme.muted)
            TextField("", text: Binding(
                get: { draft.sizes[keyPath: keyPath] },
                set: { draft.sizes[keyPath: keyPath] = String($0.prefix(8)) }
            ))
            .textInputAutocapitalization(.characters)
            .padding(10)
            .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 10))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct SegmentedChoice: View {
    let title: String
    @Binding var selected: String
    let options: [(String, String)]

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            FieldLabel(text: title)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 7) {
                    ForEach(options, id: \.0) { value, label in
                        ChoicePill(title: label, selected: selected == value) { selected = value }
                    }
                }
            }
        }
    }
}

struct FlowLayout: Layout {
    var spacing: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > width, x > 0 {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: width, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var rowHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX {
                x = bounds.minX
                y += rowHeight + spacing
                rowHeight = 0
            }
            subview.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}

private func jpegData(_ data: Data) -> Data? {
    guard let image = UIImage(data: data) else { return nil }
    let side = max(image.size.width, image.size.height)
    let resized: UIImage
    if side > 1600 {
        let scale = 1600 / side
        let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
        UIGraphicsBeginImageContextWithOptions(size, true, 1)
        image.draw(in: CGRect(origin: .zero, size: size))
        resized = UIGraphicsGetImageFromCurrentImageContext() ?? image
        UIGraphicsEndImageContext()
    } else {
        resized = image
    }
    return resized.jpegData(compressionQuality: 0.85)
}
