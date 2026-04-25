"""Data models for listings, user criteria, and analysis results."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class Listing:
    """A real estate listing scraped from a platform."""

    id: str  # platform_listingid e.g. "immoscout_12345"
    platform: str  # immoscout | kleinanzeigen | immowelt | ohne-makler
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
    # First time WE scraped this listing. Empty default means "DB will fill
    # this on INSERT" — Database.save_listing populates it explicitly so the
    # canonical timestamp is set in one place. Re-scrapes of an existing row
    # MUST NOT overwrite this column (see Database.save_listing's UPDATE).
    # Distinct from scraped_at, which is refreshed on every scrape.
    first_seen_at: str = ""
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

    @property
    def days_on_market(self) -> int:
        """Whole days since we first scraped this listing.

        "We first scraped" — NOT "the listing was published". The scrapers'
        ``listing_date`` is unreliable (often relative text like "vor 3
        Tagen"), so we report what we can defend: time since first sighting.

        Empty/missing ``first_seen_at`` returns 0 — sane fallback for any
        row that somehow slipped through without a timestamp (legacy DB
        before backfill ran, hand-inserted test fixture, etc.). A negative
        delta (clock skew, future timestamp) also clamps to 0.

        Malformed ISO strings raise nothing here either: we treat them as
        "unknown, fall back to 0" so the digest never crashes on a single
        bad row. Logging that case is the caller's call; this is a pure
        property.

        Tz-aware ISO strings (e.g. ``'2026-04-01T08:00:00+00:00'``) are
        accepted: we strip the tzinfo before subtracting from a naive
        ``datetime.now()`` so we don't raise ``TypeError: can't subtract
        offset-naive and offset-aware datetimes``. Treating "now" as local
        and "first" as local-without-its-offset is fine for whole-day
        bucketing — the worst-case error is one day in either direction,
        which the caller's bucket boundaries already absorb.
        """
        if not self.first_seen_at:
            return 0
        try:
            first = datetime.fromisoformat(self.first_seen_at)
            # Strip tz so subtraction with naive datetime.now() never raises.
            if first.tzinfo is not None:
                first = first.replace(tzinfo=None)
            delta = datetime.now() - first
        except (ValueError, TypeError):
            return 0
        days = delta.days
        return days if days > 0 else 0

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
    """Analysis output for a single listing.

    Sub-score fields (``score_price``, ``score_yield``, ``score_cashflow``,
    ``score_location``) are intentionally **dataclass-only** for the
    iter-2 batch — they are NOT persisted to the ``analysis_results``
    table yet (no migration). The scorer populates them in-memory and the
    Telegram digest renders them; reloads from the DB will see the
    default ``0.0`` for each. The next schema-change batch will add a
    migration to persist them. ``from_dict`` filters unknown DB columns
    so old rows reload cleanly even when the column set drifts.
    """

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
    # Sub-scores (dataclass-only, not persisted yet — see class docstring).
    # Default 0.0 means "unknown / reloaded-from-DB"; a fresh analysis
    # will populate non-zero values via DealScorer.
    score_price: float = 0.0
    score_yield: float = 0.0
    score_cashflow: float = 0.0
    score_location: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["undervalue_reasons"] = json.dumps(d["undervalue_reasons"])
        # Strip sub-scores: not persisted yet (no DB column). Pulling them
        # into to_dict() would break save_analysis() — it does `INSERT OR
        # REPLACE INTO analysis_results (cols...)` and SQLite errors on
        # unknown column names.
        for k in ("score_price", "score_yield", "score_cashflow", "score_location"):
            d.pop(k, None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> AnalysisResult:
        d = dict(d)
        if isinstance(d.get("undervalue_reasons"), str):
            d["undervalue_reasons"] = json.loads(d["undervalue_reasons"])
        # Filter unknown keys so DB rows from older schemas reload cleanly
        # (mirrors Listing.from_dict).
        known = set(cls.__dataclass_fields__.keys())
        d = {k: v for k, v in d.items() if k in known}
        return cls(**d)
