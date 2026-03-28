"""Tests for scraper utilities and parsing."""

import pytest

from src.scraper.utils import (
    clean_price,
    clean_size,
    clean_rooms,
    detect_district,
    RateLimiter,
)
from src.database.models import UserCriteria
from src.scraper.immoscout import ImmoScoutScraper
from src.scraper.kleinanzeigen import KleinanzeigenScraper
from src.scraper.immowelt import ImmoweltScraper


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
        assert "hamburg" in url
        assert "wohnung-kaufen" in url

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

    def test_pagination(self):
        scraper = ImmoScoutScraper()
        criteria = UserCriteria()
        url_p1 = scraper.build_search_url(criteria, page=1)
        url_p2 = scraper.build_search_url(criteria, page=2)
        assert "pagenumber" not in url_p1
        assert "pagenumber=2" in url_p2
