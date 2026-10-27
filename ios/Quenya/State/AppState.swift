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

    var authPhase: AuthPhase = .loading
    var user: User?
    var boards: [Board] = []
    var taste: TasteState?
    var outfits: [Outfit] = []
    var toast: ToastMessage?
    var fetchNote: String?
    var updating = false
    var picking = true
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

    init(tokenStore: TokenStore = KeychainTokenStore()) {
        self.tokenStore = tokenStore
        api = APIClient(baseURL: AppConfig.baseURL, tokenStore: tokenStore)
        installUnauthorizedHandler()
    }

    func launch() async {
        guard tokenStore.token() != nil else {
            authPhase = .signedOut(nil)
            return
        }
        do {
            user = try await api.me()
            authPhase = .signedIn
            await refreshBoards()
            await refreshTaste()
            syncBriefWithActiveBoard()
        } catch let error as APIError where error.status == 401 {
            authPhase = .signedOut(nil)
        } catch {
            authPhase = .failed(error.localizedDescription)
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
            user = try await api.me()
            authPhase = .signedIn
            await refreshBoards()
            await refreshTaste()
            syncBriefWithActiveBoard()
        } catch {
            tokenStore.clear()
            authPhase = .signedOut(error.localizedDescription)
        }
    }

    func signOut() async {
        _ = try? await api.logout()
        tokenStore.clear()
        user = nil
        boards = []
        taste = nil
        outfits = []
        authPhase = .signedOut(nil)
    }

    func changeBaseURL(_ value: String?) {
        if let value, let url = AppConfig.validURL(value) {
            UserDefaults.standard.set(url.absoluteString, forKey: AppConfig.userDefaultsKey)
        } else {
            UserDefaults.standard.removeObject(forKey: AppConfig.userDefaultsKey)
        }
        tokenStore.clear()
        user = nil
        boards = []
        taste = nil
        configureAPI()
        authPhase = .signedOut(nil)
    }

    func refreshBoards() async {
        do { boards = try await api.boards() }
        catch { }
    }

    func refreshTaste() async {
        do { taste = try await api.taste() }
        catch { taste = TasteState(current: nil, history: [], counts: [:], core: nil) }
    }

    func setTab(_ tab: AppTab) {
        selectedTab = tab
        if tab == .swipe {
            picking = true
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
                announce(result.taste)
            } else {
                result = try await api.createProfile(brief, newBoard: true)
            }
            outfits = []
            await refreshTaste()
            await refreshBoards()
            for photo in photos {
                do { try await upload(photo, boardId: activeBoard?.id) }
                catch { showToast("Couldn't read a photo", lines: [error.localizedDescription]) }
            }
            if let jobId = result.fetchJobId {
                Task { await pollFetch(jobId) }
            }
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

    func loadDeck() async -> [Item] {
        do { return try await api.deck(n: 10) }
        catch {
            showToast("Couldn't load the deck", lines: [error.localizedDescription])
            return []
        }
    }

    func swipe(_ item: Item, liked: Bool) async {
        do {
            let result = try await api.swipe(itemId: item.id, liked: liked)
            if result.updateDue && !updating { await runUpdate() }
        } catch {
            showToast("Swipe not saved", lines: [error.localizedDescription])
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
        selectedTab = .outfits
        outfitLoading = true
        outfitError = nil
        defer { outfitLoading = false }
        do { outfits = try await api.outfits(count: 3).outfits }
        catch { outfitError = error.localizedDescription }
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
        outfits = []
        deckKey += 1
        Task {
            await refreshTaste()
            await refreshBoards()
        }
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

    private func installUnauthorizedHandler() {
        api.onUnauthorized = { [weak self] in
            Task { @MainActor in
                self?.user = nil
                self?.authPhase = .signedOut(nil)
            }
        }
    }
}
