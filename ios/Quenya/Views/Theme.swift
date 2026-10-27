import SwiftUI

enum QuenyaTheme {
    static let cream = Color(hex: 0xF3EEE4)
    static let surface = Color(hex: 0xEAE3D6)
    static let line = Color(hex: 0xD8CFC0)
    static let ink = Color(hex: 0x15120F)
    static let softInk = Color(hex: 0x4A433B)
    static let muted = Color(hex: 0x7A7064)
    static let rust = Color(hex: 0xB4532A)
    static let rustSoft = Color(hex: 0xE7C9B8)
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
                .foregroundStyle(QuenyaTheme.softInk)
            Spacer(minLength: 8)
            if let detail {
                Text(detail)
                    .font(.system(size: 12))
                    .foregroundStyle(QuenyaTheme.muted)
            }
        }
    }
}

struct PillButton: View {
    let title: String
    var primary = false
    var disabled = false
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(primary ? QuenyaTheme.cream : QuenyaTheme.ink)
                .frame(minHeight: 44)
                .padding(.horizontal, 20)
                .background(primary ? QuenyaTheme.ink : Color.clear, in: Capsule())
                .overlay(Capsule().stroke(primary ? Color.clear : QuenyaTheme.ink, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .opacity(disabled ? 0.45 : 1)
    }
}

struct ChoicePill: View {
    let title: String
    var selected = false
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(selected ? QuenyaTheme.cream : QuenyaTheme.ink)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(selected ? QuenyaTheme.ink : QuenyaTheme.surface, in: Capsule())
        }
        .buttonStyle(.plain)
    }
}

struct ErrorPanel: View {
    let message: String
    var requestIds: [String] = []
    var retry: (() -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(message).font(.system(size: 14)).foregroundStyle(QuenyaTheme.ink)
            if !requestIds.isEmpty {
                Text("AI request \(requestIds.joined(separator: ", "))")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(QuenyaTheme.muted)
            }
            if let retry { PillButton(title: "Try again", action: retry) }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(QuenyaTheme.rustSoft.opacity(0.58), in: RoundedRectangle(cornerRadius: 16))
    }
}

struct LoadingLabel: View {
    let title: String

    var body: some View {
        HStack(spacing: 10) {
            ProgressView().tint(QuenyaTheme.rust)
            Text(title).font(.system(size: 14)).foregroundStyle(QuenyaTheme.softInk)
        }
        .padding(.vertical, 18)
    }
}

func gbp(_ amount: Double) -> String {
    "£" + (amount.rounded() == amount
           ? String(format: "%.0f", amount)
           : String(format: "%.2f", amount))
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
