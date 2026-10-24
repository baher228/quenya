from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Slot = Literal["outerwear", "top", "knitwear", "bottom", "dress", "shoes", "bag", "accessory"]
Gender = Literal["menswear", "womenswear", "unisex"]
Condition = Literal["second-hand", "new"]
Fit = Literal["slim", "regular", "relaxed", "oversized", "cropped", "unknown"]
Era = Literal["contemporary", "y2k", "2010s", "90s", "80s", "70s", "60s", "older", "unknown"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    price_ceiling_gbp: float = Field(ge=0)
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
