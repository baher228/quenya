import Foundation
import Observation

enum AuthPhase: Equatable {
    case loading
    case signedOut(String?)
    case failed(String)
    case signedIn
}

enum AppTab: String, CaseIterable, Identifiable {
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
    let cache: StyleCache

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
    var picking = true
    var deckLoading = false
    var deckError: String?
    var selectedTab: AppTab = .brief
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
            }
            authPhase = .signedIn
        }

        do {
            let refreshedUser = try await api.me()
            guard !Task.isCancelled, launchID == requestID else { return }
            if let cachedSession, cachedSession.userId != refreshedUser.id {
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
        authPhase = .loading
        do {
            let result = try await api.invite(token)
            tokenStore.save(result.token)
            let redeemedUser = try await api.me()
            if cache.userId != nil && cache.userId != redeemedUser.id {
                cache.clear()
            }
            user = redeemedUser
            cache.activate(userId: redeemedUser.id)
            authPhase = .signedIn
            let boardsRefreshed = await refreshBoards()
            let tasteRefreshed = await refreshTaste()
            syncBriefWithActiveBoard()
            if let styleId = activeBoard?.id {
                let snapshot = cache.snapshot(for: styleId)
                if !snapshot.deck.isEmpty || !snapshot.outfits.isEmpty {
                    picking = false
                }
            }
            if boardsRefreshed && tasteRefreshed { saveSessionSnapshot() }
        } catch {
            tokenStore.clear()
            cache.clear()
            resetPerUserState()
            authPhase = .signedOut(error.localizedDescription)
        }
    }

    func signOut() async {
        _ = try? await api.logout()
        tokenStore.clear()
        cache.clear()
        resetPerUserState()
        authPhase = .signedOut(nil)
    }

    func changeBaseURL(_ value: String?) {
        if let value, let url = AppConfig.validURL(value) {
            UserDefaults.standard.set(url.absoluteString, forKey: AppConfig.userDefaultsKey)
        } else {
            UserDefaults.standard.removeObject(forKey: AppConfig.userDefaultsKey)
        }
        tokenStore.clear()
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
        if tab == .swipe {
            Task { await refreshBoards() }
        } else if tab == .brief, let board = activeBoard {
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
        if !board.active {
            do {
                _ = try await api.activate(board.id)
            } catch {
                showToast("Couldn't switch board", lines: [error.localizedDescription])
                return false
            }
        }
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

    func selectBoardForSwipe(_ board: Board) async {
        if await activate(board) { picking = false }
    }

    func submitBrief(_ brief: Brief, photos: [Data]) async {
        creatingBrief = true
        briefError = nil
        currentBrief = brief
        do {
            let result: ProfileResult
            if briefMode == .edit {
                guard let board = activeBoard else {
                    throw APIError(status: 0, message: "Select a style before updating.", requestIds: [])
                }
                result = try await api.updateBrief(boardId: board.id, brief: brief)
                cache.invalidate(board.id)
                announce(result.taste)
            } else {
                result = try await api.createProfile(brief, newBoard: true)
            }
            let tasteRefreshed = await refreshTaste()
            let boardsRefreshed = await refreshBoards()
            for photo in photos {
                do { try await upload(photo, boardId: activeBoard?.id) }
                catch { showToast("Couldn't read a photo", lines: [error.localizedDescription]) }
            }
            if let jobId = result.fetchJobId {
                Task { await pollFetch(jobId) }
            }
            if tasteRefreshed && boardsRefreshed { saveSessionSnapshot() }
            deckKey += 1
            picking = false
            selectedTab = .swipe
        } catch {
            briefError = error.localizedDescription
        }
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
        guard !updating else { return }
        updating = true
        defer { updating = false }
        do { announce(try await api.updateTaste()) }
        catch let error as APIError where error.status == 409 { }
        catch { showToast("Taste update failed", lines: [error.localizedDescription]) }
    }

    func announce(_ version: TasteVersion?) {
        guard let version else { return }
        let changes = Array((version.changes ?? version.profile.changes).prefix(3))
        toast = ToastMessage(title: "Taste v\(version.version)", lines: changes, isTaste: true)
        Task {
            await refreshTaste()
            await refreshBoards()
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
        let requestID = UUID()
        outfitBuildID = requestID
        selectedTab = .outfits
        outfitLoading = true
        outfitError = nil
        defer {
            if outfitBuildID == requestID { outfitLoading = false }
        }
        do {
            let result = try await api.outfits(count: 3).outfits
            guard !Task.isCancelled, outfitBuildID == requestID else { return }
            cache.update(styleId) { $0.outfits = result }
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled, outfitBuildID == requestID else { return }
            outfitError = error.localizedDescription
        }
    }

    func replace(_ old: Outfit, with next: Outfit) {
        outfits = outfits.map { $0.id == old.id ? next : $0 }
    }

    func verdict(_ outfit: Outfit, like: Bool, reason: String? = nil) async {
        do {
            let result = try await api.verdict(
                outfitId: outfit.id,
                verdict: like ? "like" : "reject",
                reason: reason
            )
            var updated = result.outfit
            if updated.pieces.isEmpty { updated.pieces = outfit.pieces }
            updated.verdict = like ? "like" : "reject"
            if !like { updated.reason = reason }
            replace(outfit, with: updated)
            announce(result.taste)
        } catch {
            showToast("Couldn't save that", lines: [error.localizedDescription])
        }
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

    func assignPhoto(_ upload: UploadResult, boardId: String?, label: String? = nil) async {
        presentedSheet = nil
        do {
            let result = try await api.assignUpload(uploadId: upload.uploadId, boardId: boardId, label: label)
            if activeBoard?.id != result.board.id { boardChanged() }
            else { await refreshBoards() }
            announce(result.taste)
        } catch {
            showToast("Couldn't file that photo", lines: [error.localizedDescription])
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
        deckLoading = false
        deckError = nil
        outfitError = nil
        outfitLoading = false
        deckKey += 1
        Task {
            await refreshTaste()
            await refreshBoards()
        }
    }

    func sceneBecameActive() async {
        guard authPhase == .signedIn else { return }
        await refreshDeckQuietly(maxAge: 600)
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
            SessionSnapshot(userId: user.id, user: user, boards: boards, taste: taste)
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
                self.cache.clear()
                self.resetPerUserState()
                self.authPhase = .signedOut(nil)
            }
        }
    }
}
