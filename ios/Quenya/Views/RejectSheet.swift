import SwiftUI

struct RejectSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var ownReason = ""
    let onPick: (String) -> Void
    private let reasons = [
        "Too generic", "Colours don't work together", "Too expensive",
        "Wrong fit for me", "Too costume-y"
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    H3(text: "What's off?")
                    Text("Your answer is a correction: your taste updates now and helps personalise later AI suggestions.")
                        .font(.system(size: 13)).foregroundStyle(QuenyaTheme.muted)
                    ForEach(reasons, id: \.self) { reason in
                        Button { onPick(reason) } label: {
                            HStack {
                                Text(reason).foregroundStyle(QuenyaTheme.ink)
                                Spacer()
                                Image(systemName: "arrow.right").foregroundStyle(QuenyaTheme.rust)
                            }
                            .padding(13)
                            .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 13))
                        }
                        .buttonStyle(.plain)
                    }
                    TextField("Or say it yourself: “no square-toe shoes”", text: $ownReason)
                        .padding(12)
                        .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 12))
                        .onChange(of: ownReason) { _, value in if value.count > 200 { ownReason = String(value.prefix(200)) } }
                    PillButton(title: "Send", primary: true, disabled: ownReason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) {
                        onPick(ownReason.trimmingCharacters(in: .whitespacesAndNewlines))
                    }
                    .frame(maxWidth: .infinity)
                }
                .padding(20)
            }
            .background(QuenyaTheme.cream)
            .navigationTitle("Not for me")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Cancel") { dismiss() }.foregroundStyle(QuenyaTheme.ink)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}
