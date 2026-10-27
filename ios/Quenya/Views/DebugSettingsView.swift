import SwiftUI

struct DebugSettingsView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    @State private var value = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("API base URL") {
                    TextField("https://quenya-rho.vercel.app", text: $value)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    Text("Current: \(state.currentBaseURLText())")
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundStyle(QuenyaTheme.muted)
                    Button("Use local (http://localhost:8000)") {
                        state.changeBaseURL("http://localhost:8000")
                        dismiss()
                    }
                    Button("Apply URL") {
                        state.changeBaseURL(value)
                        dismiss()
                    }
                    Button("Reset to default") {
                        state.changeBaseURL(nil)
                        dismiss()
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(QuenyaTheme.cream)
            .navigationTitle("Debug settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .onAppear { value = state.currentBaseURLText() }
    }
}
