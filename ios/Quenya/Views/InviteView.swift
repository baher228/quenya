import SwiftUI

struct InviteView: View {
    @Environment(AppState.self) private var state
    @State private var input = ""
    @State private var showingDebug = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Spacer(minLength: 80)
                Text("Quenya is invite-only for now.")
                    .font(.system(size: 34, weight: .semibold, design: .serif))
                    .foregroundStyle(QuenyaTheme.ink)
                Text("Paste your invite link.")
                    .font(.system(size: 16))
                    .foregroundStyle(QuenyaTheme.softInk)
                TextField("Invite link or token", text: $input, axis: .vertical)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .padding(16)
                    .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 14))
                    .lineLimit(1...3)
                PillButton(title: "Continue", primary: true, disabled: input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) {
                    Task { await state.redeemInvite(input) }
                }
                if case .signedOut(let message) = state.authPhase, let message {
                    ErrorPanel(message: message)
                }
                if state.isDebug {
                    Button("Debug") { showingDebug = true }
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(QuenyaTheme.muted)
                        .padding(.top, 12)
                }
                Spacer(minLength: 40)
            }
            .padding(24)
        }
        .background(QuenyaTheme.cream.ignoresSafeArea())
        .sheet(isPresented: $showingDebug) { DebugSettingsView() }
    }
}
