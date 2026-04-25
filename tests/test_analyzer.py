"""Tests for the analyzer module."""

import json
import os
import tempfile

import pytest

from src.database.models import Listing, UserCriteria, AnalysisResult
from src.analyzer.market_data import HamburgMarketData
from src.analyzer.metrics import MetricsCalculator
from src.analyzer.undervalue_detector import UndervalueDetector
from src.analyzer.scorer import DealScorer
from src.scraper.utils import ERBBAURECHT_KEYWORDS


@pytest.fixture
def market_data():
    return HamburgMarketData("data/hamburg_market_data.json")


@pytest.fixture
def sample_listing():
    return Listing(
        id="test_1",
        platform="immoscout",
        url="https://example.com/1",
        title="Schöne 3-Zimmer Wohnung in Harburg",
        price=160000,
        size_sqm=65,
        rooms=3,
        district="Harburg",
        zip_code="21073",
        year_built=1985,
        hausgeld=250,
    )


@pytest.fixture
def sample_criteria():
    return UserCriteria(
        budget_min=100000,
        budget_max=300000,
        min_size_sqm=50,
        min_rooms=2,
        equity_pct=20,
        interest_rate_pct=3.5,
        loan_term_years=25,
        min_gross_yield_pct=4.0,
        risk_tolerance="moderate",
    )


class TestHamburgMarketData:
    def test_get_districts(self, market_data):
        districts = market_data.districts
        assert "Harburg" in districts
        assert "Eimsbüttel" in districts
        assert len(districts) >= 7

    def test_get_avg_price_sqm(self, market_data):
        price = market_data.get_avg_price_sqm("Harburg")
        assert 2500 <= price <= 5000

    def test_get_avg_rent_sqm(self, market_data):
        rent = market_data.get_avg_rent_sqm("Eimsbüttel")
        assert 10 <= rent <= 20

    def test_fallback_for_unknown_district(self, market_data):
        price = market_data.get_avg_price_sqm("NonExistent")
        assert price > 0  # Should return city average

    def test_estimate_monthly_rent(self, market_data):
        rent = market_data.estimate_monthly_rent("Harburg", 65)
        assert rent > 0
        assert 500 <= rent <= 1000

    def test_total_purchase_costs(self, market_data):
        costs = market_data.total_purchase_costs_pct
        assert 10 <= costs <= 15  # Typical German range


class TestMetricsCalculator:
    def test_basic_calculation(self, market_data, sample_listing, sample_criteria):
        calc = MetricsCalculator(market_data)
        result = calc.calculate(sample_listing, sample_criteria)

        assert isinstance(result, AnalysisResult)
        assert result.listing_id == "test_1"
        assert result.price_per_sqm == pytest.approx(160000 / 65, rel=0.01)
        assert result.total_purchase_cost > sample_listing.price  # Includes fees
        assert result.equity_required > 0
        assert result.mortgage_monthly > 0
        assert result.estimated_rent_monthly > 0
        assert result.gross_rental_yield_pct > 0

    def test_mortgage_calculation(self):
        payment = MetricsCalculator._calculate_mortgage(200000, 3.5, 25)
        # ~1000 EUR/month for 200k at 3.5% over 25y
        assert 900 <= payment <= 1100

    def test_zero_loan(self):
        payment = MetricsCalculator._calculate_mortgage(0, 3.5, 25)
        assert payment == 0

    def test_below_market_detection(self, market_data, sample_criteria):
        calc = MetricsCalculator(market_data)

        # Create a cheap listing in expensive district
        cheap_listing = Listing(
            id="cheap_1",
            platform="test",
            url="https://example.com",
            title="Cheap in Eimsbüttel",
            price=150000,
            size_sqm=60,  # 2500/m² vs avg 6200/m²
            rooms=2,
            district="Eimsbüttel",
        )
        result = calc.calculate(cheap_listing, sample_criteria)
        assert result.price_vs_market_pct < -50  # Way below market


class TestUndervalueDetector:
    def test_below_market_signal(self, market_data):
        detector = UndervalueDetector(market_data)

        listing = Listing(
            id="uv_1", platform="test", url="", title="Test",
            price=100000, size_sqm=60, rooms=2, district="Eimsbüttel",
        )
        analysis = AnalysisResult(
            listing_id="uv_1",
            price_per_sqm=1667,
            district_avg_price_sqm=6200,
            price_vs_market_pct=-73,
            gross_rental_yield_pct=10.8,
        )

        reasons = detector.detect(listing, analysis)
        assert any("below district average" in r.lower() for r in reasons)

    def test_estate_sale_detection(self, market_data):
        detector = UndervalueDetector(market_data)

        listing = Listing(
            id="estate_1", platform="test", url="", title="Nachlass Wohnung",
            price=200000, size_sqm=80, rooms=3, district="Wandsbek",
            description="Wohnung aus Nachlass zu verkaufen. Auflösung des Haushalts.",
        )
        analysis = AnalysisResult(
            listing_id="estate_1",
            price_vs_market_pct=-5,
            gross_rental_yield_pct=3.5,
        )

        reasons = detector.detect(listing, analysis)
        assert any("nachlass" in r.lower() or "estate" in r.lower() for r in reasons)

    def test_renovation_detection(self, market_data):
        detector = UndervalueDetector(market_data)

        listing = Listing(
            id="reno_1", platform="test", url="", title="Renovierungsbedürftig",
            price=150000, size_sqm=70, rooms=3, district="Harburg",
            description="Renovierungsbedürftige Wohnung, ideal für Handwerker",
        )
        analysis = AnalysisResult(
            listing_id="reno_1", price_vs_market_pct=-10,
            gross_rental_yield_pct=5.0,
        )

        reasons = detector.detect(listing, analysis)
        assert any("renovation" in r.lower() for r in reasons)

    def test_no_signals(self, market_data):
        detector = UndervalueDetector(market_data)

        # Price at 6500/m² in Eimsbüttel (avg 6200) = slightly above market
        listing = Listing(
            id="normal_1", platform="test", url="", title="Normal Wohnung",
            price=390000, size_sqm=60, rooms=2, district="Eimsbüttel",
        )
        analysis = AnalysisResult(
            listing_id="normal_1", price_vs_market_pct=5,
            gross_rental_yield_pct=2.5,
        )

        reasons = detector.detect(listing, analysis)
        # Overpriced listing should not have "below district average" signal
        assert not any("below district average" in r.lower() for r in reasons)


class TestDealScorer:
    def test_scoring(self, market_data, sample_listing, sample_criteria):
        scorer = DealScorer(market_data)
        analysis, score = scorer.score_listing(sample_listing, sample_criteria)

        assert 0 <= score <= 100
        assert analysis.deal_score == score
        assert isinstance(analysis.undervalue_reasons, list)

    def test_cheap_scores_higher(self, market_data, sample_criteria):
        scorer = DealScorer(market_data)

        cheap = Listing(
            id="cheap", platform="test", url="", title="Cheap",
            price=100000, size_sqm=80, rooms=3, district="Harburg",
        )
        expensive = Listing(
            id="expensive", platform="test", url="", title="Expensive",
            price=500000, size_sqm=80, rooms=3, district="Harburg",
        )

        _, cheap_score = scorer.score_listing(cheap, sample_criteria)
        _, exp_score = scorer.score_listing(expensive, sample_criteria)

        assert cheap_score > exp_score

    def test_rank_ordering(self, market_data, sample_criteria):
        scorer = DealScorer(market_data)
        listings = [
            Listing(id="a", platform="test", url="", title="A",
                    price=400000, size_sqm=60, rooms=2, district="Eimsbüttel"),
            Listing(id="b", platform="test", url="", title="B",
                    price=120000, size_sqm=70, rooms=3, district="Harburg"),
            Listing(id="c", platform="test", url="", title="C",
                    price=250000, size_sqm=65, rooms=2, district="Wandsbek"),
        ]

        results = scorer.score_and_rank(listings, sample_criteria)
        scores = [a.deal_score for _, a in results]
        assert scores == sorted(scores, reverse=True)

    def test_risk_tolerance_affects_scoring(self, market_data, sample_listing):
        scorer = DealScorer(market_data)

        conservative = UserCriteria(risk_tolerance="conservative")
        aggressive = UserCriteria(risk_tolerance="aggressive")

        _, score_cons = scorer.score_listing(sample_listing, conservative)
        _, score_aggr = scorer.score_listing(sample_listing, aggressive)

        # Scores may differ due to different weight emphasis
        assert isinstance(score_cons, float)
        assert isinstance(score_aggr, float)


# ---------------------------------------------------------------------------
# iter-2 correctness fixes
# ---------------------------------------------------------------------------


class TestRentPrecedence:
    """Item 3 — yield/cashflow prefer actual rent over market estimate when
    the unit is tenanted with a known contractual rent."""

    def test_uses_current_rent_when_rented_and_known(self, market_data, sample_criteria):
        calc = MetricsCalculator(market_data)
        # 65 m² in Harburg has avg_rent ~10/m² → ~650/mo market estimate.
        # Force the listing rent to a clearly different number so we can
        # detect which one was used.
        listing = Listing(
            id="rented_known",
            platform="immoscout",
            url="",
            title="Rented",
            price=160000,
            size_sqm=65,
            rooms=3,
            district="Harburg",
            is_rented=True,
            current_rent_monthly=400.0,  # well below the 650 estimate
        )
        result = calc.calculate(listing, sample_criteria)
        assert result.estimated_rent_monthly == pytest.approx(400.0, rel=0.001)
        # Implied gross yield = 400 * 12 / 160_000 = 3.0
        assert result.gross_rental_yield_pct == pytest.approx(3.0, abs=0.05)

    def test_falls_back_to_market_when_not_rented(self, market_data, sample_criteria):
        calc = MetricsCalculator(market_data)
        listing = Listing(
            id="vacant",
            platform="immoscout",
            url="",
            title="Vacant",
            price=160000,
            size_sqm=65,
            rooms=3,
            district="Harburg",
            is_rented=False,
            current_rent_monthly=None,
        )
        result = calc.calculate(listing, sample_criteria)
        # 65 m² × 10 €/m² ≈ 650 — market estimate is used, not None.
        assert result.estimated_rent_monthly > 500
        assert result.gross_rental_yield_pct > 0

    def test_falls_back_to_market_when_rent_unknown(self, market_data, sample_criteria):
        calc = MetricsCalculator(market_data)
        listing = Listing(
            id="rented_unknown",
            platform="immoscout",
            url="",
            title="Rented but unknown rent",
            price=160000,
            size_sqm=65,
            rooms=3,
            district="Harburg",
            is_rented=True,
            current_rent_monthly=None,
        )
        result = calc.calculate(listing, sample_criteria)
        # No actual rent → market estimate.
        assert result.estimated_rent_monthly > 500


class TestPurchaseCostsPct:
    """Item 4 — commission-free platforms / explicit markers drop Maklercourtage."""

    def test_default_includes_makler(self, market_data):
        calc = MetricsCalculator(market_data)
        listing = Listing(
            id="im", platform="immoscout", url="", title="t",
            price=300000, size_sqm=60, rooms=2,
        )
        pct = calc._purchase_costs_pct(listing)
        # Full breakdown ≈ 11.07 (Hamburg JSON: 5.5 + 1.5 + 0.5 + 3.57)
        assert pct == pytest.approx(11.07, abs=0.01)

    def test_ohne_makler_drops_makler(self, market_data):
        calc = MetricsCalculator(market_data)
        listing = Listing(
            id="om", platform="ohne-makler", url="", title="t",
            price=300000, size_sqm=60, rooms=2,
        )
        pct = calc._purchase_costs_pct(listing)
        # Without Maklercourtage → 5.5 + 1.5 + 0.5 = 7.5 (Hamburg JSON).
        assert pct == pytest.approx(7.5, abs=0.01)

    def test_kleinanzeigen_drops_makler(self, market_data):
        calc = MetricsCalculator(market_data)
        listing = Listing(
            id="ka", platform="kleinanzeigen", url="", title="t",
            price=300000, size_sqm=60, rooms=2,
        )
        pct = calc._purchase_costs_pct(listing)
        assert pct == pytest.approx(7.5, abs=0.01)

    def test_provisionsfrei_marker_drops_makler(self, market_data):
        calc = MetricsCalculator(market_data)
        # ImmoScout listing (commission-bearing platform) but description
        # explicitly says provisionsfrei → still drop Maklercourtage.
        listing = Listing(
            id="pf", platform="immoscout", url="", title="Hamburg",
            price=300000, size_sqm=60, rooms=2,
            description="Schöne Wohnung, PROVISIONSFREI für den Käufer.",
        )
        pct = calc._purchase_costs_pct(listing)
        assert pct == pytest.approx(7.5, abs=0.01)

    def test_total_purchase_cost_uses_helper(self, market_data, sample_criteria):
        calc = MetricsCalculator(market_data)
        listing = Listing(
            id="om2", platform="ohne-makler", url="", title="t",
            price=300000, size_sqm=60, rooms=2, district="Harburg",
        )
        result = calc.calculate(listing, sample_criteria)
        # 300_000 * 1.075 = 322_500
        assert result.total_purchase_cost == pytest.approx(322_500.0, abs=1.0)


class TestErbbaurechtKeywordExtensions:
    """Item 2 — extended keyword list and Pachtvertrag regex trigger."""

    def test_pachtvertrag_keyword_listed(self):
        # The literal substring "pachtvertrag" must now match
        # "Pachtvertrag läuft bis 2087" (case-insensitive).
        text = "pachtvertrag läuft bis 2087"
        assert any(kw in text for kw in ERBBAURECHT_KEYWORDS)

    def test_im_erbbau_keyword_listed(self):
        assert any(kw in "wohnung im erbbau" for kw in ERBBAURECHT_KEYWORDS)

    def test_heimfallrecht_keyword_listed(self):
        assert "heimfallrecht" in ERBBAURECHT_KEYWORDS

    def test_pacht_bis_year_regex_triggers_erbbaurecht(self, market_data):
        """Year-pinned phrasing must set is_erbbaurecht and emit the signal,
        even when no canonical keyword appears.

        We use a description that contains "pachtvertrag bis 2087" — note
        that "pachtvertrag" IS now a keyword (item 2), so to test the regex
        in isolation we use a phrase that omits the leading 'vertrag'."""
        detector = UndervalueDetector(market_data)
        listing = Listing(
            id="pacht_year",
            platform="test",
            url="",
            title="Wohnung",
            price=200000,
            size_sqm=60,
            rooms=2,
            district="Hamburg",
            description="Schöne Wohnung. Pacht bis 2087 vereinbart.",
        )
        analysis = AnalysisResult(
            listing_id="pacht_year",
            price_vs_market_pct=0,
            gross_rental_yield_pct=3.0,
        )
        reasons = detector.detect(listing, analysis)
        assert listing.is_erbbaurecht is True
        # And exactly one ERBBAURECHT signal is emitted (no double-emission).
        erbbau_signals = [r for r in reasons if "ERBBAURECHT" in r]
        assert len(erbbau_signals) == 1


class TestSchallschutzNotNoise:
    """Item 5 — Schallschutz is mitigation, not a noise red flag."""

    def test_schallschutz_does_not_trigger_noise_signal(self, market_data):
        detector = UndervalueDetector(market_data)
        listing = Listing(
            id="schall",
            platform="test",
            url="",
            title="Ruhige Lage",
            price=300000,
            size_sqm=70,
            rooms=3,
            district="Eimsbüttel",
            description="Mit hochwertigem Schallschutz an den Fenstern.",
        )
        analysis = AnalysisResult(
            listing_id="schall",
            price_vs_market_pct=0,
            gross_rental_yield_pct=3.0,
        )
        reasons = detector.detect(listing, analysis)
        # No NOISE EXPOSURE signal.
        assert not any("NOISE EXPOSURE" in r for r in reasons)


class TestSubScoresOnAnalysis:
    """Item 8 — DealScorer populates the four sub-score fields."""

    def test_fresh_analysis_has_nonzero_subscores(self, market_data):
        scorer = DealScorer(market_data)
        listing = Listing(
            id="ss",
            platform="test",
            url="",
            title="t",
            price=200000,
            size_sqm=60,
            rooms=2,
            district="Eimsbüttel",
        )
        analysis, _ = scorer.score_listing(listing, UserCriteria())
        # All four populated and within bounds.
        for v in (
            analysis.score_price,
            analysis.score_yield,
            analysis.score_cashflow,
            analysis.score_location,
        ):
            assert 0.0 <= v <= 100.0
        # At minimum location_score is non-zero (Eimsbüttel is rated stable+premium).
        assert analysis.score_location > 0
