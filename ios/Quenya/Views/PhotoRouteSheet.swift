import SwiftUI

struct PhotoRouteSheet: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let upload: UploadResult

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
                    routeButton("Add to \(matchingBoard.label)") {
                        Task { await state.assignPhoto(upload, boardId: matchingBoard.id) }
                    }
                } else if state.canCreateStyle {
                    routeButton("Start a “\(upload.route.label)” board") {
                        Task { await state.assignPhoto(upload, boardId: nil, label: upload.route.label) }
                    }
                }
                if let active = state.activeBoard {
                    routeButton("Keep it on \(active.label)") {
                        Task { await state.assignPhoto(upload, boardId: active.id) }
                    }
                }
                Spacer()
                PillButton(title: "Decide later") {
                    state.presentedSheet = nil
                    dismiss()
                }
                .frame(maxWidth: .infinity)
            }
            .padding(22)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(QuenyaTheme.cream)
            .navigationTitle("Inspiration photo")
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.medium, .large])
    }

    private func routeButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Text(title).foregroundStyle(QuenyaTheme.ink)
                Spacer()
                Image(systemName: "arrow.right").foregroundStyle(QuenyaTheme.rust)
            }
            .padding(13)
            .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 13))
        }
        .buttonStyle(.plain)
    }
}
