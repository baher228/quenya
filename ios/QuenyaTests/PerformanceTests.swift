import ImageIO
import UIKit
import XCTest
@testable import Quenya

@MainActor
final class PerformanceTests: XCTestCase {
    func testStyleCacheUpdateWithRealisticSnapshot() async {
        let directory = makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = StyleCache(directory: directory)
        cache.activate(userId: "performance")
        let deck = makeItems(count: 40, prefix: "deck")
        let outfits = (0..<3).map { index in
            Outfit(
                id: "outfit-\(index)",
                title: "Outfit \(index)",
                mood: "Everyday",
                why: "Fits the brief",
                pieces: makeItems(count: 6, prefix: "outfit-\(index)"),
                totalGbp: 420
            )
        }
        cache.update("style") {
            $0.deck = deck
            $0.outfits = outfits
        }

        measure {
            cache.update("style") { $0.swiped += 1 }
        }

        #if !PERFORMANCE_BASELINE
        await cache.flush()
        #endif
    }

    func testFullResolutionJPEGDecode() {
        let data = makeJPEG()
        measure {
            _ = Self.fullResolutionDecode(data)
        }
    }

    func test800PixelThumbnailDecode() {
        let data = makeJPEG()
        let thumbnail = Self.thumbnailDecode(data, maxPixelSize: 800)
        XCTAssertNotNil(thumbnail)
        XCTAssertLessThanOrEqual(
            max(thumbnail?.cgImage?.width ?? 0, thumbnail?.cgImage?.height ?? 0),
            800
        )
        measure {
            _ = Self.thumbnailDecode(data, maxPixelSize: 800)
        }
    }

    func testFifteenSwipeSequence() async {
        let directory = makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = StyleCache(directory: directory)
        cache.activate(userId: "performance")
        let items = makeItems(count: 40, prefix: "swipe")
        cache.update("style") { $0.deck = items }
        let store = InMemoryTokenStore("no-network")
        let client = APIClient(baseURL: URL(string: "https://example.test")!, tokenStore: store)
        let state = AppState(tokenStore: store, apiClient: client, cache: cache)
        state.user = User(id: "performance", name: "Test", role: "tester")
        state.boards = [Board(
            id: "style",
            label: "Everyday",
            context: "Daily",
            active: true,
            brief: .default,
            version: 1,
            counts: BoardCounts(swipes: 0, outfits: 3, uploads: 0)
        )]

        measure {
            for item in items.prefix(15) {
                state.recordSwipe(item)
            }
        }

        #if !PERFORMANCE_BASELINE
        await cache.flush()
        #endif
    }

    private func makeDirectory() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("QuenyaPerformanceTests-\(UUID().uuidString)", isDirectory: true)
    }

    private func makeItems(count: Int, prefix: String) -> [Item] {
        (0..<count).map { index in
            Item(
                id: "\(prefix)-\(index)",
                title: "Vintage Japanese workwear jacket \(index)",
                brand: "Archive label \(index % 5)",
                priceGbp: Double(45 + index),
                size: "Medium",
                source: "vinted",
                sourceUrl: "https://example.test/items/\(prefix)-\(index)",
                imageUrl: "/img/\(prefix)-\(index).jpg",
                tags: ItemTags(
                    slot: "outerwear",
                    category: "workwear jacket",
                    colours: ["olive", "brown"],
                    materials: ["cotton"],
                    fit: "relaxed",
                    era: "1990s",
                    aesthetics: ["Japanese workwear"],
                    formality: 2,
                    niche: 0.7,
                    gender: "menswear",
                    logoHeavy: false,
                    condition: "good",
                    oneLine: "A relaxed vintage workwear piece."
                )
            )
        }
    }

    private func makeJPEG() -> Data {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 2_000, height: 2_500), format: format)
        return renderer.jpegData(withCompressionQuality: 0.82) { context in
            var seed: UInt32 = 0x1234_5678
            for y in stride(from: 0, to: 2_500, by: 20) {
                for x in stride(from: 0, to: 2_000, by: 20) {
                    seed = 1_664_525 &* seed &+ 1_013_904_223
                    let red = CGFloat(seed & 0xff) / 255
                    let green = CGFloat((seed >> 8) & 0xff) / 255
                    let blue = CGFloat((seed >> 16) & 0xff) / 255
                    UIColor(red: red, green: green, blue: blue, alpha: 1).setFill()
                    context.cgContext.fill(CGRect(x: x, y: y, width: 20, height: 20))
                }
            }
        }
    }

    private static func fullResolutionDecode(_ data: Data) -> CGImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        return CGImageSourceCreateImageAtIndex(
            source,
            0,
            [kCGImageSourceShouldCacheImmediately: true] as CFDictionary
        )
    }

    private static func thumbnailDecode(_ data: Data, maxPixelSize: Int) -> UIImage? {
        #if PERFORMANCE_BASELINE
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
        #else
        return ImageCache.downsample(data, maxPixelSize: maxPixelSize)
        #endif
    }
}
