import SwiftUI

struct RootTabs: View {
    @Environment(AppState.self) private var state

    @State private var keyboardVisible = false
    @State private var barHeight: CGFloat = 0

    private let chromeBackground = QuenyaTheme.webPhone
    private var showsCustomBar: Bool { !keyboardVisible }

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
            case .reject(let outfit, let boardId):
                RejectSheet(boardId: boardId, outfit: outfit)
            case .photo(let upload):
                PhotoRouteSheet(upload: upload)
            }
        }
    }

    private var signedInContent: some View {
        VStack(spacing: 0) {
            VStack(spacing: 0) {
                if let fetchNote = state.fetchNote, !fetchNote.isEmpty {
                    Text(fetchNote)
                        .font(.system(size: 11))
                        .foregroundStyle(QuenyaTheme.webMuted)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 20)
                        .padding(.top, 10)
                        .padding(.bottom, 8)
                }

                if state.selectedTab != .swipe {
                    BoardStrip()
                }
            }
            .background(chromeBackground)

            TabView(selection: selection) {
                BriefView().tag(AppTab.brief)
                    .toolbar(.hidden, for: .tabBar)
                    .safeAreaInset(edge: .bottom, spacing: 0) { barSpacer }
                swipeContent.tag(AppTab.swipe)
                    .toolbar(.hidden, for: .tabBar)
                    .safeAreaInset(edge: .bottom, spacing: 0) { barSpacer }
                OutfitsView().tag(AppTab.outfits)
                    .toolbar(.hidden, for: .tabBar)
                    .safeAreaInset(edge: .bottom, spacing: 0) { barSpacer }
                FindView().tag(AppTab.find)
                    .toolbar(.hidden, for: .tabBar)
                    .safeAreaInset(edge: .bottom, spacing: 0) { barSpacer }
                TasteView().tag(AppTab.taste)
                    .toolbar(.hidden, for: .tabBar)
                    .safeAreaInset(edge: .bottom, spacing: 0) { barSpacer }
            }
            .overlay(alignment: .bottom) {
                if let toast = state.toast {
                    GeometryReader { geometry in
                        ToastView(toast: toast) {
                            state.toast = nil
                            state.setTab(.taste)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.bottom, geometry.safeAreaInsets.bottom + (showsCustomBar ? barHeight + 12 : 12))
                        .frame(maxHeight: .infinity, alignment: .bottom)
                    }
                }
            }
            .overlay(alignment: .bottom) {
                if showsCustomBar {
                    CustomTabBar(selection: state.selectedTab) { state.setTab($0) }
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
