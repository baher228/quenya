import XCTest
@testable import Quenya

@MainActor
final class AppStateTests: XCTestCase {
    func testTesterStyleLimitSurfacesInBriefError() async {
        let store = InMemoryTokenStore("test-token")
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }

        let client = APIClient(
            baseURL: URL(string: "https://api.example.test")!,
            tokenStore: store,
            session: session
        )
        let state = AppState(tokenStore: store, apiClient: client)
        StubURLProtocol.setResponse(
            status: 403,
            json: #"{"detail":"Tester accounts have one style"}"#
        )
        var brief = Brief.default
        brief.text = "One tester style"

        await state.submitBrief(brief, photos: [])

        XCTAssertEqual(state.briefError, "Tester accounts have one style")
        XCTAssertEqual(StubURLProtocol.lastRequest?.url?.path, "/api/profile")
    }

    func testFailedVerdictClearsBusyAndExposesInlineError() async {
        let store = InMemoryTokenStore("test-token")
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }

        let client = APIClient(
            baseURL: URL(string: "https://api.example.test")!,
            tokenStore: store,
            session: session
        )
        let state = AppState(tokenStore: store, apiClient: client)
        state.cache.activate(userId: "verdict-\(UUID().uuidString)")
        state.boards = [makeBoard("board-a", active: true)]
        let outfit = Outfit(
            id: "outfit-1",
            title: "Test outfit",
            mood: "Everyday",
            why: "",
            pieces: [],
            totalGbp: 0
        )
        let key = "verdict.\(outfit.id)"
        StubURLProtocol.setResponse(status: 500, json: #"{"detail":"Service unavailable"}"#)

        let saved = await state.verdict(outfit, like: false, reason: "Not my style")

        XCTAssertFalse(saved)
        XCTAssertFalse(state.busy.isBusy(key))
        XCTAssertNotNil(state.busy.error(key))
        XCTAssertEqual(StubURLProtocol.lastRequest?.url?.path, "/api/outfits/outfit-1/verdict")
    }

    func testRetryFailedRejectRepeatsVerdictAndReason() async throws {
        let store = InMemoryTokenStore("test-token")
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }

        let client = APIClient(
            baseURL: URL(string: "https://api.example.test")!,
            tokenStore: store,
            session: session
        )
        let state = AppState(tokenStore: store, apiClient: client)
        state.cache.activate(userId: "retry-\(UUID().uuidString)")
        state.boards = [makeBoard("board-a", active: true)]
        let outfit = Outfit(
            id: "outfit-1",
            title: "Test outfit",
            mood: "Everyday",
            why: "",
            pieces: [],
            totalGbp: 0
        )
        let reason = "Too costume-y"
        StubURLProtocol.setResponse(status: 500, json: #"{"detail":"Service unavailable"}"#)

        let firstAttempt = await state.verdict(outfit, like: false, reason: reason)
        XCTAssertFalse(firstAttempt)
        XCTAssertFalse(state.busy.isBusy("verdict.\(outfit.id)"))

        let retry = await state.retryVerdict(outfit)
        XCTAssertFalse(retry)
        let requestBody = try XCTUnwrap(StubURLProtocol.lastBody)
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: requestBody) as? [String: Any])
        XCTAssertEqual(payload["verdict"] as? String, "reject")
        XCTAssertEqual(payload["reason"] as? String, reason)
        XCTAssertEqual(StubURLProtocol.lastRequest?.url?.path, "/api/outfits/outfit-1/verdict")
    }

    func testActivatingBoardFromOutfitsKeepsTabAndShowsItsSnapshot() async {
        let (state, session) = makeState()
        let boardA = makeBoard("board-a", active: true)
        let boardB = makeBoard("board-b", active: true)
        let outfitA = makeOutfit(id: "outfit-a", title: "A")
        let outfitB = makeOutfit(id: "outfit-b", title: "B")
        state.cache.activate(userId: "test-\(UUID().uuidString)")
        state.cache.update(boardA.id) { $0.outfits = [outfitA] }
        state.cache.update(boardB.id) { $0.outfits = [outfitB] }
        state.boards = [boardA, boardB]
        state.selectedTab = .outfits

        let activated = await state.activateFromStrip(boardB)

        XCTAssertTrue(activated)
        XCTAssertEqual(state.selectedTab, .outfits)
        XCTAssertEqual(state.outfits, [outfitB])
    }

    func testSwapAfterBoardSwitchUpdatesCapturedBoardOnly() async throws {
        let (state, session) = makeState()
        let boardA = makeBoard("board-a", active: true)
        let boardB = makeBoard("board-b", active: true)
        let original = makeOutfit(id: "outfit-1", title: "Original", pieces: [Item(id: "old", title: "Old")])
        let swapped = makeOutfit(id: "outfit-1", title: "Swapped", pieces: [Item(id: "new", title: "New")])
        let item = try XCTUnwrap(original.pieces.first)
        state.cache.activate(userId: "test-\(UUID().uuidString)")
        state.cache.update(boardA.id) { $0.outfits = [original] }
        state.cache.update(boardB.id) { $0.outfits = [] }
        state.boards = [boardA, boardB]
        let path = "/api/outfits/outfit-1/swap"
        StubURLProtocol.setResponse(json: "{}")
        StubURLProtocol.holdRequests(path: path)

        let swapTask = Task { await state.swapOutfit(original, item: item, styleId: boardA.id) }
        await waitForPending(path: path, count: 1)
        _ = await state.activateFromStrip(boardB)
        StubURLProtocol.completeNext(path: path, status: 200, json: try json(swapped))
        let swapSucceeded = await swapTask.value
        XCTAssertTrue(swapSucceeded)
        XCTAssertEqual(state.cache.snapshot(for: boardA.id).outfits, [swapped])
        XCTAssertEqual(state.cache.snapshot(for: boardB.id).outfits, [])
        XCTAssertEqual(state.outfits, [])
    }

    func testOlderRebuildResponseIsDropped() async throws {
        let (state, session) = makeState()
        defer { session.invalidateAndCancel() }
        let board = makeBoard("board-a", active: true)
        let first = makeOutfit(id: "outfit-1", title: "First")
        let second = makeOutfit(id: "outfit-2", title: "Second")
        let stale = makeOutfit(id: "outfit-1", title: "Stale result")
        let newest = makeOutfit(id: "outfit-2", title: "Newest result")
        state.cache.activate(userId: "test-\(UUID().uuidString)")
        state.cache.update(board.id) { $0.outfits = [first, second] }
        state.boards = [board]
        StubURLProtocol.setResponse(json: "{}")
        StubURLProtocol.holdRequests(path: "/api/outfits")

        let firstTask = Task { await state.rebuildOutfit(first, styleId: board.id) }
        await waitForPending(path: "/api/outfits", count: 1)
        let secondTask = Task { await state.rebuildOutfit(second, styleId: board.id) }
        await waitForPending(path: "/api/outfits", count: 2)
        StubURLProtocol.completeLast(
            path: "/api/outfits",
            status: 200,
            json: try json(OutfitsResponse(outfits: [newest]))
        )
        let secondSucceeded = await secondTask.value
        XCTAssertTrue(secondSucceeded)
        StubURLProtocol.completeNext(
            path: "/api/outfits",
            status: 200,
            json: try json(OutfitsResponse(outfits: [stale]))
        )
        let firstSucceeded = await firstTask.value
        XCTAssertTrue(firstSucceeded)

        let results = state.cache.snapshot(for: board.id).outfits
        XCTAssertEqual(results.first(where: { $0.id == first.id })?.title, first.title)
        XCTAssertEqual(results.first(where: { $0.id == second.id })?.title, newest.title)
    }

    func testBuildFinishingAfterBoardSwitchDoesNotPopulateNewBoard() async throws {
        let (state, session) = makeState()
        let boardA = makeBoard("board-a", active: true)
        let boardB = makeBoard("board-b", active: true)
        let generated = makeOutfit(id: "generated", title: "Generated for A")
        state.cache.activate(userId: "test-\(UUID().uuidString)")
        state.cache.update(boardA.id) { $0.outfits = [] }
        state.cache.update(boardB.id) { $0.outfits = [] }
        state.boards = [boardA, boardB]
        StubURLProtocol.setResponse(json: "{}")
        StubURLProtocol.holdRequests(path: "/api/outfits")

        let buildTask = Task { await state.buildOutfits() }
        await waitForPending(path: "/api/outfits", count: 1)
        _ = await state.activateFromStrip(boardB)
        StubURLProtocol.completeNext(
            path: "/api/outfits",
            status: 200,
            json: try json(OutfitsResponse(outfits: [generated]))
        )
        await buildTask.value

        XCTAssertEqual(state.activeBoard?.id, boardB.id)
        XCTAssertEqual(state.outfits, [])
        XCTAssertEqual(state.cache.snapshot(for: boardA.id).outfits, [])
        XCTAssertFalse(state.busy.isBusy("outfits.build"))
    }

    private func makeState() -> (AppState, URLSession) {
        let store = InMemoryTokenStore("test-token")
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        let session = URLSession(configuration: configuration)
        let client = APIClient(
            baseURL: URL(string: "https://api.example.test")!,
            tokenStore: store,
            session: session
        )
        return (AppState(tokenStore: store, apiClient: client), session)
    }

    private func makeBoard(_ id: String, active: Bool) -> Board {
        Board(
            id: id,
            label: id,
            context: "",
            active: active,
            brief: .default,
            version: nil,
            counts: BoardCounts(swipes: 0, outfits: 0, uploads: 0)
        )
    }

    private func makeOutfit(id: String, title: String, pieces: [Item] = []) -> Outfit {
        Outfit(id: id, title: title, mood: "Everyday", why: "", pieces: pieces, totalGbp: 0)
    }

    private func json<T: Encodable>(_ value: T) throws -> String {
        String(decoding: try JSONEncoder().encode(value), as: UTF8.self)
    }

    private func waitForPending(path: String, count: Int) async {
        for _ in 0..<200 {
            if StubURLProtocol.pendingCount(path: path) >= count { return }
            try? await Task.sleep(for: .milliseconds(10))
        }
        XCTFail("Timed out waiting for \(count) \(path) requests")
    }
}
