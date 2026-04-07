"""Detects reasons why a property might be undervalued AND red flags that
explain why it's priced below market.

Two categories of signals:
1. OPPORTUNITY signals - reasons to BUY (motivated seller, below market)
2. RED FLAG signals - reasons it's CHEAP (Erbbaurecht, rented, renovation needed)

An agent should present BOTH so the investor understands the full picture."""

from __future__ import annotations

import re
from datetime import datetime

from src.database.models import Listing, AnalysisResult
from src.scraper.utils import (
    ERBBAURECHT_KEYWORDS, RENTED_KEYWORDS, KAPITALANLAGE_KEYWORDS,
    WBS_KEYWORDS, DACHGESCHOSS_KEYWORDS as ATTIC_KEYWORDS,
    AUSBAU_KEYWORDS,
)
from .market_data import HamburgMarketData


# === OPPORTUNITY KEYWORDS (why seller is motivated) ===

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

FORECLOSURE_KEYWORDS = [
    "zwangsversteigerung", "zwangsverkauf", "insolvenz",
    "bankverkauf", "notverkauf",
]

# === RED FLAG KEYWORDS (why it's cheap) ===
# ERBBAURECHT_KEYWORDS, RENTED_KEYWORDS, KAPITALANLAGE_KEYWORDS,
# WBS_KEYWORDS, ATTIC_KEYWORDS, AUSBAU_KEYWORDS are imported from
# src.scraper.utils (canonical definitions).

# Broader rented keywords for undervalue detection (includes Kapitalanlage signals)
RENTED_KEYWORDS_BROAD = RENTED_KEYWORDS + KAPITALANLAGE_KEYWORDS + [
    "miete ", "rendite", "mieter", "zur kapitalanlage",
]

RENOVATION_KEYWORDS = [
    "renovierungsbedürftig", "sanierungsbedürftig", "renovierung notwendig",
    "sanierung erforderlich", "handwerker", "fixer upper",
    "modernisierungsbedarf", "renovierungsobjekt",
    "modernisierung erforderlich", "instandsetzung",
]

SONDERUMLAGE_KEYWORDS = [
    "sonderumlage", "sonder-umlage", "instandhaltungsrücklage",
    "rücklage", "sanierungsbedarf am gebäude",
    "dachsanierung", "fassadensanierung",
]

HIGH_HAUSGELD_KEYWORDS = [
    "hausgeld", "wohngeld",
]

NOISE_KEYWORDS = [
    "straßenlärm", "bahnlärm", "fluglärm", "lärm",
    "hauptstraße", "durchgangsstraße", "einflugschneise",
    "lärmbelastung", "schallschutz",
]

GROUND_FLOOR_KEYWORDS = [
    "erdgeschoss", "hochparterre", "souterrain",
    "kellergeschoss", "untergeschoss",
]


class UndervalueDetector:
    """Identifies signals that explain a listing's pricing."""

    def __init__(self, market_data: HamburgMarketData):
        self.market = market_data

    def detect(self, listing: Listing, analysis: AnalysisResult) -> list[str]:
        """Run all detection checks. Returns list of signals.

        Each signal is prefixed with a category emoji:
        + = opportunity (positive for investor)
        ! = red flag (explains low price, needs due diligence)
        """
        reasons: list[str] = []

        desc = (listing.description or "").lower()
        title = (listing.title or "").lower()
        text = f"{desc} {title}"

        # === OPPORTUNITY SIGNALS (why this could be a good deal) ===

        # Price below market
        if analysis.price_vs_market_pct < -15:
            discount = abs(analysis.price_vs_market_pct)
            reasons.append(
                f"[+] Price {discount:.0f}% below district average "
                f"(€{analysis.price_per_sqm:.0f}/m² vs €{analysis.district_avg_price_sqm:.0f}/m²)"
            )

        # High rental yield
        district_avg_yield = self.market.get_avg_gross_yield(listing.district or "Hamburg")
        yield_diff = analysis.gross_rental_yield_pct - district_avg_yield
        if yield_diff > 1.0:
            reasons.append(
                f"[+] Rental yield {analysis.gross_rental_yield_pct:.1f}% "
                f"(+{yield_diff:.1f}% above district avg {district_avg_yield:.1f}%)"
            )

        # Positive cashflow
        if analysis.monthly_cashflow > 0:
            reasons.append(
                f"[+] Positive cashflow: €{analysis.monthly_cashflow:.0f}/month after mortgage"
            )

        # Motivated seller signals
        if self._has_keywords(text, ESTATE_KEYWORDS):
            reasons.append("[+] Estate/inheritance sale (Nachlass) - seller may be motivated")

        if self._has_keywords(text, URGENT_SALE_KEYWORDS):
            reasons.append("[+] Urgent sale signals - price likely negotiable")

        if self._has_keywords(text, DIVORCE_KEYWORDS):
            reasons.append("[+] Divorce/separation sale - motivated seller")

        if self._has_keywords(text, FORECLOSURE_KEYWORDS):
            reasons.append("[+] Foreclosure/forced sale - typically 20-40% below market")

        # Rising district
        trend = self.market.get_district_trend(listing.district or "Hamburg")
        if trend == "rising" and analysis.price_vs_market_pct < -10:
            reasons.append(
                f"[+] Below market in rising district ({listing.district}) - appreciation potential"
            )

        # Long time on market
        if listing.listing_date:
            try:
                listed = datetime.fromisoformat(listing.listing_date)
                days_on_market = (datetime.now() - listed).days
                if days_on_market > 60:
                    reasons.append(
                        f"[+] Listed {days_on_market} days - seller may accept lower offers"
                    )
            except ValueError:
                pass

        # Below implied value
        if listing.size_sqm > 0:
            avg_rent = self.market.get_avg_rent_sqm(listing.district or "Hamburg")
            if avg_rent > 0:
                annual_rent = listing.size_sqm * avg_rent * 12
                implied_value = annual_rent / 0.03
                if listing.price < implied_value * 0.85:
                    reasons.append(
                        f"[+] Price below implied value "
                        f"(€{listing.price:,.0f} vs est. €{implied_value:,.0f})"
                    )

        # === RED FLAGS (why it's cheap - needs due diligence) ===

        # Erbbaurecht (leasehold) - biggest price reducer
        if listing.is_erbbaurecht or self._has_keywords(text, ERBBAURECHT_KEYWORDS):
            listing.is_erbbaurecht = True
            reasons.append(
                "[!] ERBBAURECHT (leasehold land) - you don't own the land. "
                "Typically 20-40% cheaper. Check: lease expiry date, annual Erbbauzins, renewal terms"
            )

        # Currently rented (vermietet)
        if listing.is_rented or self._has_keywords(text, RENTED_KEYWORDS_BROAD):
            listing.is_rented = True
            rent_info = ""
            if listing.current_rent_monthly:
                rent_info = f" Current rent: €{listing.current_rent_monthly:,.0f}/mo."
            # Try to extract rent from text
            if not listing.current_rent_monthly:
                rent_match = re.search(
                    r"(?:miete|mieteinnahmen|kaltmiete)[\s:]*(?:ca\.?\s*)?(\d[\d.,]*)\s*(?:€|eur)",
                    text,
                )
                if rent_match:
                    rent_str = rent_match.group(1).replace(".", "").replace(",", ".")
                    try:
                        rent = float(rent_str)
                        if 100 < rent < 10000:
                            listing.current_rent_monthly = rent
                            rent_info = f" Extracted rent: €{rent:,.0f}/mo."
                    except ValueError:
                        pass

            reasons.append(
                f"[!] TENANTED (vermietet) - typically 20-30% discount vs vacant.{rent_info} "
                "Check: Hamburg 10-year Kündigungssperrfrist, rent level vs Mietspiegel"
            )

        # WBS / social housing
        if listing.is_wbs or self._has_keywords(text, WBS_KEYWORDS):
            listing.is_wbs = True
            reasons.append(
                "[!] WBS/SOCIAL HOUSING obligation - rent capped by Sozialbindung. "
                "Restricted tenant pool. Check: binding expiry date, allowed rent level"
            )

        # Renovation needed
        if self._has_keywords(text, RENOVATION_KEYWORDS):
            reasons.append(
                "[!] RENOVATION NEEDED - explains lower price but potential value-add. "
                "Estimate 500-1500 €/m² renovation cost depending on scope"
            )

        # Dachgeschoss (attic) with potential issues
        if listing.is_dachgeschoss or self._has_keywords(text, ATTIC_KEYWORDS):
            listing.is_dachgeschoss = True
            reasons.append(
                "[!] ATTIC APARTMENT (Dachgeschoss) - sloped ceilings reduce usable space. "
                "WoFlV: areas under 1m height don't count, 1-2m count 50%. "
                "Check if m² is Wohnfläche (WoFlV) or gross area (DIN 277)"
            )

        # Ausbau needed (unfinished space)
        if listing.is_ausbau_needed or self._has_keywords(text, AUSBAU_KEYWORDS):
            listing.is_ausbau_needed = True
            reasons.append(
                "[!] EXPANSION/BUILDOUT NEEDED (Ausbaureserve) - listed m² likely includes "
                "unfinished space. Actual livable area may be 30-50% less. "
                "Check: Baugenehmigung obtainable? Cost: 1000-2000 €/m² for attic buildout"
            )

        # Sonderumlage / building maintenance issues
        if listing.sonderumlage or self._has_keywords(text, SONDERUMLAGE_KEYWORDS):
            umlage_info = ""
            if listing.sonderumlage:
                umlage_info = f" Known amount: €{listing.sonderumlage:,.0f}."
            reasons.append(
                f"[!] SONDERUMLAGE risk (special WEG assessment).{umlage_info} "
                "Request last 3 years WEG Protokolle to check planned renovations"
            )

        # High Hausgeld
        if listing.hausgeld and listing.size_sqm > 0:
            hausgeld_per_sqm = listing.hausgeld / listing.size_sqm
            if hausgeld_per_sqm > 4.0:  # > 4€/m² is high
                reasons.append(
                    f"[!] HIGH HAUSGELD: €{listing.hausgeld:,.0f}/mo "
                    f"(€{hausgeld_per_sqm:.1f}/m² - above typical 2.50-3.50€/m²). "
                    "Eats into cashflow. Check what's included and if Sonderumlage is pending"
                )

        # Poor energy rating
        if listing.energy_rating and listing.energy_rating.upper() in ("F", "G", "H"):
            reasons.append(
                f"[!] POOR ENERGY RATING ({listing.energy_rating.upper()}) - "
                "EU requires class E by 2030, class D by 2033. "
                "Mandatory insulation/heating upgrades on ownership change (2-year deadline). "
                "Budget 20,000-60,000€ for energy renovation"
            )

        # Ground floor / Souterrain
        if self._has_keywords(text, GROUND_FLOOR_KEYWORDS):
            if "souterrain" in text or "kellergeschoss" in text:
                reasons.append(
                    "[!] SOUTERRAIN/BASEMENT apartment - below street level, "
                    "limited natural light, humidity risk. Typically 15-25% cheaper"
                )

        # Noise exposure
        if self._has_keywords(text, NOISE_KEYWORDS):
            reasons.append(
                "[!] NOISE EXPOSURE noted in listing - "
                "check traffic, rail, or flight path proximity"
            )

        # Old building without recent renovation
        if listing.year_built and listing.year_built < 1970:
            if not self._has_keywords(text, ["saniert", "modernisiert", "renoviert", "kernsaniert"]):
                reasons.append(
                    f"[!] BUILT {listing.year_built}, no renovation mentioned - "
                    "likely needs plumbing, electrical, and insulation updates. "
                    "Check: Asbest (pre-1993), lead pipes (pre-1973), Einzelöfen"
                )

        # Detect actual rent from description for rented properties
        if listing.is_rented and listing.current_rent_monthly and listing.price > 0:
            actual_yield = (listing.current_rent_monthly * 12) / listing.price * 100
            est_yield = analysis.gross_rental_yield_pct
            if actual_yield > est_yield + 0.5:
                reasons.append(
                    f"[+] Actual rent (€{listing.current_rent_monthly:,.0f}/mo) gives "
                    f"{actual_yield:.1f}% yield - higher than our {est_yield:.1f}% estimate"
                )
            elif actual_yield < est_yield - 1.0:
                reasons.append(
                    f"[!] Actual rent (€{listing.current_rent_monthly:,.0f}/mo) gives only "
                    f"{actual_yield:.1f}% yield - below market rent. "
                    "Old contract or Sozialbindung may limit rent increases"
                )

        return reasons

    @staticmethod
    def _has_keywords(text: str, keywords: list[str]) -> bool:
        return any(kw in text for kw in keywords)
