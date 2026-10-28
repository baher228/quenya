import SwiftUI

struct RejectSheet: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let boardId: String
    @State private var ownReason = ""
    @State private var pendingReason = ""
    @FocusState private var reasonFocused: Bool
    let outfit: Outfit
    private let reasons = [
        "Too generic", "Colours don't work together", "Too expensive",
        "Wrong fit for me", "Too costume-y"
    ]

    private var verdictKey: String { "verdict.\(outfit.id)" }
    private var busy: Bool { state.busy.isBusy(verdictKey) }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    H3(text: "What's off?")
                    Text("Your answer is a correction: your taste updates now and helps personalise later AI suggestions.")
                        .font(.system(size: 13)).foregroundStyle(QuenyaTheme.muted)
                    ForEach(reasons, id: \.self) { reason in
                        Button {
                            pendingReason = reason
                            Task { await submit(reason) }
                        } label: {
                            HStack {
                                Text(reason).foregroundStyle(QuenyaTheme.ink)
                                Spacer()
                                if busy { ProgressView().tint(QuenyaTheme.sage) }
                                else { Image(systemName: "arrow.right").foregroundStyle(QuenyaTheme.rust) }
                            }
                            .padding(13)
                            .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 13))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .disabled(busy)
                    }
                    TextField("Or say it yourself: “no square-toe shoes”", text: $ownReason)
                        .font(.system(size: 16))
                        .focused($reasonFocused)
                        .submitLabel(.send)
                        .onSubmit {
                            reasonFocused = false
                            pendingReason = ownReason.trimmingCharacters(in: .whitespacesAndNewlines)
                            Task { await submit(pendingReason) }
                        }
                        .padding(12)
                        .background(QuenyaTheme.webSurface2, in: RoundedRectangle(cornerRadius: 12))
                        .overlay(RoundedRectangle(cornerRadius: 12).stroke(QuenyaTheme.webLine2, lineWidth: 1))
                        .onChange(of: ownReason) { _, value in if value.count > 200 { ownReason = String(value.prefix(200)) } }
                    PillButton(
                        title: "Send",
                        primary: true,
                        disabled: ownReason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                        busy: busy
                    ) {
                        pendingReason = ownReason.trimmingCharacters(in: .whitespacesAndNewlines)
                        Task { await submit(pendingReason) }
                    }
                    .frame(maxWidth: .infinity)
                    if let error = state.busy.error(verdictKey) {
                        ErrorPanel(message: error) { Task { await submit(pendingReason) } }
                    }
                }
                .quenyaPage()
                .padding(20)
            }
            .background(QuenyaTheme.webPhone)
            .scrollDismissesKeyboard(.interactively)
            .dismissKeyboardOnTap()
            .navigationTitle("Not for me")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Cancel") { dismiss() }.foregroundStyle(QuenyaTheme.ink)
                }
            }
        }
        .quenyaSheet()
        .presentationDetents([.medium, .large])
        .keyboardDoneToolbar()
    }

    private func submit(_ reason: String) async {
        guard !reason.isEmpty else { return }
        if await state.verdict(outfit, like: false, reason: reason, styleId: boardId) { dismiss() }
    }
}
