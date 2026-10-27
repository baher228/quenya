import Foundation
#if DEBUG
import os

private let apiLogger = Logger(subsystem: "uk.co.quenya.app", category: "api")
#endif

struct APIError: Error, LocalizedError {
    let status: Int
    let message: String
    let requestIds: [String]
    var errorDescription: String? { message }
}

final class APIClient {
    let baseURL: URL
    let tokenStore: TokenStore
    let session: URLSession
    var onUnauthorized: (() -> Void)?
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(baseURL: URL, tokenStore: TokenStore, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.tokenStore = tokenStore
        self.session = session
        encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
    }

    func resolve(_ string: String?) -> URL? {
        guard let string, !string.isEmpty else { return nil }
        if let url = URL(string: string), let scheme = url.scheme?.lowercased(),
           ["http", "https"].contains(scheme), url.host != nil {
            return url
        }
        guard string.hasPrefix("/") else { return nil }
        return URL(string: string, relativeTo: baseURL)?.absoluteURL
    }

    func invite(_ token: String) async throws -> AuthResult {
        try await post("/api/auth/invite", body: InviteBody(token: token))
    }

    func me() async throws -> User { try await get("/api/me") }
    func logout() async throws -> [String: Bool] { try await postEmpty("/api/auth/logout") }

    func createProfile(_ brief: Brief, newBoard: Bool = true) async throws -> ProfileResult {
        try await post("/api/profile", body: CreateProfileBody(brief: brief, newBoard: newBoard))
    }

    func updateBrief(boardId: String, brief: Brief) async throws -> ProfileResult {
        try await put("/api/boards/\(escaped(boardId))/brief", body: BriefBody(brief: brief))
    }

    func fetchJob(_ id: String) async throws -> FetchJob {
        try await get("/api/fetch/\(escaped(id))")
    }

    func boards() async throws -> [Board] { try await get("/api/boards") }
    func activate(_ id: String) async throws -> BoardEnvelope {
        try await postEmpty("/api/boards/\(escaped(id))/activate")
    }

    func deck(n: Int = 10) async throws -> [Item] {
        let data = try await request(path: "/api/deck?n=\(n)", method: "GET")
        if let items = try? decoder.decode([Item].self, from: data) { return items }
        return try decoder.decode(ItemList.self, from: data).items
    }

    func swipe(itemId: String, liked: Bool) async throws -> SwipeResult {
        try await post("/api/swipe", body: SwipeBody(itemId: itemId, liked: liked))
    }

    func updateTaste() async throws -> TasteVersion { try await postEmpty("/api/taste/update") }
    func taste() async throws -> TasteState { try await get("/api/taste") }

    func outfits(count: Int, lockedIds: [String] = [], excludeIds: [String] = []) async throws -> OutfitsResponse {
        try await post("/api/outfits", body: OutfitRequestBody(count: count, lockedIds: lockedIds, excludeIds: excludeIds))
    }

    func swap(outfitId: String, itemId: String, excludeIds: [String] = []) async throws -> Outfit {
        let data = try await postData(
            "/api/outfits/\(escaped(outfitId))/swap",
            body: SwapBody(itemId: itemId, excludeIds: Array(excludeIds.suffix(300)))
        )
        if let wrapped = try? decoder.decode(OutfitEnvelope.self, from: data) {
            return wrapped.outfit
        }
        return try decoder.decode(Outfit.self, from: data)
    }

    func verdict(outfitId: String, verdict: String, reason: String? = nil) async throws -> VerdictResult {
        try await post(
            "/api/outfits/\(escaped(outfitId))/verdict",
            body: VerdictBody(verdict: verdict, reason: reason)
        )
    }

    func correctTags(itemId: String, note: String) async throws -> TagCorrectionResult {
        try await post("/api/items/\(escaped(itemId))/correct-tags", body: NoteBody(note: note))
    }

    func upload(jpegData: Data) async throws -> UploadResult {
        let boundary = "Quenya-\(UUID().uuidString)"
        var body = Data()
        body.appendUTF8("--\(boundary)\r\n")
        body.appendUTF8("Content-Disposition: form-data; name=\"file\"; filename=\"photo.jpg\"\r\n")
        body.appendUTF8("Content-Type: image/jpeg\r\n\r\n")
        body.append(jpegData)
        body.appendUTF8("\r\n--\(boundary)--\r\n")
        var request = URLRequest(url: baseURL.appending(path: "/api/uploads"))
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        try addAuthorization(to: &request)
        return try await send(request, as: UploadResult.self)
    }

    func assignUpload(uploadId: String, boardId: String?, label: String? = nil) async throws -> AssignmentResult {
        try await post(
            "/api/uploads/\(escaped(uploadId))/assign",
            body: AssignUploadBody(boardId: boardId, label: label)
        )
    }

    func search(q: String) async throws -> [Item] {
        var components = URLComponents(url: baseURL.appending(path: "/api/search"), resolvingAgainstBaseURL: false)
        components?.queryItems = [URLQueryItem(name: "q", value: q), URLQueryItem(name: "limit", value: "20")]
        guard let url = components?.url else {
            throw APIError(status: 0, message: "Can't reach the Quenya API.", requestIds: [])
        }
        return try await decodeItemList(request(url: url, method: "GET"))
    }

    func searchLive(q: String) async throws -> [Item] {
        let data = try await postData("/api/search/live", body: SearchBody(q: q))
        if let wrapped = try? decoder.decode(ItemList.self, from: data) { return wrapped.items }
        return try decoder.decode([Item].self, from: data)
    }

    func checkout(outfitId: String) async throws -> Checkout {
        try await post("/api/checkout", body: CheckoutBody(outfitId: outfitId))
    }
    func stats() async throws -> [TaskStats] { try await get("/api/model/stats") }
    func core() async throws -> CoreProfile { try await get("/api/core") }
    func correctRoute(routeId: String, boardId: String?, label: String? = nil) async throws -> RouteCorrectionResult {
        try await post("/api/route/\(escaped(routeId))/correct", body: CorrectRouteBody(boardId: boardId, label: label))
    }
    func rename(id: String, label: String) async throws -> BoardEnvelope {
        try await patch("/api/boards/\(escaped(id))", body: RenameBody(label: label))
    }

    private func decodeItemList(_ data: Data) throws -> [Item] {
        if let items = try? decoder.decode([Item].self, from: data) { return items }
        return try decoder.decode(ItemList.self, from: data).items
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        try await send(try makeRequest(path: path, method: "GET"), as: T.self)
    }

    private func post<T: Decodable, Body: Encodable>(_ path: String, body: Body) async throws -> T {
        try await send(try makeRequest(path: path, method: "POST", body: body), as: T.self)
    }

    private func postEmpty<T: Decodable>(_ path: String) async throws -> T {
        try await send(try makeRequest(path: path, method: "POST"), as: T.self)
    }

    private func put<T: Decodable, Body: Encodable>(_ path: String, body: Body) async throws -> T {
        try await send(try makeRequest(path: path, method: "PUT", body: body), as: T.self)
    }

    private func patch<T: Decodable, Body: Encodable>(_ path: String, body: Body) async throws -> T {
        try await send(try makeRequest(path: path, method: "PATCH", body: body), as: T.self)
    }

    private func postData<Body: Encodable>(_ path: String, body: Body) async throws -> Data {
        try await send(try makeRequest(path: path, method: "POST", body: body), as: Data.self)
    }

    private func request(path: String, method: String) async throws -> Data {
        try await send(try makeRequest(path: path, method: method), as: Data.self)
    }

    private func request(url: URL, method: String) async throws -> Data {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 120
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        try addAuthorization(to: &request)
        return try await send(request, as: Data.self)
    }

    private func makeRequest(path: String, method: String) throws -> URLRequest {
        try makeRequest(path: path, method: method, body: Optional<EmptyBody>.none)
    }

    private func makeRequest<Body: Encodable>(path: String, method: String, body: Body?) throws -> URLRequest {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw APIError(status: 0, message: "Can't reach the Quenya API.", requestIds: [])
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 120
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let body {
            request.httpBody = try encoder.encode(body)
        }
        try addAuthorization(to: &request)
        return request
    }

    private func addAuthorization(to request: inout URLRequest) throws {
        if let token = tokenStore.token() {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
    }

    private func send<T: Decodable>(_ request: URLRequest, as type: T.Type) async throws -> T {
        #if DEBUG
        let method = request.httpMethod ?? "GET"
        let path = request.url.map { $0.path + ($0.query.map { "?\($0)" } ?? "") } ?? ""
        apiLogger.debug("\(method, privacy: .public) \(path, privacy: .public)")
        #endif

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            if error is CancellationError || (error as? URLError)?.code == .cancelled || Task.isCancelled {
                throw CancellationError()
            }
            let timedOut = (error as? URLError)?.code == .timedOut
            throw APIError(
                status: 0,
                message: timedOut ? "The request timed out." : "Can't reach the Quenya API.",
                requestIds: []
            )
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError(status: 0, message: "Can't reach the Quenya API.", requestIds: [])
        }
        guard (200..<300).contains(http.statusCode) else {
            if http.statusCode == 401 {
                tokenStore.clear()
                onUnauthorized?()
            }
            throw apiError(data: data, status: http.statusCode)
        }
        if type == Data.self, let value = data as? T { return value }
        return try decoder.decode(type, from: data)
    }

    private func apiError(data: Data, status: Int) -> APIError {
        let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        let detail: Any?
        if let root, let value = root["detail"] {
            detail = value
        } else {
            detail = root.map { $0 as Any }
        }
        var message = "Request failed (\(status))"
        var requestIds: [String] = []
        if let text = detail as? String {
            message = text
        } else if let object = detail as? [String: Any] {
            message = object["error"] as? String ?? message
            requestIds = object["request_ids"] as? [String] ?? []
        }
        return APIError(status: status, message: message, requestIds: requestIds)
    }

    private func escaped(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(CharacterSet(charactersIn: "/"))) ?? value
    }
}

private struct EmptyBody: Encodable {}
private struct InviteBody: Encodable { var token: String }
private struct CreateProfileBody: Encodable { var brief: Brief; var newBoard: Bool }
private struct BriefBody: Encodable { var brief: Brief }
private struct SwipeBody: Encodable { var itemId: String; var liked: Bool }
private struct OutfitRequestBody: Encodable { var count: Int; var lockedIds: [String]; var excludeIds: [String] }
private struct SwapBody: Encodable { var itemId: String; var excludeIds: [String] }
private struct VerdictBody: Encodable { var verdict: String; var reason: String? }
private struct NoteBody: Encodable { var note: String }
private struct SearchBody: Encodable { var q: String }
private struct CheckoutBody: Encodable { var outfitId: String }
private struct RenameBody: Encodable { var label: String }

private struct AssignUploadBody: Encodable {
    var boardId: String?
    var label: String?

    enum CodingKeys: String, CodingKey { case boardId, label }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(boardId, forKey: .boardId)
        try container.encodeIfPresent(label, forKey: .label)
    }
}

private struct CorrectRouteBody: Encodable {
    var boardId: String?
    var label: String?

    enum CodingKeys: String, CodingKey { case boardId, label }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(boardId, forKey: .boardId)
        try container.encodeIfPresent(label, forKey: .label)
    }
}

private struct ItemList: Decodable { var items: [Item] }
private struct OutfitEnvelope: Decodable { var outfit: Outfit }
struct BoardEnvelope: Decodable { var board: Board }
struct SwipeResult: Decodable { var swipesSinceUpdate: Int; var updateDue: Bool }
struct VerdictResult: Decodable { var outfit: Outfit; var taste: TasteVersion? }
struct TagCorrectionResult: Decodable { var tags: ItemTags?; var requestId: String?; var taste: TasteVersion? }
struct AssignmentResult: Decodable { var board: Board; var taste: TasteVersion? }
struct RouteCorrectionResult: Decodable { var route: Route; var board: Board; var taste: TasteVersion? }

private extension Data {
    mutating func appendUTF8(_ string: String) {
        append(Data(string.utf8))
    }
}
