import Foundation

final class StubURLProtocol: URLProtocol {
    struct StubResponse {
        var status: Int
        var headers: [String: String]
        var body: Data
    }

    private static let lock = NSLock()
    private static var storedResponse = StubResponse(status: 200, headers: [:], body: Data())
    private static var storedFailure: URLError?
    private static var heldPaths = Set<String>()
    private static var pending: [String: [StubURLProtocol]] = [:]
    private(set) static var lastRequest: URLRequest?
    private(set) static var lastBody: Data?
    private var stopped = false

    static func setResponse(status: Int = 200, json: String, headers: [String: String] = ["Content-Type": "application/json"]) {
        lock.lock()
        storedResponse = StubResponse(status: status, headers: headers, body: Data(json.utf8))
        storedFailure = nil
        heldPaths = []
        pending = [:]
        lastRequest = nil
        lastBody = nil
        lock.unlock()
    }

    static func holdRequests(path: String) {
        lock.lock()
        heldPaths.insert(path)
        lock.unlock()
    }

    static func pendingCount(path: String) -> Int {
        lock.lock()
        defer { lock.unlock() }
        return pending[path]?.count ?? 0
    }

    static func completeNext(path: String, status: Int, json: String) {
        lock.lock()
        let protocolInstance = pending[path]?.isEmpty == false ? pending[path]?.removeFirst() : nil
        if pending[path]?.isEmpty == true { pending[path] = nil }
        lock.unlock()
        protocolInstance?.complete(
            StubResponse(status: status, headers: ["Content-Type": "application/json"], body: Data(json.utf8))
        )
    }

    static func completeLast(path: String, status: Int, json: String) {
        lock.lock()
        let protocolInstance = pending[path]?.popLast()
        if pending[path]?.isEmpty == true { pending[path] = nil }
        lock.unlock()
        protocolInstance?.complete(
            StubResponse(status: status, headers: ["Content-Type": "application/json"], body: Data(json.utf8))
        )
    }

    static func setFailure(_ error: URLError) {
        lock.lock()
        storedFailure = error
        lastRequest = nil
        lastBody = nil
        lock.unlock()
    }

    private static func record(_ request: URLRequest, body: Data?) {
        lock.lock()
        lastRequest = request
        lastBody = body
        lock.unlock()
    }

    private static func response() -> StubResponse {
        lock.lock()
        defer { lock.unlock() }
        return storedResponse
    }

    private static func failure() -> URLError? {
        lock.lock()
        defer { lock.unlock() }
        return storedFailure
    }

    private static func hold(_ protocolInstance: StubURLProtocol, path: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard heldPaths.contains(path) else { return false }
        pending[path, default: []].append(protocolInstance)
        return true
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let body = Self.readBody(from: request)
        Self.record(request, body: body)
        if let error = Self.failure() {
            client?.urlProtocol(self, didFailWithError: error)
            return
        }
        if Self.hold(self, path: request.url?.path ?? "") { return }
        let response = Self.response()
        complete(response)
    }

    private func complete(_ response: StubResponse) {
        guard !stopped, let url = request.url else { return }
        let http = HTTPURLResponse(
            url: url,
            statusCode: response.status,
            httpVersion: "HTTP/1.1",
            headerFields: response.headers
        )!
        client?.urlProtocol(self, didReceive: http, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: response.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() { stopped = true }

    private static func readBody(from request: URLRequest) -> Data? {
        if let body = request.httpBody { return body }
        guard let stream = request.httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return data
    }
}
