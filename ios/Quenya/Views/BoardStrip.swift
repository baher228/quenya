import SwiftUI

struct BoardStrip: View {
    @Environment(AppState.self) private var state

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                if state.canCreateStyle {
                    ChoicePill(title: "+ New", selected: state.briefMode == .new && state.selectedTab == .brief) {
                        state.startNewStyle()
                    }
                }
                ForEach(state.boards) { board in
                    ChoicePill(
                        title: board.label + (board.version.map { " v\($0)" } ?? ""),
                        selected: board.active && !(state.briefMode == .new && state.selectedTab == .brief)
                    ) {
                        Task {
                            if await state.activate(board) {
                                state.selectedTab = .brief
                                state.briefMode = .edit
                            }
                        }
                    }
                }
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 10)
        }
        .background(QuenyaTheme.cream)
    }
}
