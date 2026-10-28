import Foundation
import Observation

enum AuthPhase: Equatable {
    case loading
    case signedOut(String?)
    case failed(String)
    case signedIn
}

enum AppTab: String, CaseIterable, Codable, Identifiable {
    case brief, swipe, outfits, find, taste

    var id: String { rawValue }
    var title: String { rawValue.capitalized }
    var symbol: String {
        switch self {
        case .brief: "text.alignleft"
        case .swipe: "hand.draw"
        case .outfits: "square.grid.2x2"
        case .find: "magnifyingglass"
        case .taste: "slider.horizontal.3"
        }
    }
}

enum BriefMode: Equatable {
    case new
    case edit
}

struct ToastMessage: Identifiable {
    let id = UUID()
    let title: String
    let lines: [String]
    var isTaste: Bool
}

private struct VerdictAttempt {
    let like: Bool
    let reason: String?
    let styleId: String
}

enum PresentedSheet: Identifiable {
    case item(Item)
    case reject(Outfit)
    case photo(UploadResult)

    var id: String {
        switch self {
        case .item(let item): "item-\(item.id)"
        case .reject(let outfit): "reject-\(outfit.id)"
        case .photo(let upload): "photo-\(upload.uploadId)"
        }
    }
}

@Observable
@MainActor
final class AppState {
    private(set) var api: APIClient
    private let tokenStore: TokenStore
    private var launchID = UUID()
    private var boardsRefreshID = UUID()
    private var tasteRefreshID = UUID()
    private var outfitBuildID = UUID()
    private var deckLoadID = UUID()
    private var outfitGenerations: [String: UUID] = [:]
    private var outfitTasks: [String: [UUID: Task<Void, Error>]] = [:]
    private var outfitTaskBusyKeys: [String: [UUID: String]] = [:]
    let cache: StyleCache
    let busy = BusyTracker()

    var authPhase: AuthPhase = .loading
    var user: User?
    var boards: [Board] = []
    var taste: TasteState?
    var outfits: [Outfit] {
        get {
            guard let styleId = activeBoard?.id else { return [] }
            return cache.snapshot(for: styleId).outfits
        }
        set {
            guard let styleId = activeBoard?.id else { return }
            cache.update(styleId) { $0.outfits = newValue }
        }
    }
    var locked: [String: [String]] {
        get {
            guard let styleId = activeBoard?.id else { return [:] }
            return cache.snapshot(for: styleId).locked
        }
        set {
            guard let styleId = activeBoard?.id else { return }
            cache.update(styleId) { $0.locked = newValue }
        }
    }
    var swapSeen: [String: [String]] {
        get {
            guard let styleId = activeBoard?.id else { return [:] }
            return cache.snapshot(for: styleId).swapSeen
        }
        set {
            guard let styleId = activeBoard?.id else { return }
            cache.update(styleId) { $0.swapSeen = newValue }
        }
    }
    var deck: [Item] {
        get {
            guard let styleId = activeBoard?.id else { return [] }
            return cache.snapshot(for: styleId).deck
        }
        set {
            guard let styleId = activeBoard?.id else { return }
            cache.update(styleId) { $0.deck = newValue }
        }
    }
    var swipedCount: Int {
        guard let styleId = activeBoard?.id else { return 0 }
        return cache.snapshot(for: styleId).swiped
    }
    var toast: ToastMessage?
    var fetchNote: String?
    var updating = false
    var tasteUpdateError: String?
    private var lastVerdictAttempts: [String: VerdictAttempt] = [:]
    var picking = true
    var deckLoading = false
    var deckError: String?
    var selectedTab: AppTab = .brief {
        didSet {
            guard user != nil, activeBoard != nil else { return }
            saveSessionSnapshot()
        }
    }
    var briefMode: BriefMode = .new
    var currentBrief: Brief = {
        var value = Brief.default
        value.text = ""
        return value
    }()
    var creatingBrief = false
    var briefError: String?
    var deckKey = 0
    var presentedSheet: PresentedSheet?
    var outfitError: String?
    var outfitLoading = false

    var activeBoard: Board? { boards.first(where: \.active) }
    var currentVersion: Int? { taste?.current?.version }
    var canCreateStyle: Bool { user?.maxStyles != 1 || boards.isEmpty }
    var hasProfile: Bool { taste?.current != nil }
    var isDebug: Bool {
        #if DEBUG
        true
        #else
        false
        #endif
    }

    init(
        tokenStore: TokenStore = KeychainTokenStore(),
        apiClient: APIClient? = nil,
        cache: StyleCache? = nil
    ) {
        self.tokenStore = tokenStore
        self.cache = cache ?? StyleCache()
        api = apiClient ?? APIClient(baseURL: AppConfig.baseURL, tokenStore: tokenStore)
        installUnauthorizedHandler()
    }

    func launch() async {
        let requestID = UUID()
        launchID = requestID
        guard tokenStore.token() != nil else {
            authPhase = .signedOut(nil)
            return
        }

        let cachedSession = cache.loadSession()
        if let cachedSession {
            user = cachedSession.user
            boards = cachedSession.boards
            taste = cachedSession.taste
            cache.activate(userId: cachedSession.userId)
            syncBriefWithActiveBoard()
            if let styleId = activeBoard?.id {
                let snapshot = cache.snapshot(for: styleId)
                if !snapshot.deck.isEmpty || !snapshot.outfits.isEmpty {
                    picking = false
                }
                selectedTab = cachedSession.lastTab ?? (
                    !snapshot.deck.isEmpty || !snapshot.outfits.isEmpty ? .swipe : .brief
                )
            } else {
                selectedTab = .brief
            }
            authPhase = .signedIn
        }

        do {
            let refreshedUser = try await api.me()
            guard !Task.isCancelled, launchID == requestID else { return }
            if let cachedSession, cachedSession.userId != refreshedUser.id {
                await cache.flush()
                cache.clear()
                resetPerUserState()
                cache.activate(userId: refreshedUser.id)
            } else {
                cache.activate(userId: refreshedUser.id)
            }
            user = refreshedUser
            authPhase = .signedIn
            let boardsRefreshed = await refreshBoards()
            guard !Task.isCancelled, launchID == requestID else { return }
            let tasteRefreshed = await refreshTaste()
            guard !Task.isCancelled, launchID == requestID else { return }
            syncBriefWithActiveBoard()
            if boardsRefreshed && tasteRefreshed {
                saveSessionSnapshot()
                if let styleId = activeBoard?.id, !cache.snapshot(for: styleId).deck.isEmpty {
                    await refreshDeckQuietly(maxAge: nil)
                }
            }
        } catch is CancellationError {
            return
        } catch let error as APIError where error.status == 401 {
            guard !Task.isCancelled, launchID == requestID else { return }
            await cache.flush()
            cache.clear()
            resetPerUserState()
            authPhase = .signedOut(nil)
        } catch {
            guard !Task.isCancelled, launchID == requestID else { return }
            if cachedSession == nil {
                authPhase = .failed(error.localizedDescription)
            }
        }
    }

    func retryLaunch() async {
        authPhase = .loading
        await launch()
    }

    func redeemInvite(_ input: String) async {
        guard let token = InviteLink.token(from: input) else {
            authPhase = .signedOut("Paste a valid invite link or token.")
            return
        }
        guard !busy.isBusy("auth.invite") else { return }
        let completed = await busy.run("auth.invite") {
            let result = try await self.api.invite(token)
            self.tokenStore.save(result.token)
            let redeemedUser = try await self.api.me()
            if self.cache.userId != nil && self.cache.userId != redeemedUser.id {
                await self.cache.flush()
                self.cache.clear()
            }
            self.user = redeemedUser
            self.cache.activate(userId: redeemedUser.id)
            self.authPhase = .signedIn
            async let boardsTask = self.refreshBoards()
            async let tasteTask = self.refreshTaste()
            let boardsRefreshed = await boardsTask
            let tasteRefreshed = await tasteTask
            self.syncBriefWithActiveBoard()
            if let styleId = self.activeBoard?.id {
                let snapshot = self.cache.snapshot(for: styleId)
                if !snapshot.deck.isEmpty || !snapshot.outfits.isEmpty {
                    self.picking = false
                }
            }
            if boardsRefreshed && tasteRefreshed { self.saveSessionSnapshot() }
        }
        if !completed {
            tokenStore.clear()
            await cache.flush()
            cache.clear()
            resetPerUserState()
            authPhase = .signedOut(busy.error("auth.invite"))
        }
    }

    func signOut() async {
        guard !busy.isBusy("auth.signout") else { return }
        await cache.flush()
        await busy.run("auth.signout") {
            _ = try await self.api.logout()
        }
        tokenStore.clear()
        cache.clear()
        resetPerUserState()
        authPhase = .signedOut(nil)
    }

    func changeBaseURL(_ value: String?) async {
        if let value, let url = AppConfig.validURL(value) {
            UserDefaults.standard.set(url.absoluteString, forKey: AppConfig.userDefaultsKey)
        } else {
            UserDefaults.standard.removeObject(forKey: AppConfig.userDefaultsKey)
        }
        tokenStore.clear()
        await cache.flush()
        cache.clear()
        resetPerUserState()
        configureAPI()
        authPhase = .signedOut(nil)
    }

    @discardableResult
    func refreshBoards() async -> Bool {
        let requestID = UUID()
        boardsRefreshID = requestID
        do {
            let result = try await api.boards()
            guard !Task.isCancelled, boardsRefreshID == requestID else { return false }
            boards = result
            saveSessionSnapshot()
            return true
        } catch is CancellationError {
            return false
        } catch {
            return false
        }
    }

    @discardableResult
    func refreshTaste() async -> Bool {
        let requestID = UUID()
        tasteRefreshID = requestID
        do {
            let result = try await api.taste()
            guard !Task.isCancelled, tasteRefreshID == requestID else { return false }
            taste = result
            saveSessionSnapshot()
            return true
        } catch is CancellationError {
            return false
        } catch {
            guard !Task.isCancelled, tasteRefreshID == requestID else { return false }
            if taste == nil {
                taste = TasteState(current: nil, history: [], counts: [:], core: nil)
            }
            return false
        }
    }

    func setTab(_ tab: AppTab) {
        selectedTab = tab
        if tab == .brief, let board = activeBoard {
            briefMode = .edit
            currentBrief = board.brief.merged()
        } else if tab == .brief {
            briefMode = .new
            currentBrief = Brief.default
            currentBrief.text = ""
        }
    }

    func startNewStyle() {
        guard canCreateStyle else { return }
        briefMode = .new
        currentBrief = Brief.default
        currentBrief.text = ""
        selectedTab = .brief
    }

    func activate(_ board: Board) async -> Bool {
        let changed = activeBoard?.id != board.id
        let key = "style.activate.\(board.id)"
        guard !busy.isBusy(key) else { return false }
        let activated = await busy.run(key) {
            if !board.active { _ = try await self.api.activate(board.id) }
        }
        guard activated else { return false }
        boards = boards.map { current in
            var updated = current
            updated.active = current.id == board.id
            return updated
        }
        currentBrief = board.brief.merged()
        saveSessionSnapshot()
        if changed { boardChanged() }
        return true
    }

    func activateFromStrip(_ board: Board) async -> Bool {
        let wasOnBrief = selectedTab == .brief
        guard await activate(board) else { return false }
        if wasOnBrief {
            selectedTab = .brief
            briefMode = .edit
        }
        return true
    }

    func selectBoardForSwipe(_ board: Board) async {
        if await activate(board) { picking = false }
    }

    func submitBrief(_ brief: Brief, photos: [Data]) async {
        guard !busy.isBusy("brief.submit") else { return }
        creatingBrief = true
        briefError = nil
        currentBrief = brief
        let completed = await busy.run("brief.submit") {
            let result: ProfileResult
            if self.briefMode == .edit {
                guard let board = self.activeBoard else {
                    throw APIError(status: 0, message: "Select a style before updating.", requestIds: [])
                }
                result = try await self.api.updateBrief(boardId: board.id, brief: brief)
                self.cache.invalidate(board.id)
                self.announce(result.taste)
            } else {
                result = try await self.api.createProfile(brief, newBoard: true)
            }
            async let tasteTask = self.refreshTaste()
            async let boardsTask = self.refreshBoards()
            let tasteRefreshed = await tasteTask
            let boardsRefreshed = await boardsTask
            for photo in photos {
                do { try await self.upload(photo, boardId: self.activeBoard?.id) }
                catch { self.showToast("Couldn't read a photo", lines: [error.localizedDescription]) }
            }
            if let jobId = result.fetchJobId {
                Task { await self.pollFetch(jobId) }
            }
            if tasteRefreshed && boardsRefreshed { self.saveSessionSnapshot() }
            self.deckKey += 1
            self.picking = false
            self.selectedTab = .swipe
        }
        if !completed { briefError = busy.error("brief.submit") }
        creatingBrief = false
    }

    func pollFetch(_ id: String) async {
        for _ in 0..<60 {
            do {
                let job = try await api.fetchJob(id)
                switch job.status {
                case "running": fetchNote = "Fetching fresh listings · \(job.tagged)/\(job.fetched) tagged"
                case "done", "complete":
                    fetchNote = "\(job.tagged) fresh listings added"
                    try? await Task.sleep(for: .seconds(5))
                    fetchNote = nil
                    return
                default:
                    fetchNote = "Live fetch failed; using the saved pool"
                    try? await Task.sleep(for: .seconds(5))
                    fetchNote = nil
                    return
                }
            } catch {
                fetchNote = nil
                return
            }
            try? await Task.sleep(for: .seconds(2))
        }
        fetchNote = nil
    }

    func ensureDeck() async {
        guard let styleId = activeBoard?.id else { return }
        if !cache.snapshot(for: styleId).deck.isEmpty {
            deckLoading = false
            deckError = nil
            return
        }
        guard !deckLoading else { return }
        await fetchDeck(for: styleId, replacing: true)
    }

    func reloadDeck() async {
        guard let styleId = activeBoard?.id else { return }
        await fetchDeck(for: styleId, replacing: true)
    }

    private func fetchDeck(for styleId: String, replacing: Bool) async {
        guard let userId = cache.userId else { return }
        let requestID = UUID()
        deckLoadID = requestID
        deckLoading = true
        deckError = nil
        defer {
            if deckLoadID == requestID { deckLoading = false }
        }

        do {
            let result = try await api.deck(n: 10)
            guard !Task.isCancelled,
                  deckLoadID == requestID,
                  cache.userId == userId,
                  activeBoard?.id == styleId else { return }
            let sentSwipeIds = Set(cache.snapshot(for: styleId).sentSwipeIds)
            cache.update(styleId) { snapshot in
                let fetched = result.filter { !sentSwipeIds.contains($0.id) }
                if replacing {
                    snapshot.deck = fetched
                } else {
                    let existing = Set(snapshot.deck.map(\.id))
                    snapshot.deck.append(contentsOf: fetched.filter { !existing.contains($0.id) })
                }
                snapshot.deckFetchedAt = Date()
            }
            ImageCache.shared.prefetch(cache.snapshot(for: styleId).deck.map(\.imagePath))
        } catch is CancellationError {
            return
        }
        catch {
            guard !Task.isCancelled,
                  deckLoadID == requestID,
                  cache.userId == userId,
                  activeBoard?.id == styleId else { return }
            if cache.snapshot(for: styleId).deck.isEmpty {
                deckError = error.localizedDescription
            }
        }
    }

    func refreshDeckQuietly(maxAge: TimeInterval?) async {
        guard let styleId = activeBoard?.id, let userId = cache.userId else { return }
        let beforeFetch = cache.snapshot(for: styleId)
        if let maxAge,
           let fetchedAt = beforeFetch.deckFetchedAt,
           Date().timeIntervalSince(fetchedAt) < maxAge {
            return
        }
        if deckLoading && beforeFetch.deck.isEmpty { return }

        do {
            let result = try await api.deck(n: 10)
            guard !Task.isCancelled,
                  cache.userId == userId,
                  activeBoard?.id == styleId else { return }
            var added: [Item] = []
            cache.update(styleId) { snapshot in
                let excluded = Set(snapshot.deck.map(\.id)).union(snapshot.sentSwipeIds)
                added = result.filter { !excluded.contains($0.id) }
                snapshot.deck.append(contentsOf: added)
                snapshot.deckFetchedAt = Date()
            }
            ImageCache.shared.prefetch(added.map(\.imagePath))
        } catch is CancellationError {
            return
        } catch {
            if activeBoard?.id == styleId,
               cache.userId == userId,
               cache.snapshot(for: styleId).deck.isEmpty {
                deckError = error.localizedDescription
            }
        }
    }

    func swipe(_ item: Item, liked: Bool) async {
        guard let styleId = activeBoard?.id else { return }
        if !cache.snapshot(for: styleId).sentSwipeIds.contains(item.id) {
            recordSwipe(item, styleId: styleId)
        }
        do {
            let result = try await api.swipe(itemId: item.id, liked: liked)
            if result.updateDue && !updating { await runUpdate() }
        } catch {
            showToast("Swipe not saved", lines: [error.localizedDescription])
        }
    }

    func recordSwipe(_ item: Item) {
        guard let styleId = activeBoard?.id else { return }
        recordSwipe(item, styleId: styleId)
    }

    private func recordSwipe(_ item: Item, styleId: String) {
        cache.update(styleId) { snapshot in
            snapshot.deck.removeAll { $0.id == item.id }
            snapshot.sentSwipeIds.append(item.id)
            if snapshot.sentSwipeIds.count > 500 {
                snapshot.sentSwipeIds.removeFirst(snapshot.sentSwipeIds.count - 500)
            }
            snapshot.swiped += 1
        }
    }

    func deckDone() async {
        await runUpdate()
        await buildOutfits()
    }

    func runUpdate() async {
        guard !updating, !busy.isBusy("taste.update") else { return }
        updating = true
        tasteUpdateError = nil
        let completed = await busy.run("taste.update") {
            do { self.announce(try await self.api.updateTaste()) }
            catch let error as APIError where error.status == 409 { }
            catch { throw error }
        }
        if !completed { tasteUpdateError = busy.error("taste.update") }
        updating = false
    }

    func announce(_ version: TasteVersion?) {
        guard let version else { return }
        let changes = Array((version.changes ?? version.profile.changes).prefix(3))
        toast = ToastMessage(title: "Taste v\(version.version)", lines: changes, isTaste: true)
        Task {
            async let tasteTask = refreshTaste()
            async let boardsTask = refreshBoards()
            _ = await (tasteTask, boardsTask)
        }
        dismissToastLater(id: toast?.id)
    }

    func showToast(_ title: String, lines: [String] = []) {
        toast = ToastMessage(title: title, lines: lines, isTaste: false)
        dismissToastLater(id: toast?.id)
    }

    func dismissToastLater(id: UUID?) {
        guard let id else { return }
        Task {
            try? await Task.sleep(for: .seconds(3.5))
            if toast?.id == id { toast = nil }
        }
    }

    func buildOutfits() async {
        guard let styleId = activeBoard?.id else { return }
        guard !busy.isBusy("outfits.build") else { return }
        let requestID = UUID()
        outfitBuildID = requestID
        selectedTab = .outfits
        outfitLoading = true
        outfitError = nil
        defer {
            if outfitBuildID == requestID { outfitLoading = false }
        }
        let completed = await busy.run("outfits.build") {
            try await self.runOutfitGeneration(styleId: styleId, busyKey: "outfits.build") { generation in
                let result = try await self.api.outfits(count: 3).outfits
                guard !Task.isCancelled,
                      self.outfitBuildID == requestID,
                      self.isCurrentOutfitGeneration(styleId: styleId, generation: generation) else { return }
                self.cache.update(styleId) { $0.outfits = result }
            }
        }
        if !completed { outfitError = busy.error("outfits.build") }
    }

    func runOutfitGeneration(
        styleId: String,
        busyKey: String? = nil,
        operation: @escaping @MainActor (UUID) async throws -> Void
    ) async throws {
        let generation = UUID()
        outfitGenerations[styleId] = generation
        let task = Task { try await operation(generation) }
        outfitTasks[styleId, default: [:]][generation] = task
        if let busyKey { outfitTaskBusyKeys[styleId, default: [:]][generation] = busyKey }
        defer {
            outfitTasks[styleId]?[generation] = nil
            if outfitTasks[styleId]?.isEmpty == true { outfitTasks[styleId] = nil }
            outfitTaskBusyKeys[styleId]?[generation] = nil
            if outfitTaskBusyKeys[styleId]?.isEmpty == true { outfitTaskBusyKeys[styleId] = nil }
        }
        try await task.value
    }

    func isCurrentOutfitGeneration(styleId: String, generation: UUID) -> Bool {
        outfitGenerations[styleId] == generation
    }

    @discardableResult
    func swapOutfit(_ outfit: Outfit, item: Item, styleId: String) async -> Bool {
        let key = "outfit.\(outfit.id).swap.\(item.id)"
        return await busy.run(key) {
            let current = self.cache.snapshot(for: styleId)
            let next = try await self.api.swap(
                outfitId: outfit.id,
                itemId: item.id,
                excludeIds: Array((current.swapSeen[outfit.id] ?? []).suffix(300))
            )
            self.cache.update(styleId) { snapshot in
                var locks = snapshot.locked[outfit.id] ?? []
                if locks.contains(item.id) { locks.removeAll { $0 == item.id } }
                snapshot.locked[next.id] = locks
                var seen = Set<String>()
                snapshot.swapSeen[next.id] = ((snapshot.swapSeen[outfit.id] ?? []) + outfit.pieces.map(\.id))
                    .filter { seen.insert($0).inserted }
                if let index = snapshot.outfits.firstIndex(where: { $0.id == outfit.id }) {
                    snapshot.outfits[index] = next
                }
            }
        }
    }

    @discardableResult
    func rebuildOutfit(_ outfit: Outfit, styleId: String) async -> Bool {
        let key = "outfit.\(outfit.id).rebuild"
        return await busy.run(key) {
            let snapshot = self.cache.snapshot(for: styleId)
            let keep = snapshot.locked[outfit.id] ?? []
            let otherPieces = snapshot.outfits.filter { $0.id != outfit.id }.flatMap { $0.pieces.map(\.id) }
            let excluded = outfit.pieces.map(\.id).filter { !keep.contains($0) } + otherPieces
            try await self.runOutfitGeneration(styleId: styleId, busyKey: key) { generation in
                let response = try await self.api.outfits(count: 1, lockedIds: keep, excludeIds: excluded)
                guard let next = response.outfits.first else {
                    throw APIError(status: 409, message: "No outfit fits those locks and your budget. Unlock a piece and retry.", requestIds: [])
                }
                guard !Task.isCancelled,
                      self.isCurrentOutfitGeneration(styleId: styleId, generation: generation) else { return }
                self.cache.update(styleId) { snapshot in
                    snapshot.locked[next.id] = keep
                    if let index = snapshot.outfits.firstIndex(where: { $0.id == outfit.id }) {
                        snapshot.outfits[index] = next
                    }
                }
            }
        }
    }

    func replace(_ old: Outfit, with next: Outfit, styleId: String) {
        cache.update(styleId) { snapshot in
            guard let index = snapshot.outfits.firstIndex(where: { $0.id == old.id }) else { return }
            snapshot.outfits[index] = next
        }
    }

    @discardableResult
    func verdict(_ outfit: Outfit, like: Bool, reason: String? = nil, styleId: String? = nil) async -> Bool {
        guard let styleId = styleId ?? activeBoard?.id else { return false }
        let key = "verdict.\(outfit.id)"
        guard !busy.isBusy(key) else { return false }
        lastVerdictAttempts[outfit.id] = VerdictAttempt(like: like, reason: reason, styleId: styleId)
        return await busy.run(key) {
            let result = try await self.api.verdict(
                outfitId: outfit.id,
                verdict: like ? "like" : "reject",
                reason: reason
            )
            var updated = result.outfit
            if updated.pieces.isEmpty { updated.pieces = outfit.pieces }
            updated.verdict = like ? "like" : "reject"
            if !like { updated.reason = reason }
            self.replace(outfit, with: updated, styleId: styleId)
            if self.activeBoard?.id == styleId { self.announce(result.taste) }
        }
    }

    @discardableResult
    func retryVerdict(_ outfit: Outfit) async -> Bool {
        guard let attempt = lastVerdictAttempts[outfit.id] else { return false }
        return await verdict(outfit, like: attempt.like, reason: attempt.reason, styleId: attempt.styleId)
    }

    func upload(_ jpeg: Data, boardId: String? = nil) async throws {
        let result = try await api.upload(jpegData: jpeg)
        if result.pending {
            if let boardId {
                await assignPhoto(result, boardId: boardId)
            } else {
                presentedSheet = .photo(result)
            }
        } else {
            announce(result.taste)
        }
    }

    @discardableResult
    func assignPhoto(_ upload: UploadResult, boardId: String?, label: String? = nil) async -> Bool {
        return await busy.run("photo.assign") {
            let result = try await self.api.assignUpload(uploadId: upload.uploadId, boardId: boardId, label: label)
            if self.activeBoard?.id != result.board.id { self.boardChanged() }
            else { await self.refreshBoards() }
            self.announce(result.taste)
            self.presentedSheet = nil
        }
    }

    func correctTags(item: Item, note: String) async throws -> TagCorrectionResult {
        let result = try await api.correctTags(itemId: item.id, note: note)
        announce(result.taste)
        return result
    }

    func boardChanged() {
        deckLoadID = UUID()
        outfitBuildID = UUID()
        for (styleId, tasks) in outfitTasks {
            for (generation, task) in tasks {
                task.cancel()
                if let key = outfitTaskBusyKeys[styleId]?[generation] { busy.cancel(key) }
            }
        }
        outfitTasks.removeAll()
        outfitTaskBusyKeys.removeAll()
        for styleId in Array(outfitGenerations.keys) {
            outfitGenerations[styleId] = UUID()
        }
        deckLoading = false
        deckError = nil
        outfitError = nil
        outfitLoading = false
        deckKey += 1
        Task {
            async let tasteTask = refreshTaste()
            async let boardsTask = refreshBoards()
            _ = await (tasteTask, boardsTask)
        }
    }

    func sceneBecameActive() async {
        guard authPhase == .signedIn else { return }
        await refreshDeckQuietly(maxAge: 600)
    }

    func flushCache() async {
        await cache.flush()
    }

    func currentBaseURLText() -> String {
        AppConfig.baseURL.absoluteString
    }

    private func configureAPI() {
        api = APIClient(baseURL: AppConfig.baseURL, tokenStore: tokenStore)
        installUnauthorizedHandler()
    }

    private func syncBriefWithActiveBoard() {
        if let board = activeBoard {
            briefMode = .edit
            currentBrief = board.brief.merged()
        } else {
            briefMode = .new
            currentBrief = Brief.default
            currentBrief.text = ""
        }
    }

    private func saveSessionSnapshot() {
        guard let user else { return }
        cache.saveSession(
            SessionSnapshot(
                userId: user.id,
                user: user,
                boards: boards,
                taste: taste,
                lastTab: selectedTab
            )
        )
    }

    private func resetPerUserState() {
        user = nil
        boards = []
        taste = nil
        picking = true
        selectedTab = .brief
        briefMode = .new
        currentBrief = .default
        currentBrief.text = ""
        deckLoading = false
        deckError = nil
        outfitLoading = false
        outfitError = nil
        fetchNote = nil
        toast = nil
        presentedSheet = nil
        deckLoadID = UUID()
        outfitBuildID = UUID()
        deckKey += 1
    }

    private func installUnauthorizedHandler() {
        api.onUnauthorized = { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                await self.cache.flush()
                self.cache.clear()
                self.resetPerUserState()
                self.authPhase = .signedOut(nil)
            }
        }
    }
}
