import Foundation

enum AppConfig {
    static let userDefaultsKey = "QuenyaAPIBaseURL"
    static let productionURL = URL(string: "https://quenya-rho.vercel.app")!

    static var baseURL: URL {
        #if DEBUG
        if let value = UserDefaults.standard.string(forKey: userDefaultsKey),
           let url = validURL(value) {
            return url
        }
        #endif
        if let value = Bundle.main.object(forInfoDictionaryKey: "QuenyaAPIBaseURL") as? String,
           let url = validURL(value) {
            return url
        }
        return productionURL
    }

    static func validURL(_ value: String) -> URL? {
        guard let components = URLComponents(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = components.scheme?.lowercased(),
              ["http", "https"].contains(scheme),
              components.host != nil else { return nil }
        return components.url
    }
}
