import SwiftUI

struct DebugSettingsView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    @State private var value = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("API base URL") {
                    TextField("https://app.quenya.co.uk", text: $value)
                        .font(.system(size: 16))
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        .foregroundStyle(QuenyaTheme.webText)
                        .tint(QuenyaTheme.sage)
                        .listRowBackground(QuenyaTheme.webSurface)
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
                .listRowBackground(QuenyaTheme.webSurface)
            }
            .scrollContentBackground(.hidden)
            .background(QuenyaTheme.webPhone)
            .quenyaPage()
            .foregroundStyle(QuenyaTheme.webText)
            .navigationTitle("Debug settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .quenyaSheet()
        .onAppear { value = state.currentBaseURLText() }
    }
}
