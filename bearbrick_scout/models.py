"""Core data types shared by sources, rules, storage and reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class Source(str, Enum):
    EBAY = "ebay"
    MERCARI_US = "mercari_us"
    BUYEE_YAHOO = "buyee_yahoo"
    BUYEE_MERCARI = "buyee_mercari"

    @property
    def label(self) -> str:
        return {
            "ebay": "eBay",
            "mercari_us": "Mercari US",
            "buyee_yahoo": "Buyee · Yahoo Auctions JP",
            "buyee_mercari": "Buyee · Mercari JP",
        }[self.value]


class ListingType(str, Enum):
    AUCTION = "auction"
    BUY_IT_NOW = "buy_it_now"


class Status(str, Enum):
    MATCH = "match"
    REVIEW = "review"  # "Needs a look"
    REJECT = "reject"


@dataclass
class Listing:
    source: Source
    source_id: str
    url: str
    title: str
    price_usd: float
    # None means "unknown" (e.g. calculated shipping we could not resolve).
    shipping_usd: float | None
    ships_from: str | None  # ISO country code, e.g. "US", "JP"
    listing_type: ListingType
    best_offer: bool = False
    price_original: str | None = None  # e.g. "5,780 YEN"
    title_original: str | None = None  # untranslated title when different
    condition: str | None = None
    image_urls: list[str] = field(default_factory=list)
    description: str | None = None
    bid_count: int | None = None
    ends_at: str | None = None  # ISO timestamp or human text ("5 hour(s)")
    seller: str | None = None
    detail_loaded: bool = False

    @property
    def key(self) -> str:
        return f"{self.source.value}:{self.source_id}"

    @property
    def in_us(self) -> bool:
        return (self.ships_from or "").upper() == "US"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        d["listing_type"] = self.listing_type.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Listing":
        d = dict(d)
        d["source"] = Source(d["source"])
        d["listing_type"] = ListingType(d["listing_type"])
        return cls(**d)


@dataclass
class Contents:
    """What a listing contains, as best we can tell."""

    sets_400: int = 0  # 400% figures (a 400% packaged with its 100% counts as one)
    loose_100: int = 0  # 100% figures that are NOT the companion of a 400% set
    figures_1000: int = 0
    # "yes" = box confirmed, "no" = explicitly no box, "unknown" = can't tell
    box: str = "unknown"
    confident: bool = False  # piece counts are reliable
    notes: list[str] = field(default_factory=list)
    excluded_reason: str | None = None  # junk/fake/not-a-figure etc.

    @property
    def is_empty(self) -> bool:
        return not (self.sets_400 or self.loose_100 or self.figures_1000)

    @property
    def needs_box(self) -> bool:
        return bool(self.sets_400 or self.figures_1000)

    def describe(self) -> str:
        parts = []
        if self.figures_1000:
            parts.append(f"{self.figures_1000}× 1000%")
        if self.sets_400:
            parts.append(f"{self.sets_400}× 400% (or 100%+400% set)")
        if self.loose_100:
            parts.append(f"{self.loose_100}× loose 100%")
        return ", ".join(parts) or "unknown contents"


@dataclass
class Verdict:
    status: Status
    total_usd: float | None  # price (+ shipping when it counts)
    cap_usd: float | None
    rule: str  # human-readable cap breakdown
    reasons: list[str] = field(default_factory=list)
