import SwiftUI

@main
struct QuenyaApp: App {
    @State private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            RootTabs()
                .environment(appState)
                .tint(QuenyaTheme.ink)
                .task { await appState.launch() }
                .onOpenURL { url in
                    guard ["quenya", "https"].contains(url.scheme?.lowercased() ?? "") else { return }
                    Task { await appState.redeemInvite(url.absoluteString) }
                }
        }
    }
}
