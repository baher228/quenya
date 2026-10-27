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
    private(set) static var lastRequest: URLRequest?
    private(set) static var lastBody: Data?

    static func setResponse(status: Int = 200, json: String, headers: [String: String] = ["Content-Type": "application/json"]) {
        lock.lock()
        storedResponse = StubResponse(status: status, headers: headers, body: Data(json.utf8))
        storedFailure = nil
        lastRequest = nil
        lastBody = nil
        lock.unlock()
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

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let body = Self.readBody(from: request)
        Self.record(request, body: body)
        if let error = Self.failure() {
            client?.urlProtocol(self, didFailWithError: error)
            return
        }
        let response = Self.response()
        let http = HTTPURLResponse(
            url: request.url!,
            statusCode: response.status,
            httpVersion: "HTTP/1.1",
            headerFields: response.headers
        )!
        client?.urlProtocol(self, didReceive: http, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: response.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}

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
