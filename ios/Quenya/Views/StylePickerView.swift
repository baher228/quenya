import SwiftUI

struct StylePickerView: View {
    @Environment(AppState.self) private var state
    @State private var showingDebug = false
    @State private var tapCount = 0

    private var activating: Bool {
        state.boards.contains { state.busy.isBusy("style.activate.\($0.id)") }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Eyebrow(text: "Swipe")
                H3(text: "Which style today?")
                if state.boards.isEmpty {
                    Text("Start with a brief.")
                        .font(.system(size: 15))
                        .foregroundStyle(QuenyaTheme.muted)
                    PillButton(title: "Write a brief", primary: true) {
                        state.setTab(.brief)
                    }
                } else {
                    VStack(spacing: 0) {
                        ForEach(state.boards) { board in
                            let key = "style.activate.\(board.id)"
                            Button {
                                tapCount += 1
                                Task { await state.selectBoardForSwipe(board) }
                            } label: {
                                HStack(spacing: 10) {
                                    VStack(alignment: .leading, spacing: 5) {
                                        Text(board.label)
                                            .font(.system(size: 17, weight: .semibold))
                                            .foregroundStyle(QuenyaTheme.ink)
                                        Text("\(board.context) · \(board.version.map { "taste v\($0) · " } ?? "")\(board.counts.swipes) swiped")
                                            .font(.system(size: 12))
                                            .foregroundStyle(QuenyaTheme.muted)
                                    }
                                    Spacer()
                                    if state.busy.isBusy(key) {
                                        ProgressView().controlSize(.small).tint(QuenyaTheme.sage)
                                    } else {
                                        Image(systemName: "arrow.right")
                                            .foregroundStyle(QuenyaTheme.rust)
                                    }
                                }
                                .padding(.vertical, 17)
                                .padding(.horizontal, 16)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .disabled(activating && !state.busy.isBusy(key))
                            .accessibilityLabel(state.busy.isBusy(key) ? "Loading" : board.label)
                            .sensoryFeedback(.impact(weight: .light), trigger: tapCount)
                            if board.id != state.boards.last?.id || state.canCreateStyle {
                                Divider().overlay(QuenyaTheme.line)
                            }
                        }
                        if state.canCreateStyle {
                            Button { state.startNewStyle() } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 5) {
                                        Text("New style").font(.system(size: 16, weight: .semibold)).foregroundStyle(QuenyaTheme.ink)
                                        Text("Write a brief").font(.system(size: 12)).foregroundStyle(QuenyaTheme.muted)
                                    }
                                    Spacer()
                                    Image(systemName: "plus").foregroundStyle(QuenyaTheme.rust)
                                }
                                .padding(16)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 20))
                    if let board = state.boards.first(where: { state.busy.error("style.activate.\($0.id)") != nil }),
                       let error = state.busy.error("style.activate.\(board.id)") {
                        ErrorPanel(message: error) { Task { await state.selectBoardForSwipe(board) } }
                    }
                }
                Button {
                    tapCount += 1
                    Task { await state.signOut() }
                } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 5) {
                            Text("Sign out").foregroundStyle(QuenyaTheme.ink)
                            Text("Use another invite link").font(.system(size: 12)).foregroundStyle(QuenyaTheme.muted)
                        }
                        Spacer()
                        if state.busy.isBusy("auth.signout") {
                            ProgressView().controlSize(.small).tint(QuenyaTheme.sage)
                        } else {
                            Image(systemName: "arrow.right").foregroundStyle(QuenyaTheme.muted)
                        }
                    }
                    .padding(.vertical, 14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(activating || state.busy.isBusy("auth.signout"))
                .accessibilityLabel(state.busy.isBusy("auth.signout") ? "Loading" : "Sign out")
                .sensoryFeedback(.impact(weight: .light), trigger: tapCount)
                if state.isDebug {
                    Button("Debug") { showingDebug = true }
                        .font(.system(size: 13))
                        .foregroundStyle(QuenyaTheme.muted)
                }
            }
            .quenyaPage()
            .padding(18)
        }
        .scrollContentBackground(.hidden)
        .background(QuenyaTheme.webPhone.ignoresSafeArea())
        .sheet(isPresented: $showingDebug) { DebugSettingsView() }
        .quenyaSheet()
    }
}
