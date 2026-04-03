"""Data models for listings, user criteria, and analysis results."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class Listing:
    """A real estate listing scraped from a platform."""

    id: str  # platform_listingid e.g. "immoscout_12345"
    platform: str  # immoscout | kleinanzeigen | immowelt
    url: str
    title: str
    price: float  # Purchase price EUR
    size_sqm: float
    rooms: float
    address: str = ""
    district: str = ""  # Hamburg Bezirk
    zip_code: str = ""
    year_built: int | None = None
    property_type: str = "apartment"  # apartment | house | multi_family
    condition: str | None = None
    floor: int | None = None
    hausgeld: float | None = None  # Monthly building management fee
    nebenkosten: float | None = None
    energy_rating: str | None = None
    balcony: bool = False
    garden: bool = False
    parking: bool = False
    listing_date: str | None = None
    description: str | None = None
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())
    image_urls: list[str] = field(default_factory=list)
    # Enriched fields for price justification analysis
    is_erbbaurecht: bool = False  # Leasehold land (Erbpacht) - major price reducer
    is_rented: bool = False  # Currently tenanted (vermietet) - 20-30% discount
    current_rent_monthly: float | None = None  # Actual current rent if rented
    is_wbs: bool = False  # Social housing obligation (Wohnberechtigungsschein)
    sonderumlage: float | None = None  # Special assessment (one-time WEG levy)
    num_units_in_building: int | None = None  # Number of units in WEG
    is_dachgeschoss: bool = False  # Attic apartment (often sloped ceilings)
    is_ausbau_needed: bool = False  # Expansion/buildout required (raw space)
    total_floors: int | None = None  # Total floors in building
    plot_size_sqm: float | None = None  # Grundstücksfläche

    @property
    def price_per_sqm(self) -> float:
        if self.size_sqm and self.size_sqm > 0:
            return self.price / self.size_sqm
        return 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["image_urls"] = json.dumps(d["image_urls"])
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Listing:
        d = dict(d)
        if isinstance(d.get("image_urls"), str):
            d["image_urls"] = json.loads(d["image_urls"])
        # Convert sqlite integer bools
        for bool_field in (
            "balcony", "garden", "parking",
            "is_erbbaurecht", "is_rented", "is_wbs",
            "is_dachgeschoss", "is_ausbau_needed",
        ):
            if bool_field in d:
                d[bool_field] = bool(d[bool_field])
        # Remove unknown fields that might come from DB
        known = set(cls.__dataclass_fields__.keys())
        d = {k: v for k, v in d.items() if k in known}
        return cls(**d)


@dataclass
class UserCriteria:
    """Search and analysis criteria provided by the user."""

    budget_min: float = 0
    budget_max: float = 1_000_000
    min_size_sqm: float = 0
    max_size_sqm: float = 999
    min_rooms: float = 1
    max_rooms: float = 10
    property_types: list[str] = field(default_factory=lambda: ["apartment"])
    districts: list[str] = field(default_factory=list)  # empty = all
    # Financing
    equity_pct: float = 20.0  # % of purchase price
    interest_rate_pct: float = 3.5
    loan_term_years: int = 25
    # Targets
    min_gross_yield_pct: float = 4.0
    min_cashflow_monthly: float = 0.0
    # Scoring preference
    risk_tolerance: str = "moderate"  # conservative | moderate | aggressive
    # Telegram
    chat_id: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["property_types"] = json.dumps(d["property_types"])
        d["districts"] = json.dumps(d["districts"])
        return d

    @classmethod
    def from_dict(cls, d: dict) -> UserCriteria:
        d = dict(d)
        if isinstance(d.get("property_types"), str):
            d["property_types"] = json.loads(d["property_types"])
        if isinstance(d.get("districts"), str):
            d["districts"] = json.loads(d["districts"])
        return cls(**d)


@dataclass
class AnalysisResult:
    """Analysis output for a single listing."""

    listing_id: str
    price_per_sqm: float = 0.0
    district_avg_price_sqm: float = 0.0
    price_vs_market_pct: float = 0.0  # negative = below market
    estimated_rent_monthly: float = 0.0
    gross_rental_yield_pct: float = 0.0
    net_rental_yield_pct: float = 0.0
    cap_rate_pct: float = 0.0
    monthly_cashflow: float = 0.0
    cash_on_cash_return_pct: float = 0.0
    total_purchase_cost: float = 0.0
    mortgage_monthly: float = 0.0
    equity_required: float = 0.0
    # Scoring
    deal_score: float = 0.0  # 0-100
    undervalue_reasons: list[str] = field(default_factory=list)
    analyzed_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["undervalue_reasons"] = json.dumps(d["undervalue_reasons"])
        return d

    @classmethod
    def from_dict(cls, d: dict) -> AnalysisResult:
        d = dict(d)
        if isinstance(d.get("undervalue_reasons"), str):
            d["undervalue_reasons"] = json.loads(d["undervalue_reasons"])
        return cls(**d)
