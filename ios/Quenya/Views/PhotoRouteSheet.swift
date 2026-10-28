import SwiftUI

struct PhotoRouteSheet: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let upload: UploadResult
    @State private var pendingAssignment: Assignment?
    @State private var tapCount = 0

    private struct Assignment: Equatable {
        var boardId: String?
        var label: String?
        var title: String
    }

    private var matchingBoard: Board? {
        state.boards.first {
            !$0.active && $0.label.localizedCaseInsensitiveCompare(upload.route.label) == .orderedSame
        }
    }

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 13) {
                H3(text: "This looks like \(upload.route.label).")
                Text(upload.read.oneLine).font(.system(size: 13)).foregroundStyle(QuenyaTheme.muted)
                Text(upload.route.reason).font(.system(size: 12)).foregroundStyle(QuenyaTheme.muted)
                if let matchingBoard {
                    routeButton("Add to \(matchingBoard.label)", isSelected: pendingAssignment?.boardId == matchingBoard.id) {
                        await assign(Assignment(boardId: matchingBoard.id, label: nil, title: "Add to \(matchingBoard.label)"))
                    }
                } else if state.canCreateStyle {
                    routeButton("Start a “\(upload.route.label)” board", isSelected: pendingAssignment?.label == upload.route.label) {
                        await assign(Assignment(boardId: nil, label: upload.route.label, title: "Start a “\(upload.route.label)” board"))
                    }
                }
                if let active = state.activeBoard {
                    routeButton("Keep it on \(active.label)", isSelected: pendingAssignment?.boardId == active.id) {
                        await assign(Assignment(boardId: active.id, label: nil, title: "Keep it on \(active.label)"))
                    }
                }
                if let error = state.busy.error("photo.assign"), let pendingAssignment {
                    ErrorPanel(message: error) { Task { await assign(pendingAssignment) } }
                }
                Spacer()
                PillButton(title: "Decide later", disabled: state.busy.isBusy("photo.assign")) {
                    state.presentedSheet = nil
                    dismiss()
                }
                .frame(maxWidth: .infinity)
            }
            .quenyaPage()
            .padding(22)
            .frame(maxHeight: .infinity, alignment: .topLeading)
            .background(QuenyaTheme.webPhone)
            .navigationTitle("Inspiration photo")
            .navigationBarTitleDisplayMode(.inline)
        }
        .quenyaSheet()
        .presentationDetents([.medium, .large])
        .sensoryFeedback(.impact(weight: .light), trigger: tapCount)
    }

    private func routeButton(_ title: String, isSelected: Bool, action: @escaping () async -> Void) -> some View {
        Button {
            tapCount += 1
            pendingAssignment = Assignment(
                boardId: title.hasPrefix("Add to ") ? matchingBoard?.id : title.hasPrefix("Keep it on ") ? state.activeBoard?.id : nil,
                label: title.hasPrefix("Start a ") ? upload.route.label : nil,
                title: title
            )
            Task { await action() }
        } label: {
            HStack {
                Text(title).foregroundStyle(QuenyaTheme.ink).opacity(isSelected && state.busy.isBusy("photo.assign") ? 0 : 1)
                Spacer()
                if isSelected && state.busy.isBusy("photo.assign") {
                    ProgressView().controlSize(.small).tint(QuenyaTheme.sage)
                } else {
                    Image(systemName: "arrow.right").foregroundStyle(QuenyaTheme.rust)
                }
            }
            .padding(13)
            .background(QuenyaTheme.webSurface2, in: RoundedRectangle(cornerRadius: 13))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(state.busy.isBusy("photo.assign"))
        .accessibilityLabel(isSelected && state.busy.isBusy("photo.assign") ? "Loading" : title)
    }

    private func assign(_ assignment: Assignment) async {
        pendingAssignment = assignment
        _ = await state.assignPhoto(upload, boardId: assignment.boardId, label: assignment.label)
    }
}
