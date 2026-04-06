"""Hamburg real estate market reference data."""

from __future__ import annotations

import json
from pathlib import Path


class HamburgMarketData:
    """Provides Hamburg district-level market averages for comparison."""

    def __init__(self, data_path: str = "data/hamburg_market_data.json"):
        with open(data_path) as f:
            self._data = json.load(f)
        self._districts = self._data["districts"]
        self._purchase_costs = self._data.get("purchase_costs_pct", {})

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

    def get_district_data(self, district: str) -> dict | None:
        """Get market data for a district. Tries fuzzy match."""
        if district in self._districts:
            return self._districts[district]

        # Fuzzy match - but don't match bare "Hamburg" to "Hamburg-Mitte"
        district_lower = district.lower().strip()
        if district_lower in ("hamburg", ""):
            return None  # Use city-wide average via caller

        for name, data in self._districts.items():
            if name.lower() in district_lower or district_lower in name.lower():
                return data

        return None

    def get_avg_price_sqm(self, district: str) -> float:
        """Get average buy price per sqm for district."""
        data = self.get_district_data(district)
        if data:
            return data["avg_price_sqm_buy"]
        # City-wide average as fallback
        all_prices = [d["avg_price_sqm_buy"] for d in self._districts.values()]
        return sum(all_prices) / len(all_prices)

    def get_avg_rent_sqm(self, district: str) -> float:
        """Get average monthly rent per sqm for district."""
        data = self.get_district_data(district)
        if data:
            return data["avg_rent_sqm_month"]
        all_rents = [d["avg_rent_sqm_month"] for d in self._districts.values()]
        return sum(all_rents) / len(all_rents)

    def get_avg_gross_yield(self, district: str) -> float:
        """Get average gross rental yield for district."""
        data = self.get_district_data(district)
        if data:
            return data["avg_gross_yield_pct"]
        all_yields = [d["avg_gross_yield_pct"] for d in self._districts.values()]
        return sum(all_yields) / len(all_yields)

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
