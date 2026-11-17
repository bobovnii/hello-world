"""Tests for the per-search HTML detail report (`src/report/html.py`).

The HTML document is sent as a Telegram document attachment after each
search's deal cards. These tests cover the safety contract (escaping,
URL validation, length cap) and the content contract (which fields and
section labels must appear).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database.models import AnalysisResult, Listing
from src.report.html import build_digest_html


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _listing(**over) -> Listing:
    base = dict(
        id="immoscout_1",
        platform="immoscout",
        url="https://example.com/listing-1",
        title="Schöne 3-Zimmer-Wohnung",
        price=320000.0,
        size_sqm=72.0,
        rooms=3.0,
        district="Eimsbüttel",
        address="Musterstraße 1, 20255 Hamburg",
        year_built=1965,
        hausgeld=210.0,
        nebenkosten=120.0,
        energy_rating="D",
        balcony=True,
        garden=False,
        parking=False,
        description="Helle Wohnung mit Blick ins Grüne.\nGute Lage.",
        image_urls=["https://img.example.com/1.jpg"],
    )
    base.update(over)
    return Listing(**base)


def _analysis(**over) -> AnalysisResult:
    base = dict(
        listing_id="immoscout_1",
        price_per_sqm=4444.0,
        district_avg_price_sqm=5500.0,
        price_vs_market_pct=-19.2,
        estimated_rent_monthly=1300.0,
        gross_rental_yield_pct=4.9,
        net_rental_yield_pct=3.1,
        cap_rate_pct=3.4,
        monthly_cashflow=120.0,
        cash_on_cash_return_pct=5.5,
        total_purchase_cost=355000.0,
        mortgage_monthly=1280.0,
        equity_required=71000.0,
        deal_score=72.0,
        undervalue_reasons=[],
    )
    base.update(over)
    return AnalysisResult(**base)


# ---------------------------------------------------------------------------
# Empty / smoke
# ---------------------------------------------------------------------------

class TestEmpty:
    def test_renders_with_empty_deals(self):
        html_out = build_digest_html(
            search_name="Demo Suche",
            search_slug="demo",
            deals=[],
            generated_at=datetime(2026, 4, 25, 8, 0),
        )
        assert html_out.startswith("<!DOCTYPE html>")
        assert "<html" in html_out and "</html>" in html_out
        # German "no deals" note is required.
        assert "Keine Deals" in html_out
        # Header still mentions the search name.
        assert "Demo Suche" in html_out


# ---------------------------------------------------------------------------
# Single deal: all required fields appear
# ---------------------------------------------------------------------------

class TestSingleDealCoreFields:
    def test_renders_single_deal_contains_all_core_fields(self):
        listing = _listing()
        analysis = _analysis()
        html_out = build_digest_html(
            "Demo", "demo", [(listing, analysis)],
            generated_at=datetime(2026, 4, 25, 8, 0),
        )
        # Title, district, url, price, price/m².
        assert "Schöne 3-Zimmer-Wohnung" in html_out
        assert "Eimsbüttel" in html_out
        assert "https://example.com/listing-1" in html_out
        assert "320,000" in html_out  # price formatted with comma thousands
        assert "4,444" in html_out  # price/m²
        # Score, gross yield, monthly cashflow.
        assert "72/100" in html_out
        assert "4.9 %" in html_out  # gross yield
        assert "120 €" in html_out  # cashflow
        # Section headers (German) are present.
        assert "Warum dieser Deal günstig sein könnte" in html_out
        assert "Risiken" in html_out and "Red Flags" in html_out
        assert "Finanzen" in html_out
        assert "Objektdaten" in html_out
        assert "Beschreibung" in html_out


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------

class TestEscaping:
    def test_escapes_html_in_title_description_address(self):
        payload = "<script>alert(1)</script>"
        listing = _listing(
            title=payload,
            description=payload,
            address=payload,
        )
        html_out = build_digest_html(
            "Demo", "demo", [(listing, _analysis())],
            generated_at=datetime(2026, 4, 25, 8, 0),
        )
        # Literal <script> must not appear; escaped form must.
        assert "<script>alert(1)</script>" not in html_out
        # Escaped version should appear at least once per field (title,
        # description, address) — we just assert presence here.
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_out


# ---------------------------------------------------------------------------
# URL safety
# ---------------------------------------------------------------------------

class TestUrlSafety:
    def test_non_http_url_is_sanitized(self):
        listing = _listing(url="javascript:alert(1)")
        html_out = build_digest_html(
            "Demo", "demo", [(listing, _analysis())],
            generated_at=datetime(2026, 4, 25, 8, 0),
        )
        # No href targeting javascript: scheme, no matter the casing/escaping.
        assert 'href="javascript:' not in html_out
        assert "href='javascript:" not in html_out
        assert "javascript:alert(1)" not in html_out
        # Visible placeholder shows instead.
        assert "[suspicious URL elided]" in html_out

    def test_image_url_validation(self):
        listing = _listing(image_urls=[
            "https://good.example.com/a.jpg",
            "http://good.example.com/b.jpg",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "data:image/png;base64,AAAA",
        ])
        html_out = build_digest_html(
            "Demo", "demo", [(listing, _analysis())],
            generated_at=datetime(2026, 4, 25, 8, 0),
        )
        # Both http(s) URLs become <img> tags.
        assert '<img class="thumb" src="https://good.example.com/a.jpg"' in html_out
        assert '<img class="thumb" src="http://good.example.com/b.jpg"' in html_out
        # None of the suspicious ones appear at all.
        assert "javascript:alert(1)" not in html_out
        assert "file:///etc/passwd" not in html_out
        assert "data:image/png" not in html_out


# ---------------------------------------------------------------------------
# Reasons split
# ---------------------------------------------------------------------------

class TestReasonsSplit:
    def test_opportunity_and_redflag_sections_split(self):
        analysis = _analysis(undervalue_reasons=[
            "[+] yield high",
            "[!] energy G",
        ])
        html_out = build_digest_html(
            "Demo", "demo", [(_listing(), analysis)],
            generated_at=datetime(2026, 4, 25, 8, 0),
        )
        # Both phrases appear, with prefixes stripped.
        assert "yield high" in html_out
        assert "energy G" in html_out
        assert "[+] yield high" not in html_out
        assert "[!] energy G" not in html_out

        # Opportunity must appear in the opportunity list, red flag in
        # the red flag list. We anchor on the German section headers
        # rather than CSS classes (the CSS rule names also contain
        # "reasons-opp" / "reasons-flag" in the inline <style> block).
        opp_header = html_out.index("Warum dieser Deal günstig")
        flag_header = html_out.index("Risiken")
        # Find the post-section anchor (Finanzen) that bounds the red
        # flag block, so we can isolate each segment cleanly.
        fin_header = html_out.index("Finanzen")
        opp_segment = html_out[opp_header:flag_header]
        flag_segment = html_out[flag_header:fin_header]
        assert "yield high" in opp_segment
        assert "energy G" not in opp_segment
        assert "energy G" in flag_segment
        assert "yield high" not in flag_segment


# ---------------------------------------------------------------------------
# Description truncation
# ---------------------------------------------------------------------------

class TestDescriptionTruncation:
    def test_description_truncation(self):
        # Build a 3000-char string with two sentinel markers — one inside
        # the 2000-char window, one outside.
        sentinel_in = "SENTINEL_INSIDE_WINDOW"
        sentinel_out = "SENTINEL_OUTSIDE_WINDOW"
        # Place sentinel_in at offset ~1990, sentinel_out at ~2500.
        prefix = "x" * 1990
        middle = "y" * (2500 - 1990 - len(sentinel_in))
        suffix = "z" * (3000 - 2500 - len(sentinel_out))
        # We want the truncation cap (2000 chars) to keep sentinel_in
        # but drop sentinel_out. sentinel_in starts at 1990, ends at
        # 1990+22=2012 — actually that crosses the cap. Reposition so
        # sentinel_in fully fits before 2000.
        prefix = "x" * (2000 - len(sentinel_in) - 5)  # leaves 5-char tail
        # New layout: prefix + sentinel_in + 5 'y's + filler to 2500 +
        # sentinel_out + filler to 3000.
        head = prefix + sentinel_in + "y" * 5
        assert len(head) == 2000
        body = "z" * (2500 - 2000)
        tail = sentinel_out + "w" * (3000 - 2500 - len(sentinel_out))
        desc = head + body + tail
        assert len(desc) == 3000

        listing = _listing(description=desc)
        html_out = build_digest_html(
            "Demo", "demo", [(listing, _analysis())],
            generated_at=datetime(2026, 4, 25, 8, 0),
        )
        assert sentinel_in in html_out
        assert sentinel_out not in html_out
        # Truncation indicator present.
        assert "…" in html_out


# ---------------------------------------------------------------------------
# Sort order is the caller's responsibility — but assert that order is
# preserved verbatim, so a higher-score deal passed first appears first.
# ---------------------------------------------------------------------------

class TestGesamtkostenRow:
    """iter-2 item 6 (mirror) — finance section must show all-in cost."""

    def test_gesamtkosten_row_present_with_value(self):
        listing = _listing()
        analysis = _analysis(total_purchase_cost=355_000.0)
        html_out = build_digest_html(
            "Demo", "demo", [(listing, analysis)],
            generated_at=datetime(2026, 4, 25, 8, 0),
        )
        assert "Gesamtkosten (inkl. NK)" in html_out
        # Value is formatted with comma thousands and renders in the same
        # row vicinity as the label.
        assert "355,000" in html_out


class TestCityDisplayName:
    """MULTI-CITY-A: HTML title and missing-district fallback respect the
    city display name instead of hard-coding "Hamburg"."""

    def test_title_uses_city_display_name(self):
        html_out = build_digest_html(
            search_name="Demo Suche",
            search_slug="demo",
            deals=[],
            generated_at=datetime(2026, 4, 25, 8, 0),
            city_display_name="Berlin",
        )
        assert "<title>Berlin Deal-Report" in html_out
        assert "Hamburg" not in html_out

    def test_default_title_is_hamburg(self):
        # Backwards compat: caller that doesn't pass city_display_name
        # still produces the legacy "Hamburg Deal-Report" title.
        html_out = build_digest_html(
            search_name="Demo",
            search_slug="demo",
            deals=[],
            generated_at=datetime(2026, 4, 25, 8, 0),
        )
        assert "Hamburg Deal-Report" in html_out

    def test_missing_district_uses_city_display_name(self):
        listing = _listing(district="")  # empty district
        html_out = build_digest_html(
            search_name="Demo",
            search_slug="demo",
            deals=[(listing, _analysis())],
            generated_at=datetime(2026, 4, 25, 8, 0),
            city_display_name="Heide",
        )
        # "Heide" appears as the deal's district label.
        assert "Heide" in html_out


class TestSortPreservedByCaller:
    def test_deals_sorted_by_score_desc(self):
        low = (
            _listing(id="immoscout_low", title="LowScoreDeal"),
            _analysis(listing_id="immoscout_low", deal_score=40.0),
        )
        high = (
            _listing(id="immoscout_high", title="HighScoreDeal"),
            _analysis(listing_id="immoscout_high", deal_score=85.0),
        )
        # Caller passes already-sorted (desc): high first, then low.
        html_out = build_digest_html(
            "Demo", "demo", [high, low],
            generated_at=datetime(2026, 4, 25, 8, 0),
        )
        idx_high = html_out.index("immoscout_high")
        idx_low = html_out.index("immoscout_low")
        assert idx_high < idx_low, "higher-score deal must render first"
