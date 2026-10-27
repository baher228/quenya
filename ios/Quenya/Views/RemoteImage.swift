import SwiftUI
import UIKit

final class ImageCache: @unchecked Sendable {
    static let shared = ImageCache()
    private let cache = NSCache<NSURL, UIImage>()

    func image(for url: URL) -> UIImage? {
        cache.object(forKey: url as NSURL)
    }

    func load(_ url: URL) async -> UIImage? {
        if let cached = image(for: url) { return cached }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            guard let image = UIImage(data: data) else { return nil }
            cache.setObject(image, forKey: url as NSURL)
            return image
        } catch {
            return nil
        }
    }

    func prefetch(_ paths: [String?]) {
        let urls = paths.compactMap { path -> URL? in
            guard let path else { return nil }
            if let url = URL(string: path), let scheme = url.scheme, ["http", "https"].contains(scheme) {
                return url
            }
            return URL(string: path, relativeTo: AppConfig.baseURL)?.absoluteURL
        }
        Task {
            await withTaskGroup(of: Void.self) { group in
                for url in urls {
                    group.addTask { _ = await self.load(url) }
                }
            }
        }
    }
}

struct RemoteImage: View {
    let path: String?
    var contentMode: ContentMode = .fill
    @State private var image: UIImage?

    var body: some View {
        GeometryReader { geometry in
            Group {
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .aspectRatio(contentMode: contentMode)
                } else {
                    Rectangle().fill(QuenyaTheme.line)
                        .overlay(Image(systemName: "photo").font(.title2).foregroundStyle(QuenyaTheme.muted))
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
            .clipped()
        }
        .task(id: path) {
            guard let path,
                  let url = URL(string: path, relativeTo: AppConfig.baseURL)?.absoluteURL else {
                image = nil
                return
            }
            image = await ImageCache.shared.load(url)
        }
    }
}
