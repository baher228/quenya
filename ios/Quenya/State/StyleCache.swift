import Foundation
import Observation

struct StyleSnapshot: Codable, Equatable {
    var userId: String
    var styleId: String
    var deck: [Item] = []
    var swiped = 0
    var sentSwipeIds: [String] = []
    var outfits: [Outfit] = []
    var locked: [String: [String]] = [:]
    var swapSeen: [String: [String]] = [:]
    var deckFetchedAt: Date?
}

struct SessionSnapshot: Codable, Equatable {
    var userId: String
    var user: User
    var boards: [Board]
    var taste: TasteState?
    var lastTab: AppTab? = nil
}

@MainActor
@Observable
final class StyleCache {
    nonisolated static var defaultDirectory: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("QuenyaSnapshots", isDirectory: true)
    }

    private let directory: URL
    private(set) var userId: String?
    private(set) var styles: [String: StyleSnapshot] = [:]

    @ObservationIgnored private let encoder: JSONEncoder
    @ObservationIgnored private let decoder: JSONDecoder

    init(directory: URL = StyleCache.defaultDirectory) {
        self.directory = directory
        encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
    }

    func activate(userId: String) {
        let storedUserIds = [self.userId, loadSession()?.userId].compactMap { $0 }
        if storedUserIds.contains(where: { $0 != userId }) {
            clear()
        }

        self.userId = userId
        styles = [:]
        let styleDirectory = directory.appendingPathComponent(fileComponent(userId), isDirectory: true)
        guard let files = try? FileManager.default.contentsOfDirectory(
            at: styleDirectory,
            includingPropertiesForKeys: nil
        ) else { return }

        for file in files where file.pathExtension == "json" {
            guard let data = try? Data(contentsOf: file),
                  let snapshot = try? decoder.decode(StyleSnapshot.self, from: data),
                  snapshot.userId == userId else { continue }
            styles[snapshot.styleId] = snapshot
        }
    }

    func snapshot(for styleId: String) -> StyleSnapshot {
        styles[styleId] ?? StyleSnapshot(userId: userId ?? "", styleId: styleId)
    }

    func update(_ styleId: String, _ mutate: (inout StyleSnapshot) -> Void) {
        guard let userId else { return }
        var snapshot = styles[styleId] ?? StyleSnapshot(userId: userId, styleId: styleId)
        mutate(&snapshot)
        snapshot.userId = userId
        snapshot.styleId = styleId
        styles[styleId] = snapshot
        write(snapshot, to: directory.appendingPathComponent(fileComponent(userId), isDirectory: true)
            .appendingPathComponent("\(fileComponent(styleId)).json"))
    }

    func invalidate(_ styleId: String) {
        update(styleId) {
            $0.deck = []
            $0.outfits = []
            $0.locked = [:]
            $0.swapSeen = [:]
            $0.deckFetchedAt = nil
        }
    }

    func loadSession() -> SessionSnapshot? {
        let file = directory.appendingPathComponent("session.json")
        guard let data = try? Data(contentsOf: file) else { return nil }
        return try? decoder.decode(SessionSnapshot.self, from: data)
    }

    func saveSession(_ snapshot: SessionSnapshot) {
        if userId != snapshot.userId {
            activate(userId: snapshot.userId)
        }
        write(snapshot, to: directory.appendingPathComponent("session.json"))
    }

    func clear() {
        try? FileManager.default.removeItem(at: directory)
        userId = nil
        styles = [:]
    }

    private func write<T: Encodable>(_ value: T, to file: URL) {
        do {
            try FileManager.default.createDirectory(
                at: file.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try encoder.encode(value).write(to: file, options: .atomic)
        } catch {
            return
        }
    }

    private func fileComponent(_ value: String) -> String {
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        return value.unicodeScalars.map { allowed.contains($0) ? String($0) : "_" }.joined()
    }
}
