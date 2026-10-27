import SwiftUI

struct RootTabs: View {
    @Environment(AppState.self) private var state

    @AppStorage(TabBarStyle.storageKey) private var tabBarStyle: TabBarStyle = .system
    @State private var keyboardVisible = false
    @State private var barHeight: CGFloat = 0

    private let chromeBackground = QuenyaTheme.webPhone
    private var customBar: Bool { tabBarStyle != .system }
    private var showsCustomBar: Bool { customBar && !keyboardVisible }

    @ViewBuilder
    private var barSpacer: some View {
        if showsCustomBar {
            Color.clear.frame(height: barHeight)
        }
    }

    private var selection: Binding<AppTab> {
        Binding(
            get: { state.selectedTab },
            set: { state.setTab($0) }
        )
    }

    var body: some View {
        Group {
            switch state.authPhase {
            case .loading:
                VStack(spacing: 12) {
                    ProgressView().tint(QuenyaTheme.sage)
                    Text("Checking invite…").foregroundStyle(QuenyaTheme.muted)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(QuenyaTheme.cream.ignoresSafeArea())
            case .signedOut:
                InviteView()
            case .failed(let message):
                VStack(alignment: .leading, spacing: 16) {
                    H3(text: "Couldn't reach Quenya.")
                    ErrorPanel(message: message) { Task { await state.retryLaunch() } }
                    PillButton(title: "Retry", primary: true) { Task { await state.retryLaunch() } }
                    if state.isDebug { DebugSettingsView() }
                }
                .padding(24)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .background(QuenyaTheme.cream.ignoresSafeArea())
            case .signedIn:
                signedInContent
            }
        }
        .background(chromeBackground.ignoresSafeArea())
        .preferredColorScheme(.dark)
        .sheet(item: Binding(
            get: { state.presentedSheet },
            set: { state.presentedSheet = $0 }
        )) { sheet in
            switch sheet {
            case .item(let item):
                ItemDetailSheet(item: item)
            case .reject(let outfit):
                RejectSheet { reason in
                    state.presentedSheet = nil
                    Task { await state.verdict(outfit, like: false, reason: reason) }
                }
            case .photo(let upload):
                PhotoRouteSheet(upload: upload)
            }
        }
    }

    private var signedInContent: some View {
        VStack(spacing: 0) {
            VStack(spacing: 0) {
                HStack(alignment: .firstTextBaseline) {
                    (
                        Text("quenya").foregroundColor(QuenyaTheme.webText)
                        + Text(".").foregroundColor(QuenyaTheme.sage)
                    )
                        .font(.system(size: 19, weight: .semibold))
                    Spacer()
                    Text(state.updating ? "Updating taste…" : state.fetchNote ?? "")
                        .font(.system(size: 11))
                        .foregroundStyle(QuenyaTheme.webMuted)
                        .lineLimit(1)
                }
                .padding(.horizontal, 20)
                .padding(.top, 10)
                .padding(.bottom, 8)

                if state.selectedTab != .swipe {
                    BoardStrip()
                }

                Rectangle()
                    .fill(QuenyaTheme.webLine)
                    .frame(height: 1)
            }
            .background(chromeBackground)

            TabView(selection: selection) {
                BriefView().tag(AppTab.brief)
                    .tabItem { Label(AppTab.brief.title, systemImage: AppTab.brief.symbol) }
                    .toolbar(customBar ? .hidden : .automatic, for: .tabBar)
                    .safeAreaInset(edge: .bottom, spacing: 0) { barSpacer }
                swipeContent.tag(AppTab.swipe)
                    .tabItem { Label(AppTab.swipe.title, systemImage: AppTab.swipe.symbol) }
                    .toolbar(customBar ? .hidden : .automatic, for: .tabBar)
                    .safeAreaInset(edge: .bottom, spacing: 0) { barSpacer }
                OutfitsView().tag(AppTab.outfits)
                    .tabItem { Label(AppTab.outfits.title, systemImage: AppTab.outfits.symbol) }
                    .toolbar(customBar ? .hidden : .automatic, for: .tabBar)
                    .safeAreaInset(edge: .bottom, spacing: 0) { barSpacer }
                FindView().tag(AppTab.find)
                    .tabItem { Label(AppTab.find.title, systemImage: AppTab.find.symbol) }
                    .toolbar(customBar ? .hidden : .automatic, for: .tabBar)
                    .safeAreaInset(edge: .bottom, spacing: 0) { barSpacer }
                TasteView().tag(AppTab.taste)
                    .tabItem { Label(AppTab.taste.title, systemImage: AppTab.taste.symbol) }
                    .toolbar(customBar ? .hidden : .automatic, for: .tabBar)
                    .safeAreaInset(edge: .bottom, spacing: 0) { barSpacer }
            }
            .tint(QuenyaTheme.webText)
            .toolbarBackground(chromeBackground, for: .tabBar)
            .toolbarBackground(.visible, for: .tabBar)
            .overlay(alignment: .bottom) {
                if let toast = state.toast {
                    GeometryReader { geometry in
                        ToastView(toast: toast) {
                            state.toast = nil
                            state.setTab(.taste)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.bottom, geometry.safeAreaInsets.bottom + (showsCustomBar ? barHeight + 12 : 64))
                        .frame(maxHeight: .infinity, alignment: .bottom)
                    }
                }
            }
            .overlay(alignment: .bottom) {
                if showsCustomBar {
                    CustomTabBar(style: tabBarStyle, selection: state.selectedTab) { state.setTab($0) }
                        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { barHeight = $0 }
                }
            }
        }
        .background(chromeBackground)
        .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillShowNotification)) { _ in
            keyboardVisible = true
        }
        .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillHideNotification)) { _ in
            keyboardVisible = false
        }
    }

    @ViewBuilder
    private var swipeContent: some View {
        if state.picking {
            StylePickerView()
        } else if state.hasProfile {
            DeckView()
                .id(state.deckKey)
        } else {
            VStack(alignment: .leading, spacing: 14) {
                H3(text: "Start with a brief.")
                Text("Say what you're after first. Swipes and outfits come from it.")
                    .foregroundStyle(QuenyaTheme.muted)
                PillButton(title: "Write a brief", primary: true) { state.setTab(.brief) }
            }
            .padding(22)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(QuenyaTheme.cream)
        }
    }
}
