import SwiftUI

struct CustomTabBar: View {
    let selection: AppTab
    let select: (AppTab) -> Void

    @Namespace private var namespace

    var body: some View {
        mono
        .dynamicTypeSize(...DynamicTypeSize.xxLarge)
        .sensoryFeedback(.selection, trigger: selection)
    }

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

    private func choose(_ tab: AppTab) {
        guard tab != selection else { return }
        withAnimation(.spring(response: 0.38, dampingFraction: 0.8)) { select(tab) }
    }
}
