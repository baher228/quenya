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
}
