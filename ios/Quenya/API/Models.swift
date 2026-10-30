import Foundation

struct Brief: Codable, Equatable {
    var text: String
    var lookingFor: String
    var pieces: [String]
    var includeShoes: Bool
    var gender: String
    var condition: String
    var niche: Double
    var budgetGbp: Double
    var sizes: Sizes

    static let `default` = Brief(
        text: "Autumn in London. Workwear but cleaner, Japanese labels, things that age well. No big logos.",
        lookingFor: "outfits",
        pieces: ["outerwear", "top", "knitwear", "bottom", "shoes"],
        includeShoes: true,
        gender: "menswear",
        condition: "both",
        niche: 0.7,
        budgetGbp: 300,
        sizes: Sizes(tops: "M", waist: "32", shoes: "9")
    )

    enum CodingKeys: String, CodingKey {
        case text, lookingFor, pieces, includeShoes, gender, condition, niche, budgetGbp, sizes
    }

    init(
        text: String,
        lookingFor: String = "outfits",
        pieces: [String] = Brief.defaultPieces,
        includeShoes: Bool = true,
        gender: String = "menswear",
        condition: String = "both",
        niche: Double = 0.7,
        budgetGbp: Double = 300,
        sizes: Sizes = Brief.defaultSizes
    ) {
        self.text = text
        self.lookingFor = lookingFor
        self.pieces = pieces
        self.includeShoes = includeShoes
        self.gender = gender
        self.condition = condition
        self.niche = niche
        self.budgetGbp = budgetGbp
        self.sizes = sizes
    }

    private static let defaultPieces = ["outerwear", "top", "knitwear", "bottom", "shoes"]
    private static let defaultSizes = Sizes(tops: "M", waist: "32", shoes: "9")

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? Brief.default.text
        lookingFor = try container.decodeIfPresent(String.self, forKey: .lookingFor) ?? Brief.default.lookingFor
        pieces = try container.decodeIfPresent([String].self, forKey: .pieces) ?? Brief.default.pieces
        includeShoes = try container.decodeIfPresent(Bool.self, forKey: .includeShoes) ?? Brief.default.includeShoes
        gender = try container.decodeIfPresent(String.self, forKey: .gender) ?? Brief.default.gender
        condition = try container.decodeIfPresent(String.self, forKey: .condition) ?? Brief.default.condition
        niche = try container.decodeIfPresent(Double.self, forKey: .niche) ?? Brief.default.niche
        budgetGbp = try container.decodeIfPresent(Double.self, forKey: .budgetGbp) ?? Brief.default.budgetGbp
        sizes = try container.decodeIfPresent(Sizes.self, forKey: .sizes) ?? Brief.default.sizes
    }

    func merged(over base: Brief = .default) -> Brief {
        var result = base
        result.text = text
        result.lookingFor = lookingFor
        result.pieces = pieces
        result.includeShoes = includeShoes
        result.gender = gender
        result.condition = condition
        result.niche = niche
        result.budgetGbp = budgetGbp
        result.sizes = sizes.merged(over: base.sizes)
        return result
    }
}

struct Sizes: Codable, Equatable {
    var tops: String
    var waist: String
    var shoes: String

    enum CodingKeys: String, CodingKey { case tops, waist, shoes }

    init(tops: String, waist: String, shoes: String) {
        self.tops = tops
        self.waist = waist
        self.shoes = shoes
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        tops = try container.decodeIfPresent(String.self, forKey: .tops) ?? Brief.default.sizes.tops
        waist = try container.decodeIfPresent(String.self, forKey: .waist) ?? Brief.default.sizes.waist
        shoes = try container.decodeIfPresent(String.self, forKey: .shoes) ?? Brief.default.sizes.shoes
    }

    func merged(over base: Sizes) -> Sizes {
        Sizes(tops: tops, waist: waist, shoes: shoes)
    }
}

struct ItemTags: Codable, Equatable {
    var slot: String
    var category: String
    var colours: [String]
    var materials: [String]
    var fit: String
    var era: String
    var aesthetics: [String]
    var formality: Int
    var niche: Double
    var gender: String
    var logoHeavy: Bool
    var condition: String
    var oneLine: String

    enum CodingKeys: String, CodingKey {
        case slot, category, colours, materials, fit, era, aesthetics, formality, niche, gender
        case logoHeavy, condition, oneLine
    }

    init(
        slot: String = "", category: String = "", colours: [String] = [],
        materials: [String] = [], fit: String = "", era: String = "unknown",
        aesthetics: [String] = [], formality: Int = 0, niche: Double = 0,
        gender: String = "", logoHeavy: Bool = false, condition: String = "",
        oneLine: String = ""
    ) {
        self.slot = slot
        self.category = category
        self.colours = colours
        self.materials = materials
        self.fit = fit
        self.era = era
        self.aesthetics = aesthetics
        self.formality = formality
        self.niche = niche
        self.gender = gender
        self.logoHeavy = logoHeavy
        self.condition = condition
        self.oneLine = oneLine
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        slot = try c.decodeIfPresent(String.self, forKey: .slot) ?? ""
        category = try c.decodeIfPresent(String.self, forKey: .category) ?? ""
        colours = try c.decodeIfPresent([String].self, forKey: .colours) ?? []
        materials = try c.decodeIfPresent([String].self, forKey: .materials) ?? []
        fit = try c.decodeIfPresent(String.self, forKey: .fit) ?? ""
        era = try c.decodeIfPresent(String.self, forKey: .era) ?? "unknown"
        aesthetics = try c.decodeIfPresent([String].self, forKey: .aesthetics) ?? []
        formality = try c.decodeIfPresent(Int.self, forKey: .formality) ?? 0
        niche = try c.decodeIfPresent(Double.self, forKey: .niche) ?? 0
        gender = try c.decodeIfPresent(String.self, forKey: .gender) ?? ""
        logoHeavy = try c.decodeIfPresent(Bool.self, forKey: .logoHeavy) ?? false
        condition = try c.decodeIfPresent(String.self, forKey: .condition) ?? ""
        oneLine = try c.decodeIfPresent(String.self, forKey: .oneLine) ?? ""
    }
}

struct Item: Codable, Identifiable, Equatable {
    var id: String
    var title: String
    var brand: String?
    var priceGbp: Double
    var size: String?
    var source: String
    var sourceUrl: String
    var condition: String?
    var image: String?
    var imageUrl: String?
    var imagePreview: String?
    var tags: ItemTags?
    var fetchedAt: String?
    var origin: String?

    enum CodingKeys: String, CodingKey {
        case id, title, brand, priceGbp, size, source, sourceUrl
        case condition, image, imageUrl, imagePreview, tags, fetchedAt, origin
    }

    var imagePath: String? { image ?? imageUrl }

    init(
        id: String, title: String, brand: String? = nil, priceGbp: Double = 0,
        size: String? = nil, source: String = "", sourceUrl: String = "",
        condition: String? = nil, image: String? = nil, imageUrl: String? = nil,
        imagePreview: String? = nil, tags: ItemTags? = nil, fetchedAt: String? = nil,
        origin: String? = nil
    ) {
        self.id = id
        self.title = title
        self.brand = brand
        self.priceGbp = priceGbp
        self.size = size
        self.source = source
        self.sourceUrl = sourceUrl
        self.condition = condition
        self.image = image
        self.imageUrl = imageUrl
        self.imagePreview = imagePreview
        self.tags = tags
        self.fetchedAt = fetchedAt
        self.origin = origin
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        brand = try c.decodeIfPresent(String.self, forKey: .brand)
        priceGbp = try c.decodeIfPresent(Double.self, forKey: .priceGbp) ?? 0
        size = try c.decodeIfPresent(String.self, forKey: .size)
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? ""
        sourceUrl = try c.decodeIfPresent(String.self, forKey: .sourceUrl) ?? ""
        condition = try c.decodeIfPresent(String.self, forKey: .condition)
        image = try c.decodeIfPresent(String.self, forKey: .image)
        imageUrl = try c.decodeIfPresent(String.self, forKey: .imageUrl)
        imagePreview = try c.decodeIfPresent(String.self, forKey: .imagePreview)
        tags = try c.decodeIfPresent(ItemTags.self, forKey: .tags)
        fetchedAt = try c.decodeIfPresent(String.self, forKey: .fetchedAt)
        origin = try c.decodeIfPresent(String.self, forKey: .origin)
    }
}

struct User: Codable, Equatable {
    var id: String
    var name: String
    var role: String
    var maxStyles: Int?
    var styleCount: Int

    enum CodingKeys: String, CodingKey { case id, name, role, maxStyles, styleCount }

    init(id: String, name: String, role: String, maxStyles: Int? = nil, styleCount: Int = 0) {
        self.id = id
        self.name = name
        self.role = role
        self.maxStyles = maxStyles
        self.styleCount = styleCount
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        name = try container.decodeIfPresent(String.self, forKey: .name) ?? ""
        role = try container.decodeIfPresent(String.self, forKey: .role) ?? ""
        maxStyles = try container.decodeIfPresent(Int.self, forKey: .maxStyles)
        styleCount = try container.decodeIfPresent(Int.self, forKey: .styleCount) ?? 0
    }
}

struct BoardCounts: Codable, Equatable {
    var swipes: Int
    var outfits: Int
    var uploads: Int
}

struct Board: Codable, Identifiable, Equatable {
    var id: String
    var label: String
    var context: String
    var active: Bool
    var brief: Brief
    var version: Int?
    var counts: BoardCounts
}

struct TasteProfile: Codable, Equatable {
    var summary: String
    var likes: [String: Double]
    var avoids: [String: Double]
    var rules: [String]
    var palette: [String]
    var nicheLevel: Double
    var priceCeilingGbp: Double
    var conditionPreference: String
    var searchQueries: [String]
    var changes: [String]

    enum CodingKeys: String, CodingKey {
        case summary, likes, avoids, rules, palette, nicheLevel
        case priceCeilingGbp, conditionPreference, searchQueries, changes
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        summary = try c.decodeIfPresent(String.self, forKey: .summary) ?? ""
        likes = try c.decodeIfPresent([String: Double].self, forKey: .likes) ?? [:]
        avoids = try c.decodeIfPresent([String: Double].self, forKey: .avoids) ?? [:]
        rules = try c.decodeIfPresent([String].self, forKey: .rules) ?? []
        palette = try c.decodeIfPresent([String].self, forKey: .palette) ?? []
        nicheLevel = try c.decodeIfPresent(Double.self, forKey: .nicheLevel) ?? 0
        priceCeilingGbp = try c.decodeIfPresent(Double.self, forKey: .priceCeilingGbp) ?? 0
        conditionPreference = try c.decodeIfPresent(String.self, forKey: .conditionPreference) ?? "both"
        searchQueries = try c.decodeIfPresent([String].self, forKey: .searchQueries) ?? []
        changes = try c.decodeIfPresent([String].self, forKey: .changes) ?? []
    }
}

struct TasteVersion: Codable, Equatable {
    var version: Int
    var profile: TasteProfile
    var changes: [String]?
    var requestId: String?
    var latencyMs: Int?
}

struct TasteHistoryEntry: Codable, Identifiable, Equatable {
    var version: Int
    var changes: [String]?
    var profile: TasteProfile?
    var createdAt: String
    var requestId: String?
    var signalCounts: [String: Int]?
    var id: Int { version }

    enum CodingKeys: String, CodingKey {
        case version, changes, profile, createdAt, requestId, signalCounts
    }

    init(
        version: Int,
        changes: [String]?,
        profile: TasteProfile?,
        createdAt: String,
        requestId: String?,
        signalCounts: [String: Int]?
    ) {
        self.version = version
        self.changes = changes ?? profile?.changes ?? []
        self.profile = profile
        self.createdAt = createdAt
        self.requestId = requestId
        self.signalCounts = signalCounts
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        version = try container.decode(Int.self, forKey: .version)
        profile = try container.decodeIfPresent(TasteProfile.self, forKey: .profile)
        changes = try container.decodeIfPresent([String].self, forKey: .changes)
            ?? profile?.changes
            ?? []
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        requestId = try container.decodeIfPresent(String.self, forKey: .requestId)
        signalCounts = try container.decodeIfPresent([String: Int].self, forKey: .signalCounts)
    }
}

struct TasteState: Codable, Equatable {
    var current: TasteVersion?
    var history: [TasteHistoryEntry]
    var counts: [String: Int]
    var core: CoreProfile?
}

struct CoreRule: Codable, Identifiable, Equatable {
    var rule: String
    var boards: [String]
    var id: String { rule }
}

struct CoreProfile: Codable, Equatable {
    var rules: [CoreRule]
    var likes: [String: Double]
    var avoids: [String: Double]
    var palette: [String]
    var sizes: [String: String]
}

struct Outfit: Codable, Identifiable, Equatable {
    var id: String
    var title: String
    var mood: String
    var why: String
    var pieces: [Item]
    var totalGbp: Double
    var verdict: String?
    var reason: String?
    var requestId: String?
    var tasteVersion: Int?
}

struct OutfitsResponse: Codable {
    var outfits: [Outfit]
    var dropped: Int?
    var requestIds: [String]?
}

struct Route: Codable, Identifiable, Equatable {
    var id: String
    var routed: String
    var context: String
    var label: String
    var confidence: Double
    var reason: String
    var requestId: String?
}

struct InspirationRead: Codable, Equatable {
    var oneLine: String
    var aesthetics: [String]
    var palette: [String]
}

struct UploadResult: Codable, Equatable {
    var uploadId: String
    var read: InspirationRead
    var pending: Bool
    var route: Route
    var taste: TasteVersion?
}

struct ProfileResult: Codable {
    var profileId: String
    var brief: Brief
    var taste: TasteVersion
    var version: Int
    var requestId: String?
    var fetchJobId: String?
    var board: Board
    var route: Route?
}

struct FetchJob: Codable {
    var status: String
    var fetched: Int
    var tagged: Int
    var failed: Int
    var errors: [String]
}

struct Checkout: Codable {
    var orderRef: String
    var simulated: Bool
    var lines: [CheckoutLine]
    var totalGbp: Double
}

struct CheckoutLine: Codable, Identifiable {
    var title: String
    var source: String
    var sourceUrl: String
    var priceGbp: Double
    var id: String { sourceUrl }
}

struct TaskStats: Codable, Identifiable {
    var task: String
    var calls: Int
    var ok: Int
    var failed: Int
    var p50LatencyMs: Int?
    var p95LatencyMs: Int?
    var totalTokens: Int?
    var lastRequestIds: [String]
    var id: String { task }
}

struct AuthResult: Codable {
    var token: String
    var user: User
}

struct TasteReply: Codable {
    var version: Int
    var profile: TasteProfile
    var changes: [String]?
    var requestId: String?
}
