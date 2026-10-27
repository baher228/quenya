import SwiftUI

struct ToastView: View {
    let toast: ToastMessage
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    if toast.isTaste { Text("AI").font(.system(size: 10, weight: .bold)).padding(5).background(QuenyaTheme.rustSoft, in: Circle()) }
                    Text(toast.title).font(.system(size: 14, weight: .semibold))
                    Spacer()
                    if toast.isTaste { Text("updated").font(.system(size: 11)).foregroundStyle(QuenyaTheme.muted) }
                }
                ForEach(Array(toast.lines.enumerated()), id: \.offset) { _, line in
                    Text(line).font(.system(size: 12)).foregroundStyle(QuenyaTheme.softInk).lineLimit(1)
                }
            }
            .foregroundStyle(QuenyaTheme.ink)
            .padding(14)
            .background(QuenyaTheme.surface, in: Capsule())
            .overlay(Capsule().stroke(QuenyaTheme.line, lineWidth: 1))
            .shadow(color: .black.opacity(0.08), radius: 10, y: 3)
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 16)
        .padding(.bottom, 8)
    }
}
