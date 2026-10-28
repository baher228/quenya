import SwiftUI

struct BoardStrip: View {
    @Environment(AppState.self) private var state

    private var activating: Bool {
        state.boards.contains { state.busy.isBusy("style.activate.\($0.id)") }
    }

    var body: some View {
        VStack(spacing: 4) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    if state.canCreateStyle {
                        ChoicePill(
                            title: "+ New",
                            selected: state.briefMode == .new && state.selectedTab == .brief,
                            dark: true,
                            outlined: state.selectedTab == .outfits,
                            disabled: activating
                        ) {
                            state.startNewStyle()
                        }
                    }
                    ForEach(state.boards) { board in
                        let key = "style.activate.\(board.id)"
                    ChoicePill(
                        title: board.label + (board.version.map { " v\($0)" } ?? ""),
                        selected: board.active && !(state.briefMode == .new && state.selectedTab == .brief),
                        dark: true,
                        busy: state.busy.isBusy(key),
                        disabled: activating && !state.busy.isBusy(key)
                    ) {
                        Task { await state.activateFromStrip(board) }
                    }
                    }
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 10)
            }
            if let board = state.boards.first(where: { state.busy.error("style.activate.\($0.id)") != nil }),
               let message = state.busy.error("style.activate.\(board.id)") {
                HStack(spacing: 8) {
                    Text(message)
                        .font(.system(size: 11))
                        .foregroundStyle(QuenyaTheme.webMuted)
                        .lineLimit(2)
                    Spacer(minLength: 4)
                    Button("Retry") { Task { _ = await state.activate(board) } }
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(QuenyaTheme.sage)
                }
                .padding(.horizontal, 18)
                .padding(.bottom, 8)
            }
        }
        .background(QuenyaTheme.webPhone)
    }
}
