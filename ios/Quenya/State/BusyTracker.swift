import Observation

@MainActor
@Observable
final class BusyTracker {
    private(set) var active: Set<String> = []
    private(set) var errors: [String: String] = [:]

    var anyBusy: Bool { !active.isEmpty }

    func isBusy(_ key: String) -> Bool { active.contains(key) }
    func error(_ key: String) -> String? { errors[key] }
    func clearError(_ key: String) { errors[key] = nil }

    @discardableResult
    func run(_ key: String, _ op: () async throws -> Void) async -> Bool {
        guard !active.contains(key) else { return false }
        active.insert(key)
        errors[key] = nil
        defer { active.remove(key) }
        do {
            try await op()
            return true
        } catch is CancellationError {
            return false
        } catch {
            errors[key] = error.localizedDescription
            return false
        }
    }
}
