import Foundation
import Security

protocol TokenStore {
    func token() -> String?
    func save(_ token: String)
    func clear()
}

final class KeychainTokenStore: TokenStore {
    private let service = "uk.ardivia.quenya"
    private let account = "session"
    #if targetEnvironment(simulator)
    private let simulatorFallbackKey = "uk.ardivia.quenya.session"
    #endif

    func token() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: CFTypeRef?
        if SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
           let data = result as? Data,
           let token = String(data: data, encoding: .utf8) {
            return token
        }
        #if targetEnvironment(simulator)
        return UserDefaults.standard.string(forKey: simulatorFallbackKey)
        #else
        return nil
        #endif
    }

    func save(_ token: String) {
        clear()
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecValueData as String: Data(token.utf8)
        ]
        SecItemAdd(query as CFDictionary, nil)
        #if targetEnvironment(simulator)
        UserDefaults.standard.set(token, forKey: simulatorFallbackKey)
        #endif
    }

    func clear() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        SecItemDelete(query as CFDictionary)
        #if targetEnvironment(simulator)
        UserDefaults.standard.removeObject(forKey: simulatorFallbackKey)
        #endif
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
