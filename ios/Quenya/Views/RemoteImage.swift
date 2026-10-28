import ImageIO
import SwiftUI
import UIKit

final class ImageCache: @unchecked Sendable {
    static let shared = ImageCache()
    private let cache = NSCache<NSString, UIImage>()

    init() {
        cache.totalCostLimit = 80 * 1024 * 1024
    }

    static func pixelBucket(for size: CGSize, scale: CGFloat) -> Int {
        let requested = max(size.width, size.height) * scale
        if requested <= 400 { return 400 }
        if requested <= 800 { return 800 }
        return 1_400
    }

    func image(for url: URL, pixelBucket: Int) -> UIImage? {
        cache.object(forKey: cacheKey(url, pixelBucket: pixelBucket))
    }

    func load(_ url: URL, pixelBucket: Int = 800) async -> UIImage? {
        if let cached = image(for: url, pixelBucket: pixelBucket) { return cached }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let image = await Task.detached(priority: .userInitiated) {
                Self.downsample(data, maxPixelSize: pixelBucket)
            }.value
            guard let image else { return nil }
            let cost = image.cgImage.map { $0.bytesPerRow * $0.height } ?? 0
            cache.setObject(image, forKey: cacheKey(url, pixelBucket: pixelBucket), cost: cost)
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
                    group.addTask { _ = await self.load(url, pixelBucket: 800) }
                }
            }
        }
    }

    static func downsample(_ data: Data, maxPixelSize: Int) -> UIImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixelSize
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
            return nil
        }
        return UIImage(cgImage: image)
    }

    private func cacheKey(_ url: URL, pixelBucket: Int) -> NSString {
        "\(url.absoluteString)|\(pixelBucket)" as NSString
    }
}

struct RemoteImage: View {
    let path: String?
    var contentMode: ContentMode = .fill
    @Environment(\.displayScale) private var displayScale
    @State private var image: UIImage?

    var body: some View {
        GeometryReader { geometry in
            let resolvedURL = path.flatMap {
                URL(string: $0, relativeTo: AppConfig.baseURL)?.absoluteURL
            }
            let pixelBucket = ImageCache.pixelBucket(for: geometry.size, scale: displayScale)
            let displayedImage = image ?? resolvedURL.flatMap {
                ImageCache.shared.image(for: $0, pixelBucket: pixelBucket)
            }
            Group {
                if let displayedImage {
                    Image(uiImage: displayedImage)
                        .resizable()
                        .aspectRatio(contentMode: contentMode)
                } else {
                    Rectangle().fill(QuenyaTheme.line)
                        .overlay(Image(systemName: "photo").font(.title2).foregroundStyle(QuenyaTheme.muted))
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
            .clipped()
            .task(id: "\(path ?? "")|\(pixelBucket)") {
                guard let path,
                      let url = URL(string: path, relativeTo: AppConfig.baseURL)?.absoluteURL else {
                    image = nil
                    return
                }
                let loaded = await ImageCache.shared.load(url, pixelBucket: pixelBucket)
                guard !Task.isCancelled else { return }
                image = loaded
            }
        }
    }
}
