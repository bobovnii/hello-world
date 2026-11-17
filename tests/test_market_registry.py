"""Tests for the per-city market registry (MULTI-CITY-A).

Verifies that the four city JSONs in ``data/cities/`` load through
``MarketRegistry``, that ``.get(slug)`` returns a CityMarketData with
the expected per-city defaults, and that an unknown slug raises
ValueError instead of silently falling back to Hamburg.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analyzer.market_data import CityMarketData
from src.analyzer.market_registry import MarketRegistry


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestRegistryLoad:
    def test_four_cities_present(self):
        reg = MarketRegistry()
        slugs = set(reg.slugs)
        # Hamburg is required; the three new cities must also load.
        for required in ("hamburg", "berlin", "dresden", "heide"):
            assert required in slugs, f"{required} missing from registry"

    def test_get_returns_citymarketdata(self):
        reg = MarketRegistry()
        for slug in ("hamburg", "berlin", "dresden", "heide"):
            m = reg.get(slug)
            assert isinstance(m, CityMarketData)
            assert m.slug == slug

    def test_unknown_city_raises(self):
        reg = MarketRegistry()
        with pytest.raises(ValueError, match="unknown city"):
            reg.get("munich")

    def test_empty_slug_raises(self):
        reg = MarketRegistry()
        with pytest.raises(ValueError):
            reg.get("")


class TestCityDefaults:
    """Each city's city_default_price_sqm must reflect the city, not Hamburg."""

    def test_hamburg_default(self):
        m = MarketRegistry().get("hamburg")
        # Hamburg JSON sets the default to 4771.
        assert m.city_default_price_sqm == pytest.approx(4771.0)

    def test_berlin_default(self):
        m = MarketRegistry().get("berlin")
        assert m.city_default_price_sqm == pytest.approx(5300.0)
        assert m.display_name == "Berlin"
        assert m.bundesland == "Berlin"

    def test_dresden_default(self):
        m = MarketRegistry().get("dresden")
        assert m.city_default_price_sqm == pytest.approx(2700.0)
        assert m.display_name == "Dresden"
        assert m.bundesland == "Sachsen"

    def test_heide_default(self):
        m = MarketRegistry().get("heide")
        assert m.city_default_price_sqm == pytest.approx(2100.0)
        assert m.display_name == "Heide"
        assert m.bundesland == "Schleswig-Holstein"

    def test_unknown_district_falls_back_to_city_not_hamburg(self):
        """Critical regression: a Heide listing with an unknown district must
        compare against Heide's 2100 €/m², not Hamburg's 4771 €/m²."""
        heide = MarketRegistry().get("heide")
        # Pass a district name we know isn't in Heide's data.
        avg = heide.get_avg_price_sqm("Westerheide")
        assert avg == pytest.approx(2100.0)

        berlin = MarketRegistry().get("berlin")
        # Karlshorst is in the YAML but not in our seed JSON yet, so it
        # should fall back to Berlin's 5300 — NOT Hamburg's 4771.
        avg = berlin.get_avg_price_sqm("Karlshorst")
        assert avg == pytest.approx(5300.0)


class TestGrunderwerbsteuerPerBundesland:
    """purchase_costs_breakdown.grunderwerbsteuer is per-Bundesland."""

    def test_hamburg(self):
        m = MarketRegistry().get("hamburg")
        assert m.purchase_costs_breakdown["grunderwerbsteuer"] == pytest.approx(5.5)

    def test_berlin(self):
        m = MarketRegistry().get("berlin")
        assert m.purchase_costs_breakdown["grunderwerbsteuer"] == pytest.approx(6.0)

    def test_dresden_saxony(self):
        m = MarketRegistry().get("dresden")
        assert m.purchase_costs_breakdown["grunderwerbsteuer"] == pytest.approx(5.5)

    def test_heide_sh(self):
        m = MarketRegistry().get("heide")
        assert m.purchase_costs_breakdown["grunderwerbsteuer"] == pytest.approx(6.5)


class TestSourceCitations:
    """Every district seed record must carry a 'source' + 'as_of_year'."""

    @pytest.mark.parametrize("slug", ["berlin", "dresden", "heide"])
    def test_district_has_source(self, slug):
        path = REPO_ROOT / "data" / "cities" / f"{slug}.json"
        data = json.loads(path.read_text())
        for name, d in data["districts"].items():
            assert d.get("source"), f"{slug}/{name} missing source"
            assert isinstance(d.get("as_of_year"), int), (
                f"{slug}/{name} missing as_of_year int"
            )
