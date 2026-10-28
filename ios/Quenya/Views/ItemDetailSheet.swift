import SwiftUI

struct ItemDetailSheet: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    let item: Item
    @State private var note = ""
    @State private var busy = BusyTracker()
    @State private var requestId: String?
    @State private var safari: SafariTarget?
    @State private var tags: ItemTags?

    private var provenance: String {
        let source = item.source == "vinted" ? "Vinted" : item.source == "retail" ? "Retail" : item.source
        return [source, tags?.condition ?? item.condition].compactMap { $0 }.joined(separator: " · ")
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    RemoteImage(path: item.imagePath)
                        .frame(height: 310)
                        .clipShape(RoundedRectangle(cornerRadius: 22))
                    Text(item.brand ?? "Unbranded")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(QuenyaTheme.rust)
                    H3(text: item.title)
                    Text("\(gbp(item.priceGbp))\(item.size.map { " · size \($0)" } ?? "")")
                        .font(.system(size: 15, weight: .semibold)).foregroundStyle(QuenyaTheme.ink)
                    Text(provenance).font(.system(size: 12)).foregroundStyle(QuenyaTheme.muted)

                    if let tags {
                        VStack(spacing: 0) {
                            tagRow("Read as", tags.oneLine)
                            tagRow("Category", "\(tags.slot) · \(tags.category)")
                            tagRow("Style", tags.aesthetics.joined(separator: ", "))
                            tagRow("Colour", tags.colours.joined(separator: ", ") + (tags.era == "unknown" ? "" : " · \(tags.era)"))
                        }
                        .padding(12)
                        .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 16))
                    }

                    VStack(alignment: .leading, spacing: 9) {
                        FieldLabel(text: "Fix a tag")
                        HStack(spacing: 8) {
                            TextField("“this is 90s, not y2k”", text: $note)
                                .font(.system(size: 16))
                                .textInputAutocapitalization(.sentences)
                                .padding(12)
                                .background(QuenyaTheme.webSurface2, in: RoundedRectangle(cornerRadius: 12))
                                .overlay(RoundedRectangle(cornerRadius: 12).stroke(QuenyaTheme.webLine2, lineWidth: 1))
                                .onChange(of: note) { _, value in if value.count > 300 { note = String(value.prefix(300)) } }
                            PillButton(title: "Fix", primary: true, disabled: note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, busy: busy.isBusy("item.tags")) {
                                Task { await correct() }
                            }
                        }
                        if let requestId {
                            Text("Re-tagged with your correction · \(requestId)")
                                .font(.system(size: 11, design: .monospaced)).foregroundStyle(QuenyaTheme.muted)
                        }
                        if let error = busy.error("item.tags") {
                            ErrorPanel(message: error) { Task { await correct() } }
                        }
                    }

                    PillButton(title: listingTitle, primary: true) {
                        if let url = URL(string: item.sourceUrl) { safari = SafariTarget(url: url) }
                    }
                    .frame(maxWidth: .infinity)
                }
                .quenyaPage()
                .padding(18)
            }
            .background(QuenyaTheme.webPhone)
            .navigationTitle("Listing")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(QuenyaTheme.webPhone, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Close") { dismiss() }.foregroundStyle(QuenyaTheme.ink)
                }
            }
        }
        .quenyaSheet()
        .onAppear { tags = item.tags }
        .fullScreenCover(item: $safari) { target in SafariView(url: target.url) }
    }

    private var listingTitle: String {
        let host = URL(string: item.sourceUrl)?.host?.replacingOccurrences(of: "www.", with: "") ?? "listing"
        return "View listing on \(item.source == "vinted" ? "Vinted" : host)"
    }

    private func tagRow(_ title: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(title).font(.system(size: 11, weight: .semibold)).foregroundStyle(QuenyaTheme.muted).frame(width: 68, alignment: .leading)
            Text(value.isEmpty ? "—" : value).font(.system(size: 12)).foregroundStyle(QuenyaTheme.ink).frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 7)
    }

    private func correct() async {
        await busy.run("item.tags") {
            let result = try await state.correctTags(item: item, note: note.trimmingCharacters(in: .whitespacesAndNewlines))
            self.tags = result.tags
            self.requestId = result.requestId
            self.note = ""
        }
    }
}
