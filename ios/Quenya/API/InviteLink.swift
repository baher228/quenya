import Foundation

enum InviteLink {
    static func token(from input: String) -> String? {
        let value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return nil }
        if let components = URLComponents(string: value) {
            if components.scheme == "quenya" {
                return components.queryItems?.first(where: { $0.name == "token" })?.value
            }
            if let token = components.queryItems?.first(where: { $0.name == "invite" })?.value {
                return token
            }
        }
        return value
    }
}
