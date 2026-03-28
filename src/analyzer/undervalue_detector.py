"""Detects reasons why a property might be undervalued."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from src.database.models import Listing, AnalysisResult
from .market_data import HamburgMarketData


# German keywords indicating motivated sellers or distressed sales
ESTATE_KEYWORDS = [
    "nachlass", "erbschaft", "erbe ", "nachlassverkauf", "aus nachlass",
    "erbschaftsverkauf", "auflösung", "haushaltsauflösung",
]

URGENT_SALE_KEYWORDS = [
    "schneller verkauf", "schnellstmöglich", "sofort verfügbar",
    "dringend", "preis verhandelbar", "preis vhb", "vb ",
    "zu verkaufen wegen", "muss verkauft werden",
]

DIVORCE_KEYWORDS = [
    "scheidung", "trennung", "eigentümergemeinschaft auflösung",
]

RENOVATION_KEYWORDS = [
    "renovierungsbedürftig", "sanierungsbedürftig", "renovierung notwendig",
    "sanierung erforderlich", "handwerker", "fixer upper",
    "modernisierungsbedarf", "renovierungsobjekt",
]

FORECLOSURE_KEYWORDS = [
    "zwangsversteigerung", "zwangsverkauf", "insolvenz",
    "bankverkauf", "notverkauf",
]


class UndervalueDetector:
    """Identifies signals that a listing may be undervalued."""

    def __init__(self, market_data: HamburgMarketData):
        self.market = market_data

    def detect(self, listing: Listing, analysis: AnalysisResult) -> list[str]:
        """Run all detection checks and return list of undervalue reasons."""
        reasons: list[str] = []

        # Price below market
        if analysis.price_vs_market_pct < -15:
            discount = abs(analysis.price_vs_market_pct)
            reasons.append(
                f"Price {discount:.0f}% below district average "
                f"(€{analysis.price_per_sqm:.0f}/m² vs €{analysis.district_avg_price_sqm:.0f}/m²)"
            )

        # High rental yield
        district_avg_yield = self.market.get_avg_gross_yield(listing.district or "Hamburg")
        yield_diff = analysis.gross_rental_yield_pct - district_avg_yield
        if yield_diff > 1.0:
            reasons.append(
                f"Rental yield {analysis.gross_rental_yield_pct:.1f}% "
                f"(+{yield_diff:.1f}% above district avg {district_avg_yield:.1f}%)"
            )

        # Positive cashflow
        if analysis.monthly_cashflow > 0:
            reasons.append(
                f"Positive cashflow: €{analysis.monthly_cashflow:.0f}/month after mortgage"
            )

        # Text-based signals from description
        desc = (listing.description or "").lower()
        title = (listing.title or "").lower()
        text = f"{desc} {title}"

        if self._has_keywords(text, ESTATE_KEYWORDS):
            reasons.append("Estate/inheritance sale (Nachlass) - seller may be motivated")

        if self._has_keywords(text, URGENT_SALE_KEYWORDS):
            reasons.append("Urgent sale signals - price may be negotiable")

        if self._has_keywords(text, DIVORCE_KEYWORDS):
            reasons.append("Divorce/separation sale - motivated seller likely")

        if self._has_keywords(text, RENOVATION_KEYWORDS):
            reasons.append("Renovation needed - potential value-add after improvements")

        if self._has_keywords(text, FORECLOSURE_KEYWORDS):
            reasons.append("Foreclosure/forced sale - typically below market value")

        # Long time on market
        if listing.listing_date:
            try:
                listed = datetime.fromisoformat(listing.listing_date)
                days_on_market = (datetime.now() - listed).days
                if days_on_market > 60:
                    reasons.append(
                        f"Listed for {days_on_market} days - seller may accept lower offers"
                    )
            except ValueError:
                pass

        # Large for price bracket
        if listing.size_sqm > 0:
            avg_rent = self.market.get_avg_rent_sqm(listing.district or "Hamburg")
            if avg_rent > 0:
                annual_rent = listing.size_sqm * avg_rent * 12
                implied_value = annual_rent / 0.03  # 3% cap rate = fair value estimate
                if listing.price < implied_value * 0.85:
                    reasons.append(
                        f"Price significantly below implied value "
                        f"(€{listing.price:,.0f} vs est. €{implied_value:,.0f})"
                    )

        # Rising district trend
        trend = self.market.get_district_trend(listing.district or "Hamburg")
        if trend == "rising" and analysis.price_vs_market_pct < -10:
            reasons.append(
                f"Below market in rising district ({listing.district}) - appreciation potential"
            )

        return reasons

    @staticmethod
    def _has_keywords(text: str, keywords: list[str]) -> bool:
        return any(kw in text for kw in keywords)
