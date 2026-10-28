import Foundation
import Observation

private struct EncodableSnapshot: @unchecked Sendable {
    let encode: () throws -> Data

    init<T: Encodable>(_ value: T) {
        encode = {
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .iso8601
            return try encoder.encode(value)
        }
    }
}

private struct PendingSnapshotWrite {
    let value: EncodableSnapshot
    let generation: UInt64
    let sequence: UInt64
}

final class StyleCachePersistence: @unchecked Sendable {
    private let queue = DispatchQueue(label: "uk.co.quenya.style-cache-persistence", qos: .utility)
    private var generation: UInt64 = 0
    private var sequence: UInt64 = 0
    private var pending: [URL: PendingSnapshotWrite] = [:]
    private var writeCounts: [URL: Int] = [:]

    func schedule<T: Encodable>(_ value: T, to file: URL) {
        let snapshot = EncodableSnapshot(value)
        queue.async {
            self.sequence &+= 1
            let write = PendingSnapshotWrite(
                value: snapshot,
                generation: self.generation,
                sequence: self.sequence
            )
            self.pending[file] = write
            self.queue.asyncAfter(deadline: .now() + .milliseconds(500)) {
                guard self.pending[file]?.sequence == write.sequence else { return }
                self.writePending(at: file)
            }
        }
    }

    func flush() async {
        await withCheckedContinuation { continuation in
            queue.async {
                self.writePending()
                continuation.resume()
            }
        }
    }

    func remove(directory: URL) {
        queue.sync {
            self.generation &+= 1
            self.pending.removeAll()
            try? FileManager.default.removeItem(at: directory)
        }
    }

    func writeCount(at file: URL) async -> Int {
        await withCheckedContinuation { continuation in
            queue.async {
                continuation.resume(returning: self.writeCounts[file, default: 0])
            }
        }
    }

    private func writePending(at file: URL? = nil) {
        let writes: [(URL, PendingSnapshotWrite)]
        if let file {
            guard let write = pending.removeValue(forKey: file) else { return }
            writes = [(file, write)]
        } else {
            writes = pending.map { ($0.key, $0.value) }
            pending.removeAll()
        }

        for (file, write) in writes where write.generation == generation {
            do {
                try FileManager.default.createDirectory(
                    at: file.deletingLastPathComponent(),
                    withIntermediateDirectories: true
                )
                try write.value.encode().write(to: file, options: .atomic)
                writeCounts[file, default: 0] += 1
            } catch {
                continue
            }
        }
    }
}

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

    @ObservationIgnored private let decoder: JSONDecoder
    @ObservationIgnored private let persistence = StyleCachePersistence()

    init(directory: URL = StyleCache.defaultDirectory) {
        self.directory = directory
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
        persistence.schedule(snapshot, to: directory.appendingPathComponent(fileComponent(userId), isDirectory: true)
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
        persistence.schedule(snapshot, to: directory.appendingPathComponent("session.json"))
    }

    func flush() async {
        await persistence.flush()
    }

    func writeCount(forStyleId styleId: String) async -> Int {
        guard let userId else { return 0 }
        let file = directory.appendingPathComponent(fileComponent(userId), isDirectory: true)
            .appendingPathComponent("\(fileComponent(styleId)).json")
        return await persistence.writeCount(at: file)
    }

    func clear() {
        userId = nil
        styles = [:]
        persistence.remove(directory: directory)
    }

    private func fileComponent(_ value: String) -> String {
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        return value.unicodeScalars.map { allowed.contains($0) ? String($0) : "_" }.joined()
    }
}
