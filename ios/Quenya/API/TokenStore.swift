import Foundation
import Security
#if DEBUG
import os

private let keychainLogger = Logger(
    subsystem: "uk.co.quenya.app",
    category: "KeychainTokenStore"
)
#endif

protocol TokenStore {
    func token() -> String?
    func save(_ token: String)
    func clear()
}

final class KeychainTokenStore: TokenStore {
    private let service = "uk.co.quenya.app"
    private let account = "session"

    func token() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        #if DEBUG
        keychainLogger.debug("SecItemCopyMatching OSStatus: \(status, privacy: .public)")
        #endif
        guard status == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    func save(_ token: String) {
        clear()
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecValueData as String: Data(token.utf8)
        ]
        let status = SecItemAdd(query as CFDictionary, nil)
        #if DEBUG
        keychainLogger.debug("SecItemAdd OSStatus: \(status, privacy: .public)")
        #endif
    }

    func clear() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        SecItemDelete(query as CFDictionary)
    }
}

final class InMemoryTokenStore: TokenStore {
    private(set) var value: String?

    init(_ value: String? = nil) {
        self.value = value
    }

    func token() -> String? { value }
    func save(_ token: String) { value = token }
    func clear() { value = nil }
}
