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

    def test_zero_days_says_neu_heute(self):
        line = _time_on_market_line(_listing(""))  # empty → 0 days
        assert line == "🕐 Neu heute"

    def test_zero_days_via_today_first_seen(self):
        # first_seen_at = a few minutes ago is still 0 whole days.
        line = _time_on_market_line(_listing(datetime.now().isoformat()))
        assert line == "🕐 Neu heute"

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

    def test_zero_day_listing_includes_neu_heute(self):
        msg = format_deal(_listing(""), self._analysis())
        assert "🕐 Neu heute" in msg

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
