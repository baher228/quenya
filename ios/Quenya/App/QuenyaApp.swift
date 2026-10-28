import SwiftUI

@main
struct QuenyaApp: App {
    @State private var appState = AppState()
    @State private var completedInitialLaunch = false
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootTabs()
                .environment(appState)
                .tint(QuenyaTheme.webText)
                .task {
                    await appState.launch()
                    completedInitialLaunch = true
                }
                .onChange(of: scenePhase) { _, phase in
                    guard completedInitialLaunch else { return }
                    switch phase {
                    case .active:
                        Task { await appState.sceneBecameActive() }
                    case .background:
                        Task { await appState.flushCache() }
                    default:
                        break
                    }
                }
                .onOpenURL { url in
                    guard ["quenya", "https"].contains(url.scheme?.lowercased() ?? "") else { return }
                    Task { await appState.redeemInvite(url.absoluteString) }
                }
        }
    }
}
