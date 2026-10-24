from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Slot = Literal["outerwear", "top", "knitwear", "bottom", "dress", "shoes", "bag", "accessory"]
Gender = Literal["menswear", "womenswear", "unisex"]
Condition = Literal["second-hand", "new"]
Fit = Literal["slim", "regular", "relaxed", "oversized", "cropped", "unknown"]
Era = Literal["contemporary", "y2k", "2010s", "90s", "80s", "70s", "60s", "older", "unknown"]
BriefGender = Literal["menswear", "womenswear", "both"]
BriefCondition = Literal["second-hand", "new", "both"]
LookingFor = Literal["outfits", "pieces"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SizeProfile(StrictModel):
    tops: str | None = None
    waist: str | None = None
    shoes: str | None = None


class ProfileBrief(StrictModel):
    text: str = Field(min_length=1)
    looking_for: LookingFor
    pieces: list[Slot] = Field(default_factory=list)
    gender: BriefGender
    condition: BriefCondition
    niche: float = Field(ge=0, le=1)
    budget_gbp: float = Field(gt=0)
    sizes: SizeProfile = Field(default_factory=SizeProfile)


class ItemTags(StrictModel):
    slot: Slot
    category: str
    colours: list[str]
    materials: list[str]
    fit: Fit
    era: Era
    aesthetics: list[str]
    formality: int = Field(ge=1, le=5)
    niche: float = Field(ge=0, le=1)
    gender: Gender
    logo_heavy: bool
    condition: Condition
    one_line: str


class InspirationGarment(StrictModel):
    slot: Slot
    category: str
    colours: list[str]


class InspirationRead(StrictModel):
    garments: list[InspirationGarment]
    aesthetics: list[str]
    palette: list[str]
    fit: Fit
    era: Era
    search_queries: list[str]
    one_line: str


class TasteProfile(StrictModel):
    summary: str
    likes: dict[str, float]
    avoids: dict[str, float]
    rules: list[str]
    palette: list[str]
    niche_level: float = Field(ge=0, le=1)
    price_ceiling_gbp: float
    condition_preference: Literal["second-hand", "new", "both"]
    search_queries: list[str]
    changes: list[str]


class Outfit(StrictModel):
    title: str
    mood: str
    piece_ids: list[str]
    why: str


class StylistOutput(StrictModel):
    outfits: list[Outfit]


class SwipeRequest(StrictModel):
    item_id: str = Field(min_length=1)
    liked: bool


class OutfitRequest(StrictModel):
    count: int = Field(default=3, ge=1, le=5)
    locked_ids: list[str] = Field(default_factory=list)
    exclude_ids: list[str] = Field(default_factory=list)


class SwapRequest(StrictModel):
    item_id: str = Field(min_length=1)


class VerdictRequest(StrictModel):
    verdict: Literal["like", "reject"]
    reason: str | None = Field(default=None, max_length=1000)


class CheckoutRequest(StrictModel):
    outfit_id: str = Field(min_length=1)


class LiveSearchRequest(StrictModel):
    q: str = Field(min_length=1, max_length=300)


class TasteUpdateResponse(StrictModel):
    version: int
    profile: TasteProfile
    changes: list[str]
    request_id: str | None
    latency_ms: int
