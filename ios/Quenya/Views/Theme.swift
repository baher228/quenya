import SwiftUI

enum QuenyaTheme {
    // Web tokens, mapped onto the existing app-wide names.
    static let cream = Color(hex: 0x171411)
    static let surface = Color(hex: 0x221E1A)
    static let line = Color(hex: 0x2A2520)
    static let ink = Color(hex: 0xEDE6DA)
    static let softInk = Color(hex: 0xC9C0B3)
    static let muted = Color(hex: 0x8E8579)
    static let rust = Color(hex: 0xB9C4A8)
    static let rustSoft = Color(hex: 0xD9897A)
    static let sage = Color(hex: 0xB9C4A8)
    static let coral = Color(hex: 0xD9897A)
    static let webPhone = Color(hex: 0x171411)
    static let webSurface = Color(hex: 0x1F1B17)
    static let webSurface2 = Color(hex: 0x221E1A)
    static let webLine = Color(hex: 0x2A2520)
    static let webLine2 = Color(hex: 0x2F2A24)
    static let webText = Color(hex: 0xEDE6DA)
    static let webSoft = Color(hex: 0xC9C0B3)
    static let webMuted = Color(hex: 0x8E8579)
    static let webDim = Color(hex: 0x7D7468)
    static let webSegmentSelected = Color(hex: 0x3A332C)
    static let webSegmentMuted = Color(hex: 0x9A9184)
}

extension Color {
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xff) / 255,
            green: Double((hex >> 8) & 0xff) / 255,
            blue: Double(hex & 0xff) / 255
        )
    }
}

extension View {
    func quenyaPage() -> some View {
        frame(maxWidth: .infinity, alignment: .topLeading)
    }

    func quenyaSheet() -> some View {
        background(QuenyaTheme.webPhone.ignoresSafeArea())
            .presentationBackground(QuenyaTheme.webPhone)
            .preferredColorScheme(.dark)
    }

    func skeletonPulse(enabled: Bool = true) -> some View {
        modifier(SkeletonPulse(enabled: enabled))
    }
}

struct SkeletonBlock: View {
    var cornerRadius: CGFloat = 8

    var body: some View {
        RoundedRectangle(cornerRadius: cornerRadius)
            .fill(QuenyaTheme.webLine)
    }
}

private struct SkeletonPulse: ViewModifier {
    var enabled: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var dimmed = false

    func body(content: Content) -> some View {
        content
            .opacity(reduceMotion ? 1 : (dimmed ? 0.55 : 1))
            .onAppear { animateIfAllowed() }
            .onChange(of: reduceMotion) { _, _ in animateIfAllowed() }
            .onChange(of: enabled) { _, _ in animateIfAllowed() }
    }

    private func animateIfAllowed() {
        guard !reduceMotion, enabled else {
            dimmed = false
            return
        }
        withAnimation(.easeInOut(duration: 1.1).repeatForever(autoreverses: true)) {
            dimmed = true
        }
    }
}

struct SlowHint: View {
    var text = "Still working, the stylist is picking pieces…"
    @State private var visible = false

    var body: some View {
        Group {
            if visible {
                Text(text)
                    .font(.system(size: 12))
                    .foregroundStyle(QuenyaTheme.webMuted)
                    .transition(.opacity)
            }
        }
        .task {
            guard !visible else { return }
            try? await Task.sleep(for: .seconds(8))
            guard !Task.isCancelled else { return }
            withAnimation(.easeIn(duration: 0.35)) { visible = true }
        }
    }
}

struct Eyebrow: View {
    let text: String

    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 12, weight: .medium))
            .tracking(1.4)
            .foregroundStyle(QuenyaTheme.rust)
    }
}

struct H1: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 34, weight: .semibold, design: .serif))
            .foregroundStyle(QuenyaTheme.ink)
            .fixedSize(horizontal: false, vertical: true)
    }
}

struct H3: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 22, weight: .semibold, design: .serif))
            .foregroundStyle(QuenyaTheme.ink)
            .fixedSize(horizontal: false, vertical: true)
    }
}

struct FieldLabel: View {
    let text: String
    var detail: String? = nil

    var body: some View {
        HStack {
            Text(text.uppercased())
                .font(.system(size: 11, weight: .semibold))
                .tracking(1.2)
                .foregroundStyle(QuenyaTheme.webMuted)
            Spacer(minLength: 8)
            if let detail {
                Text(detail)
                    .font(.system(size: 12))
                    .foregroundStyle(QuenyaTheme.webSoft)
            }
        }
    }
}

struct PillButton: View {
    let title: String
    var primary = false
    var disabled = false
    var dark = false
    var busy = false
    var action: () -> Void
    @State private var tapCount = 0

    private var foreground: Color {
        primary ? QuenyaTheme.webPhone : QuenyaTheme.webText
    }

    var body: some View {
        Button {
            tapCount += 1
            action()
        } label: {
            ZStack {
                Text(title)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(foreground)
                    .opacity(busy ? 0 : 1)
                if busy {
                    ProgressView().controlSize(.small).tint(foreground)
                }
            }
                .frame(minHeight: 44)
                .padding(.horizontal, 20)
                .background(primary ? QuenyaTheme.webText : QuenyaTheme.webSurface2, in: Capsule())
                .overlay(Capsule().stroke(primary ? Color.clear : QuenyaTheme.webLine, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .disabled(disabled || busy)
        .opacity(disabled && !busy ? 0.45 : 1)
        .accessibilityLabel(busy ? "Loading" : title)
        .sensoryFeedback(.impact(weight: .light), trigger: tapCount)
    }
}

struct ChoicePill: View {
    let title: String
    var selected = false
    var dark = false
    var outlined = false
    var busy = false
    var disabled = false
    var action: () -> Void
    @State private var tapCount = 0

    var body: some View {
        Button {
            tapCount += 1
            action()
        } label: {
            ZStack {
                Text(title)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(selected ? QuenyaTheme.webPhone : outlined ? QuenyaTheme.sage : QuenyaTheme.webSoft)
                    .opacity(busy ? 0 : 1)
                if busy {
                    ProgressView().controlSize(.small)
                        .tint(selected ? QuenyaTheme.webPhone : outlined ? QuenyaTheme.sage : QuenyaTheme.webSoft)
                }
            }
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(selected ? QuenyaTheme.webText : (outlined ? Color.clear : QuenyaTheme.webSurface2), in: Capsule())
                .overlay(Capsule().stroke(outlined ? QuenyaTheme.sage.opacity(0.35) : .clear, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .disabled(disabled || busy)
        .opacity(disabled && !busy ? 0.5 : 1)
        .accessibilityLabel(busy ? "Loading" : title)
        .sensoryFeedback(.impact(weight: .light), trigger: tapCount)
    }
}

struct ErrorPanel: View {
    let message: String
    var requestIds: [String] = []
    var retry: (() -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(message).font(.system(size: 14)).foregroundStyle(QuenyaTheme.webText)
            if !requestIds.isEmpty {
                Text("AI request \(requestIds.joined(separator: ", "))")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(QuenyaTheme.muted)
            }
            if let retry { PillButton(title: "Try again", action: retry) }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(QuenyaTheme.coral.opacity(0.14), in: RoundedRectangle(cornerRadius: 16))
    }
}

struct LoadingLabel: View {
    let title: String
    var dark = false

    var body: some View {
        HStack(spacing: 10) {
            ProgressView().tint(QuenyaTheme.sage)
            Text(title).font(.system(size: 14)).foregroundStyle(QuenyaTheme.webSoft)
        }
        .padding(.vertical, 18)
    }
}

func gbp(_ amount: Double) -> String {
    let formatter = NumberFormatter()
    formatter.locale = Locale(identifier: "en_GB")
    formatter.numberStyle = .currency
    formatter.currencyCode = "GBP"
    formatter.currencySymbol = "£"
    formatter.usesGroupingSeparator = true
    formatter.minimumFractionDigits = amount.rounded() == amount ? 0 : 2
    formatter.maximumFractionDigits = 2
    return formatter.string(from: NSNumber(value: amount)) ?? "£\(amount)"
}

func pretty(_ key: String) -> String {
    let stripped = key.replacingOccurrences(
        of: #"^(colou?rs?|aesthetics?|styles?|categor(?:y|ies)|materials?|patterns?|fits?|eras?|brands?|slots?)[:_]"#,
        with: "",
        options: .regularExpression
    ).replacingOccurrences(of: "_", with: " ")
    if stripped == stripped.lowercased() {
        return stripped.capitalized
    }
    return stripped
}

func uniqueTasteEntries(_ entries: [(String, Double)], limit: Int? = nil) -> [(String, Double)] {
    var seen = Set<String>()
    let values = entries.filter { key, value in
        let sign = value > 0 ? "+" : "−"
        return seen.insert("\(sign):\(pretty(key).lowercased())").inserted
    }
    return limit.map { Array(values.prefix($0)) } ?? values
}

func gbpText(_ value: String) -> String {
    value.replacingOccurrences(
        of: #"(\d[\d,]*(?:\.\d+)?)\s*GBP"#,
        with: "£$1",
        options: .regularExpression
    )
}
