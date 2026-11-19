import Foundation
import XCTest
@testable import Quenya

@MainActor
final class StyleCacheTests: XCTestCase {
    func testEnsureDeckUsesCachedItemsWithoutRequest() async {
        let directory = makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = StyleCache(directory: directory)
        cache.activate(userId: "u1")
        cache.update("b1") { $0.deck = (1...20).map { item("item-\($0)") } }

        let (state, session) = makeState(cache: cache)
        defer { session.invalidateAndCancel() }
        state.user = user()
        state.boards = [board(id: "b1", active: true)]
        StubURLProtocol.setResponse(json: "[]")

        await state.ensureDeck()

        XCTAssertNil(StubURLProtocol.lastRequest)
        XCTAssertEqual(state.deck.count, 20)
    }

    func testThirtySwipesKeepBufferedDeckAndCoalesceRefills() async throws {
        let directory = makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = StyleCache(directory: directory)
        cache.activate(userId: "u1")
        let cards = (1...60).map { item("item-\($0)") }
        cache.update("b1") { $0.deck = Array(cards.prefix(20)) }
        let (state, session) = makeState(cache: cache)
        defer { session.invalidateAndCancel() }
        state.user = user()
        state.boards = [board(id: "b1", active: true)]
        StubURLProtocol.setResponse(json: #"{"swipes_since_update":1,"update_due":false}"#)
        let path = "/api/deck"
        StubURLProtocol.holdRequests(path: path)

        for swipe in 1...30 {
            let visible = try XCTUnwrap(state.deck.first)
            XCTAssertEqual(visible.id, "item-\(swipe)")
            state.commitSwipe(visible, liked: true)
            XCTAssertNotEqual(state.deck.first?.id, visible.id)
            if [12, 30].contains(swipe) {
                await waitUntil { StubURLProtocol.pendingCount(path: path) > 0 }
                await state.ensureDeck()
                XCTAssertEqual(StubURLProtocol.pendingCount(path: path), 1)
                let request = try XCTUnwrap(StubURLProtocol.firstPendingRequest(path: path))
                let url = try XCTUnwrap(request.url)
                let query = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false)).queryItems ?? []
                XCTAssertEqual(query.first(where: { $0.name == "board_id" })?.value, "b1")
                let excluded = Set(query.filter { $0.name == "exclude_ids" }.compactMap(\.value))
                XCTAssertTrue(excluded.contains(swipe == 12 ? "item-10" : "item-30"))
                XCTAssertTrue(excluded.contains(swipe == 12 ? "item-20" : "item-40"))
                let page = swipe == 12 ? Array(cards[19..<40]) : Array(cards[39..<60])
                StubURLProtocol.completeNext(path: path, status: 200, json: try itemsJSON(page))
                await waitUntil { !state.deckLoading }
            }
        }

        XCTAssertEqual(state.deck.first?.id, "item-31")
        XCTAssertEqual(state.deck.last?.id, "item-60")
        XCTAssertEqual(state.swipedCount, 30)
        await cache.flush()
    }

    private func waitUntil(_ condition: () -> Bool) async {
        for _ in 0..<200 {
            if condition() { return }
            try? await Task.sleep(for: .milliseconds(10))
        }
        XCTFail("Timed out waiting for deck refill")
    }

    func testSnapshotRoundTripsAcrossCacheInstances() async {
        let directory = makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = StyleCache(directory: directory)
        cache.activate(userId: "u1")
        cache.update("b1") {
            $0.deck = [item("one")]
            $0.outfits = [outfit("outfit-1", piece: item("one"))]
            $0.locked = ["outfit-1": ["one"]]
            $0.swapSeen = ["outfit-1": ["old-piece"]]
            $0.deckFetchedAt = Date(timeIntervalSince1970: 1_234_567_890)
        }
        let expected = cache.snapshot(for: "b1")
        await cache.flush()

        let reopened = StyleCache(directory: directory)
        reopened.activate(userId: "u1")

        XCTAssertEqual(reopened.snapshot(for: "b1"), expected)
    }

    func testSessionSnapshotRoundTripsLastTabAndDecodesLegacyJSON() async throws {
        let directory = makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = StyleCache(directory: directory)
        cache.activate(userId: "u1")
        cache.saveSession(sessionSnapshot(lastTab: .outfits))
        await cache.flush()

        let reopened = StyleCache(directory: directory)
        XCTAssertEqual(reopened.loadSession()?.lastTab, .outfits)

        let sessionFile = directory.appendingPathComponent("session.json")
        let encoded = try Data(contentsOf: sessionFile)
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        json.removeValue(forKey: "lastTab")
        try JSONSerialization.data(withJSONObject: json).write(to: sessionFile, options: .atomic)

        XCTAssertNil(StyleCache(directory: directory).loadSession()?.lastTab)
    }

    func testSignOutClearsStyleAndSessionSnapshots() async throws {
        let directory = makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = StyleCache(directory: directory)
        cache.activate(userId: "u1")
        cache.update("b1") { $0.deck = [item("one")] }
        cache.saveSession(sessionSnapshot())

        let (state, session) = makeState(cache: cache)
        defer { session.invalidateAndCancel() }
        StubURLProtocol.setResponse(json: "{}")

        await state.signOut()

        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.path))
        XCTAssertNil(StyleCache(directory: directory).loadSession())
    }

    func testActivatingDifferentUserWipesPreviousStyleData() {
        let directory = makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = StyleCache(directory: directory)
        cache.activate(userId: "u1")
        cache.update("b1") { $0.deck = [item("one")] }

        cache.activate(userId: "u2")

        XCTAssertEqual(cache.snapshot(for: "b1"), StyleSnapshot(userId: "u2", styleId: "b1"))
    }

    func testRapidUpdatesCoalesceAndFlushPersistsLatestSnapshot() async throws {
        let directory = makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = StyleCache(directory: directory)
        cache.activate(userId: "u1")

        for value in 1...50 {
            cache.update("b1") { $0.swiped = value }
        }
        await cache.flush()

        let writeCount = await cache.writeCount(forStyleId: "b1")
        XCTAssertEqual(writeCount, 1)
        let data = try Data(contentsOf: directory.appendingPathComponent("u1/b1.json"))
        let persisted = try JSONDecoder().decode(StyleSnapshot.self, from: data)
        XCTAssertEqual(persisted, cache.snapshot(for: "b1"))
    }

    func testClearWithPendingUpdateDoesNotRecreateCacheFiles() async {
        let directory = makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = StyleCache(directory: directory)
        cache.activate(userId: "u1")
        cache.update("b1") { $0.deck = [item("pending")] }

        cache.clear()
        await cache.flush()

        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.path))
    }

    func testSwipeIsPersistedAndNeverReaddedByRefresh() async throws {
        let directory = makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = StyleCache(directory: directory)
        cache.activate(userId: "u1")
        let swiped = item("swiped")
        cache.update("b1") { $0.deck = [swiped] }

        let (state, session) = makeState(cache: cache)
        defer { session.invalidateAndCancel() }
        state.user = user()
        state.boards = [board(id: "b1", active: true)]
        StubURLProtocol.setResponse(json: #"{"swipes_since_update":1,"update_due":false}"#)

        await state.swipe(swiped, liked: true)
        await cache.flush()

        let data = try Data(contentsOf: directory.appendingPathComponent("u1/b1.json"))
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let storedDeck = try XCTUnwrap(object["deck"] as? [[String: Any]])
        let sentIds = try XCTUnwrap(object["sentSwipeIds"] as? [String])
        XCTAssertTrue(storedDeck.isEmpty)
        XCTAssertEqual(sentIds, ["swiped"])

        StubURLProtocol.setResponse(json: try itemsJSON([swiped, item("new")]))
        await state.refreshDeckQuietly(maxAge: nil)

        XCTAssertEqual(state.deck.map(\.id), ["new"])
    }

    private func makeState(cache: StyleCache) -> (AppState, URLSession) {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        let session = URLSession(configuration: configuration)
        let store = InMemoryTokenStore("test-token")
        let client = APIClient(
            baseURL: URL(string: "https://api.example.test")!,
            tokenStore: store,
            session: session
        )
        return (AppState(tokenStore: store, apiClient: client, cache: cache), session)
    }

    private func makeDirectory() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("QuenyaStyleCacheTests-\(UUID().uuidString)", isDirectory: true)
    }

    private func item(_ id: String) -> Item {
        Item(id: id, title: "Item \(id)", source: "test", sourceUrl: "https://example.test/\(id)")
    }

    private func outfit(_ id: String, piece: Item) -> Outfit {
        Outfit(
            id: id,
            title: "Outfit",
            mood: "Casual",
            why: "Fits the brief",
            pieces: [piece],
            totalGbp: piece.priceGbp
        )
    }

    private func user() -> User {
        User(id: "u1", name: "Tester", role: "tester")
    }

    private func board(id: String, active: Bool) -> Board {
        Board(
            id: id,
            label: "Style",
            context: "Daily",
            active: active,
            brief: .default,
            version: 1,
            counts: BoardCounts(swipes: 0, outfits: 0, uploads: 0)
        )
    }

    private func sessionSnapshot(lastTab: AppTab? = nil) -> SessionSnapshot {
        SessionSnapshot(
            userId: "u1",
            user: user(),
            boards: [board(id: "b1", active: true)],
            taste: nil,
            lastTab: lastTab
        )
    }

    private func itemsJSON(_ items: [Item]) throws -> String {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return String(decoding: try encoder.encode(items), as: UTF8.self)
    }
}
