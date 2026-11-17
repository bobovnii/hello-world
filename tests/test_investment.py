"""Investor view: cost model, tax, 10-year projection, rendering."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analyzer.investment import (
    InvestmentAssumptions,
    afa_rate_pct,
    annual_operating_costs,
    annuity_monthly,
    irr,
)
from src.analyzer.market_data import CityMarketData
from src.analyzer.scorer import DealScorer
from src.database.models import Listing, UserCriteria
from src.report.html import build_digest_html


def _listing(**kw) -> Listing:
    base = dict(
        id="t1", platform="immoscout", url="https://example.org/x", title="2 Zi Altona",
        price=249_000, size_sqm=60, rooms=2, district="Altona", zip_code="22761",
        scraped_at=datetime.now().isoformat(),
    )
    base.update(kw)
    return Listing(**base)


CRIT = UserCriteria(50_000, 250_000, 30, min_rooms=2, property_types=["apartment"])


def _analyse(listing, city="hamburg", assumptions=None):
    analysis, _ = DealScorer(CityMarketData(city), assumptions).score_listing(listing, CRIT)
    return analysis


class TestBuildingBlocks:
    def test_irr_known_value(self):
        # -100 now, +110 in a year → 10 %
        assert irr([-100, 110]) == pytest.approx(0.10, abs=1e-6)
        assert irr([-1000, 100, 100, 1100]) == pytest.approx(0.10, abs=1e-6)
        assert irr([100, 100]) is None

    def test_annuity_matches_formula(self):
        assert annuity_monthly(200_000, 3.5, 25) == pytest.approx(1001.25, abs=0.5)
        assert annuity_monthly(0, 3.5, 25) == 0

    def test_afa_rates(self):
        assert afa_rate_pct(1900) == 2.5
        assert afa_rate_pct(1965) == 2.0
        assert afa_rate_pct(2024) == 3.0
        assert afa_rate_pct(None) == 2.0

    def test_only_nonrecoverable_hausgeld_is_a_cost(self):
        a = InvestmentAssumptions()
        known, est = annual_operating_costs(_listing(hausgeld=300), 10_000, a)
        assert not est
        # 35 % of 3600 + 60 m² × 8 + 3 % vacancy
        assert known == pytest.approx(1260 + 480 + 300)
        unknown, est2 = annual_operating_costs(_listing(), 10_000, a)
        assert est2 and unknown == pytest.approx(60 * 1.3 * 12 + 480 + 300)

    def test_from_config_rejects_typos_and_merges_dicts(self):
        with pytest.raises(ValueError):
            InvestmentAssumptions.from_config({"tax_rate": 30})
        a = InvestmentAssumptions.from_config({"tax_rate_pct": 30, "rent_growth_pct": {"rising": 3.0}})
        assert a.tax_rate_pct == 30.0
        assert a.rent_growth_pct == {"rising": 3.0, "stable": 2.0, "declining": 1.0}


class TestInvestmentView:
    def test_core_numbers_consistent(self):
        a = _analyse(_listing())
        v = a.investment
        assert v.rent_month == pytest.approx(14.5 * 60)
        assert v.kaufpreisfaktor == pytest.approx(249_000 / (14.5 * 60 * 12))
        assert v.district_factor == pytest.approx(100 / 3.0)
        # Year-1 pre-tax CF equals NOI minus debt service
        assert v.cf_pre_tax_month * 12 == pytest.approx(v.noi_year1 - a.mortgage_monthly * 12, abs=1)
        assert a.monthly_cashflow == pytest.approx(v.cf_pre_tax_month, abs=1)
        # Loss year → refund → after-tax CF better than pre-tax
        assert v.tax_month < 0 and v.cf_after_tax_month > v.cf_pre_tax_month
        assert v.makler == pytest.approx(249_000 * 0.0357)

    def test_scenarios_are_ordered(self):
        v = _analyse(_listing()).investment
        assert v.bear.irr_after_tax_pct < v.base.irr_after_tax_pct < v.bull.irr_after_tax_pct
        assert len(v.base.rows) == 10
        assert v.base.rows[-1].loan_balance < v.loan

    def test_sitting_tenant_rent_capped_by_kappungsgrenze(self):
        v = _analyse(_listing(is_rented=True, current_rent_monthly=650)).investment
        assert v.rent_source.startswith("Ist-Miete")
        assert v.rent_potential_pct == pytest.approx((870 / 650 - 1) * 100)
        rents = [r.rent / 12 / 0.97 for r in v.base.rows]
        assert rents[0] == pytest.approx(650)
        assert rents[3] == pytest.approx(650 * 1.15)        # first step-up in year 4
        assert rents[2] == pytest.approx(650)               # nothing before

    def test_erbbaurecht_charges_ground_rent_and_lowers_return(self):
        free = _analyse(_listing()).investment
        lease = _analyse(_listing(is_erbbaurecht=True)).investment
        # 3.5 % × 25 % land share × 5800 €/m² × 60 m²
        assert lease.erbbauzins_year == pytest.approx(0.035 * 0.25 * 5800 * 60)
        assert lease.opex_year1 == pytest.approx(free.opex_year1 + lease.erbbauzins_year)
        assert lease.base.irr_after_tax_pct < free.base.irr_after_tax_pct

    def test_rented_without_known_rent_is_flagged(self):
        v = _analyse(_listing(is_rented=True)).investment
        assert v.tenanted_rent_unknown
        assert not _analyse(_listing()).investment.tenanted_rent_unknown

    def test_commission_free_has_no_makler(self):
        v = _analyse(_listing(platform="ohne-makler")).investment
        assert v.makler == 0

    def test_to_dict_does_not_persist_investment(self):
        a = _analyse(_listing())
        d = a.to_dict()
        assert "investment" not in d
        assert a.investment is not None


class TestRendering:
    def test_card_and_html_show_investor_block(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from send_telegram_digest import format_deal

        listing = _listing()
        a = _analyse(listing)
        msg = format_deal(listing, a, CityMarketData("hamburg"))
        assert "🏦 Miete ~870 €/Mo" in msg
        assert "Faktor 23.9 (Ø 33.3)" in msg
        assert "nach Steuer" in msg
        assert "🔟 10 J vermieten: IRR" in msg

        html_out = build_digest_html("t", "t", [(listing, a)])
        for s in ("Investoren-Kennzahlen", "Kaufpreisfaktor", "10-Jahres-Szenario",
                  "Pessimistisch", "Jahr für Jahr", "Spekulationsfrist"):
            assert s in html_out, s
