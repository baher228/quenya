import PhotosUI
import SwiftUI
import UIKit

struct FindView: View {
    @Environment(AppState.self) private var state
    @State private var busy = BusyTracker()
    @State private var query = ""
    @State private var items: [Item]?
    @State private var mode: String?
    @State private var error: String?
    @State private var selection: PhotosPickerItem?
    @State private var uploadError: String?
    @State private var searchGeneration = 0
    @State private var lastSearchLive = false
    @State private var retrySelection: PhotosPickerItem?
    @State private var tapCount = 0
    @FocusState private var queryFocused: Bool

    private var uploading: Bool { busy.isBusy("photo.upload") }

    private let examples = ["brown suede loafers", "cable knit jumper", "wax jacket", "wide-leg trousers"]
    private let columns = [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Eyebrow(text: "Single pieces")
                H1(text: "Looking for one thing?")
                HStack(spacing: 8) {
                    TextField("e.g. brown suede loafers", text: $query)
                        .font(.system(size: 16))
                        .focused($queryFocused)
                        .submitLabel(.search)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .padding(13)
                        .background(QuenyaTheme.webSurface2, in: RoundedRectangle(cornerRadius: 12))
                        .overlay(RoundedRectangle(cornerRadius: 12).stroke(QuenyaTheme.webLine2, lineWidth: 1))
                        .onSubmit {
                            queryFocused = false
                            Task { await search(live: false) }
                        }
                        .keyboardDoneToolbar()
                    PillButton(title: "Search", primary: true, disabled: query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, busy: mode == "saved") {
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
                if let mode {
                    LoadingLabel(title: mode == "live" ? "Fetching and tagging live listings" : "Searching")
                    VStack(spacing: 10) {
                        ForEach(0..<4, id: \.self) { _ in FindSkeletonRow() }
                    }
                    .skeletonPulse()
                    .accessibilityHidden(true)
                }
                if let error { ErrorPanel(message: error) { Task { await search(live: lastSearchLive) } } }
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
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                if items != nil || error != nil {
                    PillButton(
                        title: "Search Vinted live for “\(query)”",
                        disabled: query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                        busy: mode == "live"
                    ) { Task { await search(live: true) } }
                    .frame(maxWidth: .infinity)
                }

                VStack(alignment: .leading, spacing: 10) {
                    FieldLabel(text: "Inspiration photo")
                    Text("Upload a look you like. The photo reader picks out garments, palette and era, then your taste updates.")
                        .font(.system(size: 13)).foregroundStyle(QuenyaTheme.muted)
                    if let uploadError {
                        ErrorPanel(message: uploadError) {
                            if let retrySelection { Task { await upload(retrySelection) } }
                        }
                    }
                    PhotosPicker(selection: $selection, matching: .images) {
                        ZStack {
                            HStack {
                                Image(systemName: "camera")
                                Text("+ Add a photo")
                                Spacer()
                            }
                            .opacity(uploading ? 0 : 1)
                            if uploading { ProgressView().tint(QuenyaTheme.sage) }
                        }
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(QuenyaTheme.ink)
                        .padding(14)
                        .background(QuenyaTheme.webSurface2, in: RoundedRectangle(cornerRadius: 12))
                        .overlay(RoundedRectangle(cornerRadius: 12).stroke(QuenyaTheme.webLine2, lineWidth: 1))
                        .contentShape(Rectangle())
                    }
                    .disabled(uploading)
                    .sensoryFeedback(.impact(weight: .light), trigger: tapCount)
                }
                .padding(15)
                .background(QuenyaTheme.surface, in: RoundedRectangle(cornerRadius: 18))
            }
            .quenyaPage()
            .padding(18)
        }
        .background(QuenyaTheme.cream)
        .scrollDismissesKeyboard(.immediately)
        .dismissKeyboardOnTap()
        .onChange(of: selection) { _, value in
            guard let value else { return }
            Task { await upload(value) }
        }
    }

    private func search(live: Bool, value: String? = nil) async {
        let term = (value ?? query).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !term.isEmpty else { return }
        let key = live ? "search.live" : "search.saved"
        guard !busy.isBusy(key) else { return }
        lastSearchLive = live
        searchGeneration += 1
        let generation = searchGeneration
        query = term
        mode = live ? "live" : "saved"
        error = nil
        items = nil
        defer {
            if !Task.isCancelled, generation == searchGeneration {
                mode = nil
            }
        }
        let completed = await busy.run(key) {
            let result = live ? try await self.state.api.searchLive(q: term) : try await self.state.api.search(q: term)
            guard !Task.isCancelled, generation == self.searchGeneration else { return }
            self.items = result
        }
        if !completed, let message = busy.error(key) { error = message }
    }

    private func upload(_ item: PhotosPickerItem) async {
        guard !busy.isBusy("photo.upload") else { return }
        retrySelection = item
        uploadError = nil
        let completed = await busy.run("photo.upload") {
            guard let data = try await item.loadTransferable(type: Data.self),
                  let jpeg = makeJPEG(data) else {
                throw APIError(status: 0, message: "Couldn't read that photo.", requestIds: [])
            }
            try await self.state.upload(jpeg)
        }
        if !completed { uploadError = busy.error("photo.upload") }
        else { retrySelection = nil }
        selection = nil
        tapCount += 1
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

private struct FindSkeletonRow: View {
    var body: some View {
        HStack(spacing: 12) {
            SkeletonBlock(cornerRadius: 9).frame(width: 56, height: 56)
            GeometryReader { geometry in
                VStack(alignment: .leading, spacing: 8) {
                    SkeletonBlock(cornerRadius: 5).frame(width: geometry.size.width * 0.72, height: 12)
                    SkeletonBlock(cornerRadius: 5).frame(width: geometry.size.width * 0.46, height: 10)
                }
                .frame(maxHeight: .infinity, alignment: .center)
            }
            .frame(height: 56)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(QuenyaTheme.webSurface, in: RoundedRectangle(cornerRadius: 14))
    }
}
