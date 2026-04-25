"""Tests for the Telegram digest formatter (scripts/send_telegram_digest.py).

Focused on the time-on-market line introduced alongside `Listing.first_seen_at`.
The rest of `format_deal()` is exercised by an integration / dry-run smoke
test elsewhere; here we just verify the bucket boundaries and that the line
shows up where we expect.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# scripts/ is not a package; resolve it the same way the script itself does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.send_telegram_digest import _time_on_market_line, format_deal
from src.database.models import AnalysisResult, Listing


def _listing(first_seen_at: str) -> Listing:
    return Listing(
        id="x",
        platform="immoscout",
        url="https://example.com/x",
        title="Schöne 3-Zimmer-Wohnung mit Balkon",
        price=320000,
        size_sqm=72,
        rooms=3,
        district="Eimsbüttel",
        first_seen_at=first_seen_at,
    )


def _ago(days: int, *, hours: int = 1) -> str:
    """ISO timestamp `days` days ago (+ a buffer so floor(days) is stable)."""
    return (datetime.now() - timedelta(days=days, hours=hours)).isoformat()


class TestTimeOnMarketLine:
    """Bucket boundaries for the digest's "🕐" line."""

    def test_zero_days_says_neu_im_bot(self):
        # iter-2: "Neu im Bot" replaces "Neu heute" — honest framing,
        # the listing may be months old on the source platform.
        line = _time_on_market_line(_listing(""))  # empty → 0 days
        assert line == "🕐 Neu im Bot"

    def test_zero_days_via_today_first_seen(self):
        # first_seen_at = a few minutes ago is still 0 whole days.
        line = _time_on_market_line(_listing(datetime.now().isoformat()))
        assert line == "🕐 Neu im Bot"

    def test_one_day_uses_singular(self):
        line = _time_on_market_line(_listing(_ago(1)))
        assert line == "🕐 1 Tag im Markt"

    def test_mid_range_uses_plural(self):
        line = _time_on_market_line(_listing(_ago(5)))
        assert line == "🕐 5 Tage im Markt"

    def test_just_under_sixty_no_negotiation_hint(self):
        # 59 days = still inside the "neutral" range; threshold mirrors
        # undervalue_detector's >60 day "seller may accept lower offers"
        # signal so we don't show two different "stale" cutoffs.
        line = _time_on_market_line(_listing(_ago(59)))
        assert line == "🕐 59 Tage im Markt"
        assert "verhandelbar" not in line

    def test_sixty_days_adds_negotiation_hint(self):
        line = _time_on_market_line(_listing(_ago(60)))
        assert "60 Tage im Markt" in line
        assert "möglicherweise verhandelbar" in line

    def test_long_stale_listing_keeps_hint(self):
        line = _time_on_market_line(_listing(_ago(90)))
        assert "90 Tage im Markt" in line
        assert "möglicherweise verhandelbar" in line


class TestFormatDealIncludesTimeOnMarket:
    """The new line must appear in format_deal output, below the Score line."""

    def _analysis(self) -> AnalysisResult:
        return AnalysisResult(
            listing_id="x",
            price_per_sqm=4444,
            price_vs_market_pct=-12,
            deal_score=68,
            gross_rental_yield_pct=4.6,
            monthly_cashflow=-200,
            undervalue_reasons=[],
        )

    def test_zero_day_listing_includes_neu_im_bot(self):
        msg = format_deal(_listing(""), self._analysis())
        assert "🕐 Neu im Bot" in msg

    def test_stale_listing_includes_negotiation_hint(self):
        # 75 days > 60-day stale threshold (matches undervalue_detector).
        msg = format_deal(_listing(_ago(75)), self._analysis())
        assert "möglicherweise verhandelbar" in msg

    def test_time_line_is_below_score_line(self):
        """Design says: new line directly below the Score/Rendite/CF line."""
        msg = format_deal(_listing(_ago(5)), self._analysis())
        score_idx = msg.index("🎯 Score")
        time_idx = msg.index("🕐")
        assert score_idx < time_idx, (
            "🕐 line must appear after the 🎯 Score line"
        )
        # And before the link.
        link_idx = msg.index("🔗")
        assert time_idx < link_idx


# ---------------------------------------------------------------------------
# iter-2 UX additions: Baujahr+Energie line, all-in cash, sub-score sparkline
# ---------------------------------------------------------------------------


def _l(**over) -> Listing:
    base = dict(
        id="x",
        platform="immoscout",
        url="https://example.com/x",
        title="Schöne 3-Zimmer-Wohnung",
        price=320000.0,
        size_sqm=72.0,
        rooms=3.0,
        district="Eimsbüttel",
        first_seen_at="",
    )
    base.update(over)
    return Listing(**base)


def _a(**over) -> AnalysisResult:
    base = dict(
        listing_id="x",
        price_per_sqm=4444.0,
        price_vs_market_pct=-12.0,
        deal_score=73.0,
        gross_rental_yield_pct=4.6,
        monthly_cashflow=-200.0,
        total_purchase_cost=355_000.0,
        undervalue_reasons=[],
    )
    base.update(over)
    return AnalysisResult(**base)


class TestAllInCash:
    """Item 6 — all-in cash figure on the price line."""

    def test_card_shows_all_in_cash_after_price(self):
        msg = format_deal(_l(), _a(total_purchase_cost=355_000.0))
        assert "💵 355,000 € all-in" in msg
        # Order: price formatting comes before the all-in figure.
        price_idx = msg.index("💰 ")
        cash_idx = msg.index("💵")
        assert price_idx < cash_idx


class TestBaujahrEnergieLine:
    """Item 7 — combined Baujahr + Energie line, only when something is set."""

    def test_both_set_renders_combined(self):
        msg = format_deal(_l(year_built=1965, energy_rating="d"), _a())
        # uppercased, both parts present.
        assert "🏗️ BJ 1965 · Energie D" in msg

    def test_only_year_built(self):
        msg = format_deal(_l(year_built=1965, energy_rating=None), _a())
        assert "🏗️ BJ 1965" in msg
        assert "Energie" not in msg

    def test_only_energy_rating(self):
        msg = format_deal(_l(year_built=None, energy_rating="F"), _a())
        assert "🏗️ Energie F" in msg
        assert "BJ " not in msg

    def test_neither_set_omits_line(self):
        msg = format_deal(_l(year_built=None, energy_rating=None), _a())
        assert "🏗️" not in msg
        assert "BJ None" not in msg


class TestSubScoreSparkline:
    """Item 8 — Pr/Y/CF/Loc sparkline, with em-dash fallback for reloaded rows."""

    def test_fresh_analysis_renders_pr_value(self):
        msg = format_deal(
            _l(),
            _a(
                score_price=85.0, score_yield=40.0,
                score_cashflow=60.0, score_location=70.0,
            ),
        )
        # Compact, no spaces in the per-axis tokens.
        assert "(Pr85·Y40·CF60·Loc70)" in msg

    def test_zero_subscores_render_em_dash_fallback(self):
        """Reloaded-from-DB rows have score_price=0.0 → em-dash placeholder."""
        msg = format_deal(_l(), _a())  # all four sub-scores default 0.0
        assert "Pr—" in msg
        assert "Y—" in msg
        # Non-zero deal_score still rendered.
        assert "Score 73/100" in msg
