"""Tests for the city registry (CITY-SUPPORT-1).

Pure unit tests — no network. Verifies that the four cities we need to
support today (Hamburg, Berlin, Dresden, Heide) all have valid entries
and that ``get_city`` enforces an allowlist.
"""

from __future__ import annotations

import pytest

from src.scraper.city_registry import CITIES, CityInfo, get_city


class TestGetCity:
    def test_returns_hamburg(self):
        info = get_city("hamburg")
        assert isinstance(info, CityInfo)
        assert info.slug == "hamburg"

    def test_case_insensitive(self):
        assert get_city("Hamburg").slug == "hamburg"
        assert get_city("HAMBURG").slug == "hamburg"

    def test_strips_whitespace(self):
        assert get_city("  berlin ").slug == "berlin"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown city"):
            get_city("munich")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            get_city("")


class TestRegistryShape:
    def test_all_four_cities_present(self):
        for slug in ("hamburg", "berlin", "dresden", "heide"):
            assert slug in CITIES, f"{slug} missing from CITIES"

    def test_each_city_has_required_fields(self):
        for slug, info in CITIES.items():
            assert info.slug == slug, f"{slug}: slug mismatch"
            assert info.immowelt_path, f"{slug}: immowelt_path empty"
            assert info.kleinanzeigen_path, f"{slug}: kleinanzeigen_path empty"
            assert info.kleinanzeigen_location_id.startswith("l"), (
                f"{slug}: kleinanzeigen_location_id should start with 'l'"
            )
            assert info.ohne_makler_path, f"{slug}: ohne_makler_path empty"
            assert info.zip_ranges, f"{slug}: zip_ranges empty"
            for low, hi in info.zip_ranges:
                assert isinstance(low, int) and isinstance(hi, int)
                assert low <= hi, f"{slug}: zip range {low}-{hi} inverted"
                assert 0 < low < 100_000, f"{slug}: zip {low} out of plausible range"
                assert 0 < hi < 100_000, f"{slug}: zip {hi} out of plausible range"
            lat, lon, radius = info.geocode_radius
            assert 47 < lat < 56, f"{slug}: lat {lat} not in DE bounds"
            assert 5 < lon < 16, f"{slug}: lon {lon} not in DE bounds"
            assert 0 < radius <= 50, f"{slug}: radius {radius} implausible"

    def test_hamburg_zip_range_matches_legacy(self):
        """Regression: Hamburg's zip ranges must still cover the original
        20038-22769 + 21029-21149 used by the pre-multi-city scraper.
        """
        hh = get_city("hamburg")
        zips_to_check = [20038, 22000, 22769, 21029, 21100, 21149]
        for z in zips_to_check:
            assert any(low <= z <= hi for low, hi in hh.zip_ranges), (
                f"Hamburg zip {z} no longer covered by zip_ranges"
            )

    def test_berlin_zip_range(self):
        bln = get_city("berlin")
        # Mitte (10115), Charlottenburg (14199) — endpoints from the spec.
        assert any(low <= 10115 <= hi for low, hi in bln.zip_ranges)
        assert any(low <= 14199 <= hi for low, hi in bln.zip_ranges)

    def test_dresden_zip_range(self):
        # Dresden zips 01067-01328 → ints 1067..1328 after parsing.
        dd = get_city("dresden")
        assert any(low <= 1067 <= hi for low, hi in dd.zip_ranges)
        assert any(low <= 1328 <= hi for low, hi in dd.zip_ranges)

    def test_heide_zip(self):
        heide = get_city("heide")
        assert any(low <= 25746 <= hi for low, hi in heide.zip_ranges)

    def test_kleinanzeigen_ids_match_spec(self):
        assert get_city("hamburg").kleinanzeigen_location_id == "l9409"
        assert get_city("berlin").kleinanzeigen_location_id == "l3331"
        assert get_city("dresden").kleinanzeigen_location_id == "l4030"
        assert get_city("heide").kleinanzeigen_location_id == "l1814"

    def test_ohne_makler_path_for_heide_includes_bundesland(self):
        """Heide's ohne-makler URL needs the Bundesland prefix."""
        assert get_city("heide").ohne_makler_path == "schleswig-holstein/heide"
