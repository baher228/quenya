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
}
