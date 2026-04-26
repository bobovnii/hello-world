"""Tests for scraper utilities and parsing."""

import pytest

from src.scraper.utils import (
    clean_price,
    clean_size,
    clean_rooms,
    detect_district,
    RateLimiter,
    ERBBAURECHT_KEYWORDS,
    RENTED_KEYWORDS,
    WBS_KEYWORDS,
    DACHGESCHOSS_KEYWORDS,
    AUSBAU_KEYWORDS,
    MFH_KEYWORDS,
    OFF_PLAN_PROJECT_KEYWORDS,
)
from src.database.models import UserCriteria, Listing
from src.scraper.base import BaseScraper
from src.scraper.immoscout import ImmoScoutScraper
from src.scraper.kleinanzeigen import KleinanzeigenScraper
from src.scraper.immowelt import ImmoweltScraper
from src.scraper.ohne_makler import OhneMaklerScraper


class TestCleanPrice:
    def test_german_format(self):
        assert clean_price("250.000 €") == 250000.0

    def test_with_eur(self):
        assert clean_price("250.000 EUR") == 250000.0

    def test_with_decimals(self):
        assert clean_price("250.000,50 €") == 250000.50

    def test_simple_number(self):
        assert clean_price("250000") == 250000.0

    def test_none_input(self):
        assert clean_price("") is None
        assert clean_price(None) is None

    def test_invalid(self):
        assert clean_price("abc") is None


class TestCleanSize:
    def test_german_format(self):
        assert clean_size("75,5 m²") == 75.5

    def test_without_unit(self):
        assert clean_size("75,5") == 75.5

    def test_integer(self):
        assert clean_size("100 m²") == 100.0

    def test_none(self):
        assert clean_size("") is None


class TestCleanRooms:
    def test_integer(self):
        assert clean_rooms("3 Zimmer") == 3.0

    def test_half_room(self):
        assert clean_rooms("3,5 Zimmer") == 3.5

    def test_just_number(self):
        assert clean_rooms("2") == 2.0


class TestDetectDistrict:
    def test_by_zip_code(self):
        assert detect_district("", "22765") == "Altona"
        assert detect_district("", "21073") == "Harburg"

    def test_from_address(self):
        assert detect_district("Eppendorfer Weg 123, 20253 Hamburg") == "Eimsbüttel"

    def test_by_name_in_address(self):
        assert detect_district("Winterhude, Hamburg") == "Hamburg-Nord"
        assert detect_district("Ottensen") == "Altona"

    def test_unknown(self):
        assert detect_district("Somewhere") == "Hamburg"


class TestRateLimiter:
    def test_creates(self):
        rl = RateLimiter(0.01, 0.02)
        assert rl.min_delay == 0.01


class TestSearchURLBuilding:
    def test_immoscout_url(self):
        scraper = ImmoScoutScraper()
        criteria = UserCriteria(budget_max=300000, min_size_sqm=50, min_rooms=2)
        url = scraper.build_search_url(criteria)
        assert "immobilienscout24.de" in url
        assert "apartmentbuy" in url
        assert "300000" in url

    def test_kleinanzeigen_url(self):
        scraper = KleinanzeigenScraper()
        criteria = UserCriteria(budget_max=300000)
        url = scraper.build_search_url(criteria)
        assert "kleinanzeigen.de" in url
        assert "hamburg" in url
        assert "l9409" in url  # Hamburg location ID
        assert "c196" in url  # Eigentumswohnung category

    def test_immowelt_url(self):
        scraper = ImmoweltScraper()
        criteria = UserCriteria(budget_max=300000)
        url = scraper.build_search_url(criteria)
        assert "immowelt.de" in url
        assert "hamburg" in url

    def test_ohne_makler_url(self):
        scraper = OhneMaklerScraper()
        criteria = UserCriteria(budget_max=300000)
        url = scraper.build_search_url(criteria)
        assert "ohne-makler.net" in url
        assert "hamburg" in url

    def test_pagination(self):
        scraper = ImmoScoutScraper()
        criteria = UserCriteria()
        url_p1 = scraper.build_search_url(criteria, page=1)
        url_p2 = scraper.build_search_url(criteria, page=2)
        assert "pagenumber=1" in url_p1
        assert "pagenumber=2" in url_p2


class TestCityAwareSearchURLs:
    """CITY-SUPPORT-1: each scraper must honour ``criteria.city``.

    Pure URL-construction tests — no network. The Hamburg-default
    regressions live in ``TestSearchURLBuilding`` above; here we just
    prove that switching cities flips the right tokens in the URL.
    """

    # ------------------------------------------------------------------
    # immowelt
    # ------------------------------------------------------------------
    def test_immowelt_berlin(self):
        scraper = ImmoweltScraper()
        criteria = UserCriteria(city="berlin", budget_max=300000)
        url = scraper.build_search_url(criteria)
        assert "/berlin/" in url
        assert "/hamburg/" not in url

    def test_immowelt_dresden(self):
        scraper = ImmoweltScraper()
        url = scraper.build_search_url(UserCriteria(city="dresden"))
        assert "/dresden/" in url

    def test_immowelt_heide(self):
        scraper = ImmoweltScraper()
        url = scraper.build_search_url(UserCriteria(city="heide"))
        assert "/heide/" in url

    def test_immowelt_default_is_hamburg(self):
        """Bare UserCriteria() must keep the legacy Hamburg URL shape."""
        scraper = ImmoweltScraper()
        url = scraper.build_search_url(UserCriteria())
        assert "/hamburg/" in url

    # ------------------------------------------------------------------
    # kleinanzeigen
    # ------------------------------------------------------------------
    def test_kleinanzeigen_berlin(self):
        scraper = KleinanzeigenScraper()
        url = scraper.build_search_url(UserCriteria(city="berlin"))
        assert "/berlin/" in url
        assert "l3331" in url
        assert "l9409" not in url

    def test_kleinanzeigen_dresden(self):
        scraper = KleinanzeigenScraper()
        url = scraper.build_search_url(UserCriteria(city="dresden"))
        assert "/dresden/" in url
        # l3820 = Dresden - Sachsen (was l4030 = Annaberg-Buchholz, wrong).
        assert "l3820" in url

    def test_kleinanzeigen_heide(self):
        scraper = KleinanzeigenScraper()
        url = scraper.build_search_url(UserCriteria(city="heide"))
        assert "/heide/" in url
        # l836 = Heide - Dithmarschen (was l1814 = Arnsberg NRW, wrong).
        assert "l836" in url

    def test_kleinanzeigen_default_is_hamburg(self):
        scraper = KleinanzeigenScraper()
        url = scraper.build_search_url(UserCriteria())
        assert "/hamburg/" in url
        assert "l9409" in url

    # ------------------------------------------------------------------
    # ohne-makler
    # ------------------------------------------------------------------
    def test_ohne_makler_berlin(self):
        scraper = OhneMaklerScraper()
        url = scraper.build_search_url(UserCriteria(city="berlin"))
        assert "/berlin/berlin/" in url

    def test_ohne_makler_dresden(self):
        scraper = OhneMaklerScraper()
        url = scraper.build_search_url(UserCriteria(city="dresden"))
        assert "/sachsen/dresden/" in url

    def test_ohne_makler_heide_uses_bundesland_path(self):
        scraper = OhneMaklerScraper()
        url = scraper.build_search_url(UserCriteria(city="heide"))
        assert "/schleswig-holstein/heide/" in url

    def test_ohne_makler_default_is_hamburg(self):
        scraper = OhneMaklerScraper()
        url = scraper.build_search_url(UserCriteria())
        assert "/hamburg/hamburg/" in url

    # ------------------------------------------------------------------
    # immoscout (mobile API, geocoordinates differ per city)
    # ------------------------------------------------------------------
    def test_immoscout_berlin_geocoords(self):
        """Berlin URL must carry Berlin coordinates, not Hamburg's."""
        scraper = ImmoScoutScraper()
        url = scraper.build_search_url(UserCriteria(city="berlin"))
        # Berlin centre lat starts with 52.; Hamburg's with 53.
        assert "geocoordinates=52." in url
        assert "geocoordinates=53." not in url

    def test_immoscout_dresden_geocoords(self):
        scraper = ImmoScoutScraper()
        url = scraper.build_search_url(UserCriteria(city="dresden"))
        assert "geocoordinates=51." in url

    def test_immoscout_heide_geocoords(self):
        scraper = ImmoScoutScraper()
        url = scraper.build_search_url(UserCriteria(city="heide"))
        # Heide centre lat ~54.19
        assert "geocoordinates=54." in url

    def test_immoscout_default_is_hamburg(self):
        scraper = ImmoScoutScraper()
        url = scraper.build_search_url(UserCriteria())
        assert "geocoordinates=53.5511" in url


class TestKeywordLists:
    """Verify canonical keyword lists are properly defined and non-empty."""

    def test_mfh_keywords(self):
        assert len(MFH_KEYWORDS) > 0
        assert "mehrfamilienhaus" in MFH_KEYWORDS

    def test_erbbaurecht_keywords(self):
        assert len(ERBBAURECHT_KEYWORDS) > 0
        assert "erbbaurecht" in ERBBAURECHT_KEYWORDS

    def test_rented_keywords(self):
        assert len(RENTED_KEYWORDS) > 0
        assert "vermietet" in RENTED_KEYWORDS

    def test_wbs_keywords(self):
        assert len(WBS_KEYWORDS) > 0
        assert "wbs" in WBS_KEYWORDS

    def test_dachgeschoss_keywords(self):
        assert len(DACHGESCHOSS_KEYWORDS) > 0
        assert "dachgeschoss" in DACHGESCHOSS_KEYWORDS

    def test_ausbau_keywords(self):
        assert len(AUSBAU_KEYWORDS) > 0
        assert "ausbaureserve" in AUSBAU_KEYWORDS

    def test_off_plan_project_keywords(self):
        assert len(OFF_PLAN_PROJECT_KEYWORDS) > 0
        # Cooperative
        assert "wohngenossenschaft" in OFF_PLAN_PROJECT_KEYWORDS
        # Pre-construction project
        assert "neubauprojekt" in OFF_PLAN_PROJECT_KEYWORDS
        assert "voraussichtliche fertigstellung" in OFF_PLAN_PROJECT_KEYWORDS


def _mk_listing(**overrides) -> Listing:
    """Minimal valid Listing for filter tests."""
    base = dict(
        id="test_1", platform="immoscout",
        url="https://example.com/1", title="3-Zimmer-Wohnung",
        price=300000.0, size_sqm=70.0, rooms=3.0,
        description="", address="", district="",
    )
    base.update(overrides)
    return Listing(**base)


class TestOffPlanFilter:
    """``BaseScraper._is_off_plan_or_coop`` drops cooperative shares and
    pre-construction projects so they don't pollute the digest."""

    def test_real_world_op_n_holm_cooperative_dropped(self):
        # Real listing the user flagged: Op'n Holm cooperative.
        l = _mk_listing(
            id="immoscout_165545304",
            title="Op'n Holm - 4-Zimmer - Süd-West-Balkon - private Wohngenossenschaft",
            price=118000.0, size_sqm=92.0,
        )
        assert BaseScraper._is_off_plan_or_coop(l) is True

    def test_neubauprojekt_in_title_dropped(self):
        l = _mk_listing(
            title="Erdgeschosswohnung mit Terrasse im Neubauprojekt Grüner Winkel",
            price=409000.0, size_sqm=70.0,
        )
        assert BaseScraper._is_off_plan_or_coop(l) is True

    def test_voraussichtliche_fertigstellung_in_description_dropped(self):
        l = _mk_listing(
            title="Schöne 2-Zimmer-Wohnung",
            description="Voraussichtliche Fertigstellung Q3 2027.",
            price=420000.0, size_sqm=58.0,
        )
        assert BaseScraper._is_off_plan_or_coop(l) is True

    def test_low_price_per_sqm_floor_catches_share_listings(self):
        # No keyword in title, but €1 280/m² is impossibly low for any
        # German market — almost certainly a cooperative share.
        l = _mk_listing(
            title="Op'n Holm - 4-Zimmer Balkon",
            price=118000.0, size_sqm=92.0,  # ~€1 283/m²
        )
        assert BaseScraper._is_off_plan_or_coop(l) is True

    def test_completed_neubau_kept(self):
        # Plain "Neubau" without project markers is a completed new build —
        # legitimate to surface (e.g., 2024-built unit being resold in 2026).
        l = _mk_listing(
            title="Neubau-Wohnung 2 Zimmer mit Balkon",
            description="Bezugsfertig, vom Vorbesitzer.",
            price=380000.0, size_sqm=55.0,
        )
        assert BaseScraper._is_off_plan_or_coop(l) is False

    def test_normal_listing_kept(self):
        l = _mk_listing(
            title="3-Zimmer-Wohnung in zentraler Lage",
            description="Bezugsfrei, Erstbezug nach Sanierung.",
            price=350000.0, size_sqm=85.0,
        )
        assert BaseScraper._is_off_plan_or_coop(l) is False

    def test_zero_size_does_not_crash(self):
        l = _mk_listing(price=300000.0, size_sqm=0.0)
        assert BaseScraper._is_off_plan_or_coop(l) is False

    def test_zero_price_does_not_crash(self):
        l = _mk_listing(price=0.0, size_sqm=70.0)
        assert BaseScraper._is_off_plan_or_coop(l) is False


class TestKleinanzeigenRegionFilter:
    """``KleinanzeigenScraper._passes_region_filter`` is the defense-in-depth
    layer that drops cross-region listings even when the location_id is
    correct (kleinanzeigen's radius search routinely surfaces adjacent
    municipalities). The user received a 59759-Arnsberg listing under the
    Heide search; this filter would have caught it regardless of the
    location_id bug."""

    def _scraper_with_city(self, _criteria_city: str) -> KleinanzeigenScraper:
        s = KleinanzeigenScraper(rate_limit_min=0, rate_limit_max=0)
        return s

    def _heide_criteria(self) -> UserCriteria:
        return UserCriteria(city="heide", property_types=["apartment"])

    def _berlin_criteria(self) -> UserCriteria:
        return UserCriteria(city="berlin", property_types=["apartment"])

    def _hamburg_criteria(self) -> UserCriteria:
        return UserCriteria(city="hamburg", property_types=["apartment"])

    def test_arnsberg_listing_dropped_under_heide_search(self):
        """The exact case the user flagged: zip 59759 (NRW) under Heide."""
        s = KleinanzeigenScraper(rate_limit_min=0, rate_limit_max=0)
        l = _mk_listing(
            title="Ohne Provision Etagenwohnung in 59759 Arnsberg",
            address="59759 Arnsberg",
            zip_code="59759",
        )
        assert s._passes_region_filter(l, self._heide_criteria()) is False

    def test_heide_zip_passes_under_heide_search(self):
        s = KleinanzeigenScraper(rate_limit_min=0, rate_limit_max=0)
        l = _mk_listing(
            title="3-Zimmer-Wohnung in Heide",
            address="25746 Heide",
            zip_code="25746",
        )
        assert s._passes_region_filter(l, self._heide_criteria()) is True

    def test_berlin_zip_passes_under_berlin_search(self):
        s = KleinanzeigenScraper(rate_limit_min=0, rate_limit_max=0)
        l = _mk_listing(
            title="2-Zimmer Karlshorst",
            address="10318 Berlin",
            zip_code="10318",
        )
        assert s._passes_region_filter(l, self._berlin_criteria()) is True

    def test_hamburg_zip_dropped_under_berlin_search(self):
        """Zip 22087 (Hamburg) must not pass a Berlin search even if the
        kleinanzeigen radius found it."""
        s = KleinanzeigenScraper(rate_limit_min=0, rate_limit_max=0)
        l = _mk_listing(
            title="2-Zimmer in Hamburg",
            address="22087 Hamburg",
            zip_code="22087",
        )
        assert s._passes_region_filter(l, self._berlin_criteria()) is False

    def test_no_zip_falls_back_to_address_token(self):
        """When the address has no zip but contains the city token, allow."""
        s = KleinanzeigenScraper(rate_limit_min=0, rate_limit_max=0)
        l = _mk_listing(
            title="2-Zimmer-Wohnung",
            address="Heide, schöne Lage",
            zip_code="",
        )
        assert s._passes_region_filter(l, self._heide_criteria()) is True

    def test_no_zip_no_token_dropped(self):
        s = KleinanzeigenScraper(rate_limit_min=0, rate_limit_max=0)
        l = _mk_listing(
            title="2-Zimmer-Wohnung",
            address="Schöne Wohnung",
            zip_code="",
        )
        assert s._passes_region_filter(l, self._heide_criteria()) is False

    def test_zip_extracted_from_title_when_address_empty(self):
        s = KleinanzeigenScraper(rate_limit_min=0, rate_limit_max=0)
        l = _mk_listing(
            title="Ohne Provision Etagenwohnung in 59759 Arnsberg",
            address="",
            zip_code="",
        )
        assert s._passes_region_filter(l, self._heide_criteria()) is False


class TestEnrichedFieldDetection:
    """Test that enriched fields are detected correctly from text."""

    def test_erbbaurecht_detection(self):
        text = "schöne wohnung im erbbaurecht"
        assert any(kw in text for kw in ERBBAURECHT_KEYWORDS)

    def test_rented_detection(self):
        text = "aktuell vermietet an langjährigen mieter"
        assert any(kw in text for kw in RENTED_KEYWORDS)

    def test_wbs_detection(self):
        text = "wohnung mit wbs erforderlich"
        assert any(kw in text for kw in WBS_KEYWORDS)

    def test_dachgeschoss_detection(self):
        text = "gemütliche dachgeschosswohnung"
        assert any(kw in text for kw in DACHGESCHOSS_KEYWORDS)

    def test_ausbau_detection(self):
        text = "mit großer ausbaureserve im dach"
        assert any(kw in text for kw in AUSBAU_KEYWORDS)

    def test_room_filter_strict_minimum(self):
        """Room filter should be strict: min_rooms=3 excludes 2-room listings."""
        from src.scraper.base import BaseScraper
        criteria = UserCriteria(budget_min=0, budget_max=500000, min_rooms=3, min_size_sqm=0)
        listing_2rooms = Listing(
            id="t1", platform="test", url="", title="Test",
            price=200000, size_sqm=60, rooms=2,
        )
        listing_3rooms = Listing(
            id="t2", platform="test", url="", title="Test",
            price=200000, size_sqm=60, rooms=3,
        )
        assert not BaseScraper._matches_criteria(listing_2rooms, criteria)
        assert BaseScraper._matches_criteria(listing_3rooms, criteria)
