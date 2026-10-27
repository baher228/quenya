import SwiftUI
import UIKit

enum TabBarStyle: String, CaseIterable, Identifiable {
    case system, mono, capsule, index

    static let storageKey = "QuenyaTabBarStyle"

    var id: String { rawValue }
    var title: String {
        switch self {
        case .system: "Current (system)"
        case .mono: "A · Mono text"
        case .capsule: "B · Floating capsule"
        case .index: "C · Index blocks"
        }
    }
}

struct CustomTabBar: View {
    let style: TabBarStyle
    let selection: AppTab
    let select: (AppTab) -> Void

    @Namespace private var namespace

    var body: some View {
        Group {
            switch style {
            case .system: EmptyView()
            case .mono: mono
            case .capsule: capsule
            case .index: index
            }
        }
        .dynamicTypeSize(...DynamicTypeSize.xxLarge)
        .sensoryFeedback(.selection, trigger: selection)
    }

    // MARK: A · Mono text

    private var mono: some View {
        HStack(spacing: 0) {
            ForEach(AppTab.allCases) { tab in
                let active = tab == selection
                Button { choose(tab) } label: {
                    VStack(spacing: 7) {
                        Text(tab.title.uppercased())
                            .font(.system(size: 11, weight: active ? .semibold : .regular, design: .monospaced))
                            .tracking(0.88)
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                            .foregroundStyle(active ? QuenyaTheme.webText : QuenyaTheme.webMuted)
                        ZStack {
                            Color.clear.frame(height: 2)
                            if active {
                                Rectangle()
                                    .fill(QuenyaTheme.coral)
                                    .frame(width: 22, height: 2)
                                    .matchedGeometryEffect(id: "mono-underline", in: namespace)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, minHeight: 48)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(tab.title)
                .accessibilityAddTraits(active ? .isSelected : [])
            }
        }
        .padding(.horizontal, 8)
        .padding(.top, 6)
        .background(Color(hex: 0x0E0C0A).ignoresSafeArea(edges: .bottom))
        .overlay(alignment: .top) {
            Rectangle().fill(QuenyaTheme.webLine).frame(height: 1)
        }
    }

    // MARK: B · Floating capsule

    private var capsule: some View {
        HStack(spacing: 2) {
            ForEach(AppTab.allCases) { tab in
                let active = tab == selection
                Button { choose(tab) } label: {
                    HStack(spacing: 6) {
                        Image(systemName: tab.symbol)
                            .font(.system(size: 16, weight: .medium))
                        if active {
                            Text(tab.title)
                                .font(.system(size: 13, weight: .semibold))
                                .lineLimit(1)
                                .fixedSize()
                                .transition(.opacity.combined(with: .scale(scale: 0.8, anchor: .leading)))
                        }
                    }
                    .foregroundStyle(active ? QuenyaTheme.webPhone : QuenyaTheme.webMuted)
                    .padding(.horizontal, active ? 14 : 0)
                    .frame(maxWidth: active ? nil : .infinity, minHeight: 44)
                    .background {
                        if active {
                            Capsule()
                                .fill(QuenyaTheme.webText)
                                .matchedGeometryEffect(id: "capsule-pill", in: namespace)
                        }
                    }
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(tab.title)
                .accessibilityAddTraits(active ? .isSelected : [])
            }
        }
        .padding(5)
        .background(
            Capsule()
                .fill(QuenyaTheme.webPhone)
                .shadow(color: .black.opacity(0.45), radius: 14, y: 6)
        )
        .overlay(Capsule().stroke(QuenyaTheme.webLine, lineWidth: 1))
        .padding(.horizontal, 16)
        .padding(.top, 8)
        .padding(.bottom, 6)
        .frame(maxWidth: .infinity)
        .background(QuenyaTheme.webPhone.ignoresSafeArea(edges: .bottom))
    }

    // MARK: C · Index blocks

    private var index: some View {
        HStack(spacing: 0) {
            ForEach(Array(AppTab.allCases.enumerated()), id: \.element) { offset, tab in
                let active = tab == selection
                if offset > 0 {
                    Rectangle().fill(QuenyaTheme.webLine).frame(width: 1)
                }
                Button { choose(tab) } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(String(format: "%02d", offset + 1))
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(active ? QuenyaTheme.webPhone.opacity(0.6) : QuenyaTheme.webDim)
                        Text(tab.title.uppercased())
                            .font(.system(size: 11, weight: .semibold, design: .monospaced))
                            .tracking(0.6)
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                            .foregroundStyle(active ? QuenyaTheme.webPhone : QuenyaTheme.webMuted)
                    }
                    .padding(.horizontal, 8)
                    .frame(maxWidth: .infinity, minHeight: 50, alignment: .leading)
                    .background(active ? QuenyaTheme.webText : Color.clear)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(tab.title)
                .accessibilityAddTraits(active ? .isSelected : [])
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .overlay(Rectangle().stroke(QuenyaTheme.webLine, lineWidth: 1))
        .padding(.horizontal, 12)
        .padding(.top, 8)
        .padding(.bottom, 6)
        .background(QuenyaTheme.webPhone.ignoresSafeArea(edges: .bottom))
        .overlay(alignment: .top) {
            Rectangle().fill(QuenyaTheme.webLine).frame(height: 1)
        }
    }

    private func choose(_ tab: AppTab) {
        guard tab != selection else { return }
        withAnimation(.spring(response: 0.38, dampingFraction: 0.8)) { select(tab) }
    }
}
