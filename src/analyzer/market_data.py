"""Per-city real estate market reference data.

Originally Hamburg-only (`HamburgMarketData`). Generalised to a
``CityMarketData`` that loads its numbers from
``data/cities/{slug}.json`` so multiple cities can be benchmarked
against their own averages instead of all silently inheriting Hamburg's
€4 771/m² baseline.

The legacy class name ``HamburgMarketData`` is preserved as an alias so
existing tests and call sites that construct it without arguments still
work — they implicitly target the Hamburg city slug.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


# Repository root (two parents up from this file: src/analyzer/ → repo).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CITIES_DIR = _REPO_ROOT / "data" / "cities"
_LEGACY_HAMBURG_PATH = _REPO_ROOT / "data" / "hamburg_market_data.json"


def _resolve_city_path(city_slug: str) -> Path:
    """Locate the per-city JSON.

    Preference: ``data/cities/{slug}.json``. Falls back to the legacy
    ``data/hamburg_market_data.json`` for the Hamburg slug only — kept
    for one iteration so a deploy without the new directory still works.
    """
    new_path = _CITIES_DIR / f"{city_slug}.json"
    if new_path.exists():
        return new_path
    if city_slug == "hamburg" and _LEGACY_HAMBURG_PATH.exists():
        return _LEGACY_HAMBURG_PATH
    return new_path  # caller will hit FileNotFoundError when opening


class CityMarketData:
    """Per-city district-level market averages used for scoring.

    Loaded from ``data/cities/{slug}.json``. The first positional
    argument is now a ``city_slug`` (default ``"hamburg"``); a ``Path``
    or path-like ``str`` is accepted for backwards compatibility with
    test fixtures that pass a custom JSON path.
    """

    def __init__(self, city_slug: str | os.PathLike[str] = "hamburg"):
        # Backwards compat: tests like ``HamburgMarketData("data/hamburg_market_data.json")``
        # pass a path instead of a slug. Detect by looking for "/" or ".json".
        slug_str = str(city_slug)
        if "/" in slug_str or "\\" in slug_str or slug_str.endswith(".json"):
            data_path = Path(slug_str)
            self.city_slug = data_path.stem.replace("_market_data", "")
        else:
            self.city_slug = slug_str.lower().strip()
            data_path = _resolve_city_path(self.city_slug)

        with open(data_path) as f:
            self._data = json.load(f)

        self._districts = self._data.get("districts", {})
        self._purchase_costs = self._data.get("purchase_costs_pct", {})

        # New top-level fields. Hamburg's legacy file may not have them
        # — fall back to legacy/computed defaults so the legacy fallback
        # path keeps working without manually re-editing the old JSON.
        self.display_name = self._data.get(
            "display_name", self._data.get("city", self.city_slug.capitalize())
        )
        self.bundesland = self._data.get("bundesland", "")
        self._city_default_price_sqm = self._data.get("city_default_price_sqm")
        self._city_default_rent_sqm = self._data.get("city_default_rent_sqm")
        self._city_default_yield = self._data.get("city_default_yield")

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def slug(self) -> str:
        return self.city_slug

    @property
    def districts(self) -> list[str]:
        return list(self._districts.keys())

    @property
    def total_purchase_costs_pct(self) -> float:
        """Total additional purchase costs as percentage."""
        return sum(self._purchase_costs.values())

    @property
    def purchase_costs_breakdown(self) -> dict[str, float]:
        return dict(self._purchase_costs)

    @property
    def city_default_price_sqm(self) -> float:
        """Per-city fallback buy price when district unknown."""
        if self._city_default_price_sqm is not None:
            return float(self._city_default_price_sqm)
        # Legacy Hamburg JSON without the new key: compute the unweighted
        # mean across districts (matches old behaviour for that one file).
        if self._districts:
            prices = [d["avg_price_sqm_buy"] for d in self._districts.values()]
            return sum(prices) / len(prices)
        return 0.0

    @property
    def city_default_rent_sqm(self) -> float:
        if self._city_default_rent_sqm is not None:
            return float(self._city_default_rent_sqm)
        if self._districts:
            rents = [d["avg_rent_sqm_month"] for d in self._districts.values()]
            return sum(rents) / len(rents)
        return 0.0

    @property
    def city_default_yield(self) -> float:
        if self._city_default_yield is not None:
            return float(self._city_default_yield)
        if self._districts:
            yields = [d["avg_gross_yield_pct"] for d in self._districts.values()]
            return sum(yields) / len(yields)
        return 0.0

    # ------------------------------------------------------------------
    # District lookup
    # ------------------------------------------------------------------

    def get_district_data(self, district: str) -> dict | None:
        """Get market data for a district. Tries fuzzy match.

        A bare city name (``"Hamburg"`` for the Hamburg city, ``"Berlin"``
        for Berlin, etc.) is treated as "no specific district" so the
        caller falls back to the city-wide default rather than picking
        the first ``city-Mitte`` district by accident.
        """
        if district in self._districts:
            return self._districts[district]

        district_lower = (district or "").lower().strip()
        if district_lower in ("", self.city_slug, self.display_name.lower()):
            return None  # Use city-wide average via caller

        for name, data in self._districts.items():
            if name.lower() in district_lower or district_lower in name.lower():
                return data

        return None

    def get_avg_price_sqm(self, district: str) -> float:
        """Get average buy price per sqm for district.

        Falls back to **this city's** ``city_default_price_sqm`` when the
        district is unknown — never to Hamburg's number.
        """
        data = self.get_district_data(district)
        if data:
            return data["avg_price_sqm_buy"]
        return self.city_default_price_sqm

    def get_avg_rent_sqm(self, district: str) -> float:
        """Get average monthly rent per sqm for district."""
        data = self.get_district_data(district)
        if data:
            return data["avg_rent_sqm_month"]
        return self.city_default_rent_sqm

    def get_avg_gross_yield(self, district: str) -> float:
        """Get average gross rental yield for district."""
        data = self.get_district_data(district)
        if data:
            return data["avg_gross_yield_pct"]
        return self.city_default_yield

    def get_district_trend(self, district: str) -> str:
        """Get market trend for district: rising, stable, or declining."""
        data = self.get_district_data(district)
        if data:
            return data.get("trend", "stable")
        return "stable"

    def estimate_monthly_rent(self, district: str, size_sqm: float) -> float:
        """Estimate monthly rent for a property."""
        rent_per_sqm = self.get_avg_rent_sqm(district)
        return rent_per_sqm * size_sqm


# Backwards-compatible alias. ``HamburgMarketData()`` (no args) loads the
# Hamburg slug from ``data/cities/hamburg.json``; ``HamburgMarketData(path)``
# (string path) keeps the test fixture form working.
HamburgMarketData = CityMarketData
