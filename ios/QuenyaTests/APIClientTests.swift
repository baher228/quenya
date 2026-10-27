import XCTest
@testable import Quenya

final class APIClientTests: XCTestCase {
    private let baseURL = URL(string: "https://api.example.test")!
    private var store: InMemoryTokenStore!
    private var client: APIClient!
    private var session: URLSession!

    private let realItem = """
    {
      "id":"arcteryx.com:sidewinder-jacket-men-s-black-aquaculture-ac-size-s",
      "source":"arcteryx.com",
      "source_url":"https://arcteryx.com/gb/en/shop/mens/sidewinder-jacket-0804",
      "title":"Sidewinder Jacket Men's, Black / Aquaculture AC, Size S",
      "brand":"Sidewinder",
      "price_gbp":750.0,
      "size":null,
      "description":null,
      "image_url":"/img/6fcfec76d4c10954de398a5bb6be22e4c7a789c9.jpg",
      "image_path":"/home/ubuntu/repos/quenya/backend/data/img/6fcfec76d4c10954de398a5bb6be22e4c7a789c9.jpg",
      "gender_hint":"m",
      "condition_hint":"new",
      "availability":"https://schema.org/InStock",
      "query":"live:9c69f3e5f27049c89a0ad3ae67f221d3",
      "fetched_at":"2026-10-24T11:48:31.383922+00:00",
      "tags_request_id":"mreq_01a16dbddad37c9086414611f4aa4b92",
      "tagged_at":"2026-10-24T11:48:42.760180+00:00",
      "tag_error":null,
      "tags":{
        "slot":"outerwear",
        "category":"technical shell jacket",
        "colours":["black","aquaculture blue"],
        "materials":[],
        "fit":"regular",
        "era":"contemporary",
        "aesthetics":["technical outerwear","gorpcore","outdoor performance","minimal"],
        "formality":1,
        "niche":1.0,
        "gender":"menswear",
        "logo_heavy":false,
        "condition":"new",
        "one_line":"Technical men’s black shell jacket with aquaculture blue zip and logo details."
      },
      "image":"/img/6fcfec76d4c10954de398a5bb6be22e4c7a789c9.jpg",
      "origin":"live",
      "condition":"new"
    }
    """

    override func setUp() {
        super.setUp()
        store = InMemoryTokenStore("test-token")
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        session = URLSession(configuration: configuration)
        client = APIClient(baseURL: baseURL, tokenStore: store, session: session)
    }

    override func tearDown() {
        session.invalidateAndCancel()
        super.tearDown()
    }

    func testBearerOnJSONGetPostAndMultipartUpload() async throws {
        StubURLProtocol.setResponse(json: #"{"id":"u1","name":"Tester","role":"tester"}"#)
        _ = try await client.me()
        XCTAssertEqual(StubURLProtocol.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer test-token")
        XCTAssertEqual(StubURLProtocol.lastRequest?.value(forHTTPHeaderField: "Content-Type"), "application/json")

        StubURLProtocol.setResponse(json: #"{"token":"next","user":{"id":"u1","name":"Tester","role":"tester"}}"#)
        _ = try await client.invite("invite-token")
        XCTAssertEqual(StubURLProtocol.lastRequest?.httpMethod, "POST")
        XCTAssertEqual(StubURLProtocol.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer test-token")
        XCTAssertEqual(StubURLProtocol.lastRequest?.value(forHTTPHeaderField: "Content-Type"), "application/json")

        StubURLProtocol.setResponse(json: """
        {"upload_id":"up1","read":{"one_line":"A jacket","aesthetics":[],"palette":[]},"pending":false,
         "route":{"id":"r1","routed":"other","context":"other","label":"Photo ideas","confidence":0.2,"reason":"New"},
         "taste":null}
        """)
        _ = try await client.upload(jpegData: Data([0xff, 0xd8, 0xff]))
        let contentType = StubURLProtocol.lastRequest?.value(forHTTPHeaderField: "Content-Type") ?? ""
        XCTAssertTrue(contentType.hasPrefix("multipart/form-data; boundary="))
        XCTAssertEqual(StubURLProtocol.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer test-token")
        XCTAssertTrue(String(decoding: StubURLProtocol.lastBody ?? Data(), as: UTF8.self)
            .contains(#"name="file"; filename="photo.jpg""#))
    }

    func testCheckoutUsesAuthenticatedOutfitRequest() async throws {
        StubURLProtocol.setResponse(json: """
        {"order_ref":"order-1","simulated":true,"lines":[
          {"title":"Jacket","source":"vinted","source_url":"https://example.test/jacket","price_gbp":25.0}
        ],"total_gbp":25.0}
        """)

        let result = try await client.checkout(outfitId: "outfit-1")
        let request = StubURLProtocol.lastRequest
        XCTAssertEqual(request?.url?.path, "/api/checkout")
        XCTAssertEqual(request?.httpMethod, "POST")
        XCTAssertEqual(request?.value(forHTTPHeaderField: "Authorization"), "Bearer test-token")
        let body = try XCTUnwrap(StubURLProtocol.lastBody)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: String])
        XCTAssertEqual(json, ["outfit_id": "outfit-1"])
        XCTAssertEqual(result.orderRef, "order-1")
        XCTAssertEqual(result.lines.first?.sourceUrl, "https://example.test/jacket")
    }

    func testEmptyTokenOmitsAuthorization() async throws {
        store.clear()
        StubURLProtocol.setResponse(json: #"{"id":"u1","name":"Tester","role":"tester"}"#)
        _ = try await client.me()
        XCTAssertNil(StubURLProtocol.lastRequest?.value(forHTTPHeaderField: "Authorization"))
    }

    func testUnauthorizedClearsTokenAndCallsHandler() async throws {
        var called = false
        client.onUnauthorized = { called = true }
        StubURLProtocol.setResponse(status: 401, json: #"{"detail":"Not authenticated"}"#)
        do {
            _ = try await client.me()
            XCTFail("Expected a 401")
        } catch let error as APIError {
            XCTAssertEqual(error.status, 401)
        }
        XCTAssertNil(store.token())
        XCTAssertTrue(called)
    }

    func testErrorMessagesAndRequestIds() async throws {
        StubURLProtocol.setResponse(status: 403, json: #"{"detail":"Tester accounts have one style"}"#)
        do {
            _ = try await client.invite("x")
            XCTFail("Expected a 403")
        } catch let error as APIError {
            XCTAssertEqual(error.message, "Tester accounts have one style")
            XCTAssertTrue(error.requestIds.isEmpty)
        }

        StubURLProtocol.setResponse(status: 502, json: #"{"detail":{"error":"x","request_ids":["r1"]}}"#)
        do {
            _ = try await client.invite("x")
            XCTFail("Expected a 502")
        } catch let error as APIError {
            XCTAssertEqual(error.message, "x")
            XCTAssertEqual(error.requestIds, ["r1"])
        }
    }

    func testCancelledTransportThrowsCancellationError() async {
        StubURLProtocol.setFailure(URLError(.cancelled))
        do {
            _ = try await client.me()
            XCTFail("Expected cancellation")
        } catch is CancellationError {
        } catch {
            XCTFail("Expected CancellationError, got \(error)")
        }
    }

    func testDeckArrayAndWrappedDecodingAndNullableTags() async throws {
        StubURLProtocol.setResponse(json: "[\(realItem)]")
        let array = try await client.deck()
        XCTAssertEqual(array.first?.id, "arcteryx.com:sidewinder-jacket-men-s-black-aquaculture-ac-size-s")

        StubURLProtocol.setResponse(json: #"{"items":[{"id":"bare","title":"Bare","tags":null,"image":"/img/a.jpg"}]}"#)
        let wrapped = try await client.deck()
        XCTAssertNil(wrapped.first?.tags)
        XCTAssertEqual(client.resolve(wrapped.first?.imagePath)?.absoluteString, "https://api.example.test/img/a.jpg")
    }

    func testSearchDecodesArrayAndWrappedLiveResults() async throws {
        StubURLProtocol.setResponse(json: "[\(realItem)]")
        let saved = try await client.search(q: "brown shoes & more")
        XCTAssertEqual(saved.count, 1)
        let components = URLComponents(url: StubURLProtocol.lastRequest!.url!, resolvingAgainstBaseURL: false)
        XCTAssertEqual(components?.queryItems?.first(where: { $0.name == "q" })?.value, "brown shoes & more")

        StubURLProtocol.setResponse(json: #"{"items":[{"id":"live","title":"Fresh"}]}"#)
        let live = try await client.searchLive(q: "loafer")
        XCTAssertEqual(live.first?.id, "live")
    }

    func testTasteHistoryChangesFallbackToProfileChanges() async throws {
        StubURLProtocol.setResponse(json: """
        {"current":null,"history":[{"version":1,"changes":null,
         "profile":{"summary":"A style","changes":["From the brief"]},"created_at":"now"}],
         "counts":{},"core":null}
        """)
        let result = try await client.taste()
        XCTAssertEqual(result.history.first?.changes, ["From the brief"])
    }

    func testSwapDecodesBareAndWrappedOutfits() async throws {
        let outfit = #"{"id":"o1","title":"Field","mood":"Easy","why":"Balanced","pieces":[],"total_gbp":0}"#
        StubURLProtocol.setResponse(json: outfit)
        let bare = try await client.swap(outfitId: "o", itemId: "i")
        XCTAssertEqual(bare.id, "o1")
        StubURLProtocol.setResponse(json: #"{"outfit":\#(outfit)}"#)
        let wrapped = try await client.swap(outfitId: "o", itemId: "i")
        XCTAssertEqual(wrapped.id, "o1")
    }

    func testPartialBriefDefaultsAndNullableMaxStyles() async throws {
        StubURLProtocol.setResponse(json: """
        [{"id":"b1","label":"One","context":"city","active":true,
          "brief":{"text":"Partial","sizes":{"tops":"S"}},
          "version":1,"counts":{"swipes":0,"outfits":0,"uploads":0}}]
        """)
        let board = try await client.boards().first!
        XCTAssertEqual(board.brief.text, "Partial")
        XCTAssertEqual(board.brief.lookingFor, Brief.default.lookingFor)
        XCTAssertEqual(board.brief.sizes.tops, "S")
        XCTAssertEqual(board.brief.sizes.waist, Brief.default.sizes.waist)

        StubURLProtocol.setResponse(json: #"{"id":"u1","name":"Tester","role":"tester","max_styles":1}"#)
        let tester = try await client.me()
        XCTAssertEqual(tester.maxStyles, 1)
        StubURLProtocol.setResponse(json: #"{"id":"u1","name":"Admin","role":"admin","max_styles":null}"#)
        let admin = try await client.me()
        XCTAssertNil(admin.maxStyles)
    }

    func testResolveRelativeAndAbsoluteURLs() {
        XCTAssertEqual(client.resolve("/img/x.jpg")?.absoluteString, "https://api.example.test/img/x.jpg")
        XCTAssertEqual(client.resolve("https://cdn.example.test/x.jpg")?.absoluteString, "https://cdn.example.test/x.jpg")
        XCTAssertNil(client.resolve("not-a-url"))
    }

    func testGBPUsesUKGroupingAndKeepsDecimalPrecision() {
        XCTAssertEqual(gbp(1250), "£1,250")
        XCTAssertEqual(gbp(39.5), "£39.50")
    }

    func testInviteLinkParsing() {
        XCTAssertEqual(InviteLink.token(from: "https://app.quenya.co.uk/?invite=abc"), "abc")
        XCTAssertEqual(InviteLink.token(from: "quenya://invite?token=def"), "def")
        XCTAssertEqual(InviteLink.token(from: "  bare-token  "), "bare-token")
    }
}
