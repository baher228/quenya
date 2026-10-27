import PhotosUI
import SwiftUI
import UIKit

struct FindView: View {
    @Environment(AppState.self) private var state
    @State private var query = ""
    @State private var items: [Item]?
    @State private var mode: String?
    @State private var error: String?
    @State private var selection: PhotosPickerItem?
    @State private var uploading = false
    @State private var uploadError: String?

    private let examples = ["brown suede loafers", "cable knit jumper", "wax jacket", "wide-leg trousers"]
    private let columns = [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Eyebrow(text: "Single pieces")
                H1(text: "Looking for one thing?")
                HStack(spacing: 8) {
                    TextField("e.g. brown suede loafers", text: $query)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .padding(13)
                        .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 13))
                        .onSubmit { Task { await search(live: false) } }
                    PillButton(title: "Search", primary: true, disabled: query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || mode != nil) {
                        Task { await search(live: false) }
                    }
                }
                Text("Ranked by your taste. Search Vinted live to fetch fresh listings, which get tagged as they arrive.")
                    .font(.system(size: 13)).foregroundStyle(QuenyaTheme.muted)
                if items == nil && mode == nil {
                    FlowLayout(spacing: 7) {
                        ForEach(examples, id: \.self) { example in
                            ChoicePill(title: example) {
                                query = example
                                Task { await search(live: false, value: example) }
                            }
                        }
                    }
                }
                if let mode { LoadingLabel(title: mode == "live" ? "Fetching and tagging live listings" : "Searching") }
                if let error { ErrorPanel(message: error) { Task { await search(live: mode == "live") } } }
                if let items, items.isEmpty {
                    Text("Nothing in the pool matches. Try searching Vinted live.")
                        .font(.system(size: 14)).foregroundStyle(QuenyaTheme.muted)
                }
                if let items, !items.isEmpty {
                    LazyVGrid(columns: columns, spacing: 12) {
                        ForEach(items) { item in
                            Button { state.presentedSheet = .item(item) } label: {
                                VStack(alignment: .leading, spacing: 6) {
                                    RemoteImage(path: item.imagePath)
                                        .frame(height: 145)
                                        .clipShape(RoundedRectangle(cornerRadius: 13))
                                    Text(gbp(item.priceGbp)).font(.system(size: 12, weight: .semibold))
                                    Text(item.title).font(.system(size: 12)).lineLimit(2).foregroundStyle(QuenyaTheme.ink)
                                    Text(item.source == "vinted" ? "Vinted" : item.source == "retail" ? "Retail" : item.source)
                                        .font(.system(size: 10)).foregroundStyle(QuenyaTheme.muted)
                                }
                                .foregroundStyle(QuenyaTheme.ink)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                if items != nil || error != nil {
                    PillButton(
                        title: "Search Vinted live for “\(query)”",
                        disabled: query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || mode != nil
                    ) { Task { await search(live: true) } }
                    .frame(maxWidth: .infinity)
                }

                VStack(alignment: .leading, spacing: 10) {
                    FieldLabel(text: "Inspiration photo")
                    Text("Upload a look you like. The photo reader picks out garments, palette and era, then your taste updates.")
                        .font(.system(size: 13)).foregroundStyle(QuenyaTheme.muted)
                    if let uploadError { ErrorPanel(message: uploadError) }
                    PhotosPicker(selection: $selection, matching: .images) {
                        HStack {
                            Image(systemName: "camera")
                            Text(uploading ? "Reading photo…" : "+ Add a photo")
                            Spacer()
                            if uploading { ProgressView().tint(QuenyaTheme.rust) }
                        }
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(QuenyaTheme.ink)
                        .padding(14)
                        .overlay(RoundedRectangle(cornerRadius: 15).stroke(QuenyaTheme.line, lineWidth: 1))
                    }
                    .disabled(uploading)
                }
                .padding(15)
                .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 18))
            }
            .padding(18)
        }
        .background(QuenyaTheme.cream)
        .onChange(of: selection) { _, value in
            guard let value else { return }
            Task { await upload(value) }
        }
    }

    private func search(live: Bool, value: String? = nil) async {
        let term = (value ?? query).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty else { return }
        query = term
        mode = live ? "live" : "saved"
        error = nil
        items = nil
        defer { mode = nil }
        do { items = live ? try await state.api.searchLive(q: term) : try await state.api.search(q: term) }
        catch { self.error = error.localizedDescription }
    }

    private func upload(_ item: PhotosPickerItem) async {
        uploading = true
        uploadError = nil
        defer { uploading = false; selection = nil }
        do {
            guard let data = try await item.loadTransferable(type: Data.self),
                  let jpeg = makeJPEG(data) else {
                throw APIError(status: 0, message: "Couldn't read that photo.", requestIds: [])
            }
            try await state.upload(jpeg)
        } catch {
            uploadError = error.localizedDescription
        }
    }

    private func makeJPEG(_ data: Data) -> Data? {
        guard let image = UIImage(data: data) else { return nil }
        let longest = max(image.size.width, image.size.height)
        let finalImage: UIImage
        if longest > 1600 {
            let scale = 1600 / longest
            let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
            UIGraphicsBeginImageContextWithOptions(size, true, 1)
            image.draw(in: CGRect(origin: .zero, size: size))
            finalImage = UIGraphicsGetImageFromCurrentImageContext() ?? image
            UIGraphicsEndImageContext()
        } else { finalImage = image }
        return finalImage.jpegData(compressionQuality: 0.85)
    }
}
