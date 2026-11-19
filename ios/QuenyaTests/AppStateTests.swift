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

    func testTasteUpdateErrorIsFriendlyAndDismissible() async throws {
        let (state, session) = makeState()
        defer { session.invalidateAndCancel() }
        let rawDetail = "cursor-agent returned invalid outer JSON"
        StubURLProtocol.setResponse(
            status: 502,
            json: #"{"detail":"cursor-agent returned invalid outer JSON"}"#
        )

        await state.runUpdate()

        XCTAssertEqual(StubURLProtocol.lastRequest?.url?.path, "/api/taste/update")
        let displayedError = try XCTUnwrap(state.tasteUpdateError)
        XCTAssertEqual(
            displayedError,
            "Couldn't update your taste just now. Your swipes are saved, so try again in a minute."
        )
        XCTAssertFalse(displayedError.contains("cursor-agent"))
        XCTAssertEqual(state.busy.error("taste.update"), rawDetail)

        state.dismissTasteUpdateError()

        XCTAssertNil(state.tasteUpdateError)
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

    func testSwipeRequestKeepsCapturedBoardAfterBoardSwitch() async throws {
        let (state, session) = makeState()
        defer { session.invalidateAndCancel() }
        let boardA = makeBoard("board-a", active: true)
        let boardB = makeBoard("board-b", active: false)
        state.cache.activate(userId: "swipe-\(UUID().uuidString)")
        state.boards = [boardA, boardB]
        StubURLProtocol.setResponse(
            json: #"{"swipes_since_update":1,"update_due":false}"#
        )
        StubURLProtocol.holdRequests(path: "/api/swipe")

        let swipeTask = Task {
            await state.swipe(Item(id: "item-1", title: "Jacket"), liked: true)
        }
        await waitForPending(path: "/api/swipe", count: 1)
        XCTAssertEqual(try boardIdInPendingRequest(path: "/api/swipe"), boardA.id)

        state.boards = [
            makeBoard(boardA.id, active: false),
            makeBoard(boardB.id, active: true),
        ]
        StubURLProtocol.completeNext(
            path: "/api/swipe",
            status: 200,
            json: #"{"swipes_since_update":1,"update_due":false}"#
        )
        await swipeTask.value
    }

    func testTasteUpdateRequestKeepsCapturedBoardAfterBoardSwitch() async throws {
        let (state, session) = makeState()
        defer { session.invalidateAndCancel() }
        let boardA = makeBoard("board-a", active: true)
        let boardB = makeBoard("board-b", active: false)
        state.boards = [boardA, boardB]
        StubURLProtocol.setResponse(
            status: 502,
            json: #"{"detail":"Taste update failed"}"#
        )
        StubURLProtocol.holdRequests(path: "/api/taste/update")

        let updateTask = Task { await state.runUpdate() }
        await waitForPending(path: "/api/taste/update", count: 1)
        XCTAssertEqual(try boardIdInPendingRequest(path: "/api/taste/update"), boardA.id)

        state.boards = [
            makeBoard(boardA.id, active: false),
            makeBoard(boardB.id, active: true),
        ]
        StubURLProtocol.completeNext(
            path: "/api/taste/update",
            status: 502,
            json: #"{"detail":"Taste update failed"}"#
        )
        await updateTask.value
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
        XCTAssertEqual(try boardIdInPendingRequest(path: path), boardA.id)
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
        let jobs = "/api/outfits/jobs"
        let oldJob = "\(jobs)/old"
        let newJob = "\(jobs)/new"
        StubURLProtocol.setResponse(json: "{}")
        StubURLProtocol.holdRequests(path: jobs)
        StubURLProtocol.holdRequests(path: oldJob)
        StubURLProtocol.holdRequests(path: newJob)

        let firstTask = Task { await state.rebuildOutfit(first, styleId: board.id) }
        await waitForPending(path: jobs, count: 1)
        XCTAssertEqual(try boardIdInPendingRequest(path: jobs), board.id)
        let secondTask = Task { await state.rebuildOutfit(second, styleId: board.id) }
        await waitForPending(path: jobs, count: 2)
        XCTAssertEqual(try boardIdInPendingRequest(path: jobs), board.id)
        StubURLProtocol.completeLast(path: jobs, status: 202, json: #"{"job_id":"new"}"#)
        StubURLProtocol.completeNext(path: jobs, status: 202, json: #"{"job_id":"old"}"#)
        await waitForPending(path: newJob, count: 1)
        await waitForPending(path: oldJob, count: 1)
        try completeOutfitJob(path: newJob, outfit: newest)
        let secondSucceeded = await secondTask.value
        XCTAssertTrue(secondSucceeded)
        try completeOutfitJob(path: oldJob, outfit: stale)
        let firstSucceeded = await firstTask.value
        XCTAssertTrue(firstSucceeded)

        let results = state.cache.snapshot(for: board.id).outfits
        XCTAssertEqual(results.first(where: { $0.id == first.id })?.title, first.title)
        XCTAssertEqual(results.first(where: { $0.id == second.id })?.title, newest.title)
    }

    func testBuildFinishingAfterBoardSwitchDoesNotPopulateNewBoard() async throws {
        let (state, session) = makeState()
        defer { session.invalidateAndCancel() }
        let boardA = makeBoard("board-a", active: true)
        let boardB = makeBoard("board-b", active: true)
        let generated = makeOutfit(id: "generated", title: "Generated for A")
        state.cache.activate(userId: "test-\(UUID().uuidString)")
        state.cache.update(boardA.id) { $0.outfits = [] }
        state.cache.update(boardB.id) { $0.outfits = [] }
        state.boards = [boardA, boardB]
        let jobs = "/api/outfits/jobs"
        let job = "\(jobs)/build"
        StubURLProtocol.setResponse(json: "{}")
        StubURLProtocol.holdRequests(path: jobs)
        StubURLProtocol.holdRequests(path: job)

        let buildTask = Task { await state.buildOutfits() }
        await waitForPending(path: jobs, count: 1)
        XCTAssertEqual(try boardIdInPendingRequest(path: jobs), boardA.id)
        StubURLProtocol.completeNext(path: jobs, status: 202, json: #"{"job_id":"build"}"#)
        await waitForPending(path: job, count: 1)
        _ = await state.activateFromStrip(boardB)
        try completeOutfitJob(path: job, outfit: generated)
        await buildTask.value

        XCTAssertEqual(state.activeBoard?.id, boardB.id)
        XCTAssertEqual(state.outfits, [])
        XCTAssertEqual(state.cache.snapshot(for: boardA.id).outfits, [])
        XCTAssertFalse(state.busy.isBusy("outfits.build"))
    }

    func testDeckBuildStartsWhileTasteUpdateIsPending() async {
        let (state, session) = makeState()
        defer { session.invalidateAndCancel() }
        let board = makeBoard("board-a", active: true)
        state.cache.activate(userId: "deck-build-\(UUID().uuidString)")
        state.boards = [board]
        let tastePath = "/api/taste/update"
        let jobsPath = "/api/outfits/jobs"
        StubURLProtocol.setResponse(json: "{}")
        StubURLProtocol.holdRequests(path: tastePath)
        StubURLProtocol.holdRequests(path: jobsPath)

        let build = Task { await state.deckDone() }
        await waitForPending(path: tastePath, count: 1)
        await waitForPending(path: jobsPath, count: 1)
        XCTAssertEqual(state.selectedTab, .outfits)
        XCTAssertTrue(state.outfitLoading)
        StubURLProtocol.completeNext(path: tastePath, status: 409, json: "{}")
        StubURLProtocol.completeNext(path: jobsPath, status: 409, json: "{}")
        await build.value
    }

    func testRebuildRequestKeepsCapturedBoardAfterSwitch() async throws {
        let (state, session) = makeState()
        defer { session.invalidateAndCancel() }
        let boardA = makeBoard("board-a", active: true)
        let boardB = makeBoard("board-b", active: true)
        let outfit = makeOutfit(id: "outfit-a", title: "A")
        state.cache.activate(userId: "test-\(UUID().uuidString)")
        state.cache.update(boardA.id) { $0.outfits = [outfit] }
        state.cache.update(boardB.id) { $0.outfits = [] }
        state.boards = [boardA, boardB]
        let jobs = "/api/outfits/jobs"
        StubURLProtocol.setResponse(json: "{}")
        StubURLProtocol.holdRequests(path: jobs)

        let rebuildTask = Task {
            await state.rebuildOutfit(outfit, styleId: boardA.id)
        }
        await waitForPending(path: jobs, count: 1)
        XCTAssertEqual(try boardIdInPendingRequest(path: jobs), boardA.id)

        _ = await state.activateFromStrip(boardB)
        StubURLProtocol.completeNext(path: jobs, status: 409, json: "{}")
        await rebuildTask.value

        XCTAssertEqual(state.activeBoard?.id, boardB.id)
        XCTAssertEqual(state.outfits, [])
    }

    func testVerdictRequestKeepsCapturedBoardAfterSwitch() async throws {
        let (state, session) = makeState()
        defer { session.invalidateAndCancel() }
        let boardA = makeBoard("board-a", active: true)
        let boardB = makeBoard("board-b", active: true)
        let outfit = makeOutfit(id: "outfit-a", title: "A")
        state.cache.activate(userId: "test-\(UUID().uuidString)")
        state.cache.update(boardA.id) { $0.outfits = [outfit] }
        state.cache.update(boardB.id) { $0.outfits = [] }
        state.boards = [boardA, boardB]
        let path = "/api/outfits/\(outfit.id)/verdict"
        StubURLProtocol.setResponse(json: "{}")
        StubURLProtocol.holdRequests(path: path)

        let verdictTask = Task {
            await state.verdict(outfit, like: true, styleId: boardA.id)
        }
        await waitForPending(path: path, count: 1)
        XCTAssertEqual(try boardIdInPendingRequest(path: path), boardA.id)

        _ = await state.activateFromStrip(boardB)
        let responseOutfit = makeOutfit(id: outfit.id, title: "Loved")
        StubURLProtocol.completeNext(
            path: path,
            status: 200,
            json: #"{"outfit":\#(try json(responseOutfit)),"taste":null}"#
        )

        let verdictSucceeded = await verdictTask.value
        XCTAssertTrue(verdictSucceeded)
        XCTAssertEqual(state.cache.snapshot(for: boardA.id).outfits.first?.title, "Loved")
        XCTAssertEqual(state.cache.snapshot(for: boardB.id).outfits, [])
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
        for _ in 0..<400 {
            if StubURLProtocol.pendingCount(path: path) >= count { return }
            try? await Task.sleep(for: .milliseconds(10))
        }
        XCTFail("Timed out waiting for \(count) \(path) requests")
    }

    private func completeOutfitJob(path: String, outfit: Outfit) throws {
        let result = try json(OutfitsResponse(outfits: [outfit]))
        StubURLProtocol.completeNext(path: path, status: 200, json: #"{"status":"done","result":\#(result)}"#)
    }

    private func boardIdInPendingRequest(path: String) throws -> String? {
        let body = try XCTUnwrap(StubURLProtocol.lastPendingBody(path: path))
        let payload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: body) as? [String: Any]
        )
        return payload["board_id"] as? String
    }
}
