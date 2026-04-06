"""Deal scoring algorithm - ranks listings by investment potential."""

from __future__ import annotations

from src.database.models import Listing, UserCriteria, AnalysisResult
from .market_data import HamburgMarketData
from .metrics import MetricsCalculator
from .undervalue_detector import UndervalueDetector


# Scoring weight presets based on risk tolerance
WEIGHT_PRESETS = {
    "conservative": {
        "price_below_market": 0.20,
        "rental_yield": 0.15,
        "cashflow": 0.35,  # Prioritize stable cashflow
        "undervalue_signals": 0.10,
        "location_trend": 0.20,  # Prefer stable locations
    },
    "moderate": {
        "price_below_market": 0.25,
        "rental_yield": 0.25,
        "cashflow": 0.20,
        "undervalue_signals": 0.15,
        "location_trend": 0.15,
    },
    "aggressive": {
        "price_below_market": 0.30,  # Hunt for deep discounts
        "rental_yield": 0.20,
        "cashflow": 0.10,
        "undervalue_signals": 0.25,  # Value add / distressed
        "location_trend": 0.15,
    },
}


class DealScorer:
    """Scores and ranks real estate deals."""

    def __init__(self, market_data: HamburgMarketData):
        self.market = market_data
        self.metrics_calc = MetricsCalculator(market_data)
        self.undervalue_detector = UndervalueDetector(market_data)

    def score_listing(
        self, listing: Listing, criteria: UserCriteria
    ) -> tuple[AnalysisResult, float]:
        """Score a single listing. Returns (analysis, score)."""
        # Calculate metrics
        analysis = self.metrics_calc.calculate(listing, criteria)

        # Detect undervalue reasons
        reasons = self.undervalue_detector.detect(listing, analysis)
        analysis.undervalue_reasons = reasons

        # Get weights based on risk tolerance
        weights = WEIGHT_PRESETS.get(criteria.risk_tolerance, WEIGHT_PRESETS["moderate"])

        # Calculate sub-scores (each 0-100)
        price_score = self._score_price(analysis)
        yield_score = self._score_yield(analysis, listing.district)
        cashflow_score = self._score_cashflow(analysis)
        undervalue_score = self._score_undervalue(reasons)
        location_score = self._score_location(listing.district)

        # Weighted composite
        total = (
            price_score * weights["price_below_market"]
            + yield_score * weights["rental_yield"]
            + cashflow_score * weights["cashflow"]
            + undervalue_score * weights["undervalue_signals"]
            + location_score * weights["location_trend"]
        )

        # Apply red flag penalties (reduce score for each major risk factor)
        red_flags = [r for r in reasons if r.startswith("[!]")]
        penalty = 0
        for flag in red_flags:
            flag_lower = flag.lower()
            if "erbbaurecht" in flag_lower:
                penalty += 12  # Major: don't own land
            elif "ausbau" in flag_lower or "rohbau" in flag_lower:
                penalty += 15  # Major: uninhabitable
            elif "energy rating" in flag_lower:
                penalty += 8   # Mandatory renovation by 2030
            elif "tenanted" in flag_lower or "vermietet" in flag_lower:
                penalty += 5   # Moderate: limits use, but has income
            elif "wbs" in flag_lower or "sozialbindung" in flag_lower:
                penalty += 10  # Rent capped
            elif "sonderumlage" in flag_lower:
                penalty += 7
            elif "renovation" in flag_lower or "sanierung" in flag_lower:
                penalty += 6
            elif "built" in flag_lower and "no renovation" in flag_lower:
                penalty += 5
            elif "souterrain" in flag_lower:
                penalty += 8
            elif "attic" in flag_lower or "dachgeschoss" in flag_lower:
                penalty += 4
            else:
                penalty += 3  # Generic minor flag

        total = max(0, total - penalty)
        analysis.deal_score = round(min(100, total), 1)
        return analysis, analysis.deal_score

    def score_and_rank(
        self, listings: list[Listing], criteria: UserCriteria
    ) -> list[tuple[Listing, AnalysisResult]]:
        """Score all listings and return sorted by score descending."""
        scored: list[tuple[Listing, AnalysisResult]] = []

        for listing in listings:
            analysis, score = self.score_listing(listing, criteria)
            scored.append((listing, analysis))

        scored.sort(key=lambda x: x[1].deal_score, reverse=True)
        return scored

    def _score_price(self, analysis: AnalysisResult) -> float:
        """Score based on how far below market price is. 0-100."""
        discount = -analysis.price_vs_market_pct  # positive = below market
        if discount <= 0:
            return max(0, 20 + discount)  # Still some points if near market
        if discount >= 30:
            return 100
        return 20 + (discount / 30) * 80

    def _score_yield(self, analysis: AnalysisResult, district: str) -> float:
        """Score based on gross rental yield vs district average."""
        yield_pct = analysis.gross_rental_yield_pct
        avg_yield = self.market.get_avg_gross_yield(district or "Hamburg")

        if yield_pct <= 0:
            return 0
        if yield_pct >= 7:
            return 100

        # Base score from absolute yield
        base = min(80, (yield_pct / 7) * 80)

        # Bonus for beating district average
        if yield_pct > avg_yield:
            bonus = min(20, (yield_pct - avg_yield) * 10)
            base += bonus

        return min(100, base)

    def _score_cashflow(self, analysis: AnalysisResult) -> float:
        """Score based on monthly cashflow."""
        cf = analysis.monthly_cashflow
        if cf <= -500:
            return 0
        if cf <= 0:
            return 20 + (cf + 500) / 500 * 20  # 0-20 for -500 to 0
        if cf >= 500:
            return 100
        return 40 + (cf / 500) * 60  # 40-100 for 0-500

    def _score_undervalue(self, reasons: list[str]) -> float:
        """Score based on number of OPPORTUNITY signals only (not red flags)."""
        opportunities = [r for r in reasons if r.startswith("[+]")]
        if not opportunities:
            return 10  # Small base score
        score_per_reason = 20
        return min(100, 10 + len(opportunities) * score_per_reason)

    def _score_location(self, district: str) -> float:
        """Score based on district trend and desirability."""
        trend = self.market.get_district_trend(district or "Hamburg")
        trend_scores = {"rising": 80, "stable": 60, "declining": 30}
        base = trend_scores.get(trend, 50)

        # Premium districts get a bonus
        premium = {"Eimsbüttel", "Altona", "Hamburg-Nord"}
        if district in premium:
            base = min(100, base + 15)

        return base
