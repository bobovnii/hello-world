"""Tests for database operations."""

import os
import tempfile

import pytest

from src.database.models import Listing, UserCriteria, AnalysisResult
from src.database.db import Database


@pytest.fixture
def db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = Database(db_path=path)
    yield database
    database.close()
    os.unlink(path)


@pytest.fixture
def sample_listing():
    return Listing(
        id="test_1",
        platform="immoscout",
        url="https://example.com/1",
        title="Test Wohnung",
        price=200000,
        size_sqm=60,
        rooms=3,
        district="Harburg",
    )


class TestDatabase:
    def test_save_and_get_listing(self, db, sample_listing):
        is_new = db.save_listing(sample_listing)
        assert is_new

        listings = db.get_listings()
        assert len(listings) == 1
        assert listings[0].id == "test_1"
        assert listings[0].price == 200000

    def test_update_existing(self, db, sample_listing):
        db.save_listing(sample_listing)
        sample_listing.price = 190000
        is_new = db.save_listing(sample_listing)
        assert not is_new

        listings = db.get_listings()
        assert len(listings) == 1
        assert listings[0].price == 190000

    def test_bulk_save(self, db):
        listings = [
            Listing(id=f"test_{i}", platform="test", url=f"url_{i}",
                    title=f"Listing {i}", price=100000 + i * 50000,
                    size_sqm=50 + i * 10, rooms=2 + i)
            for i in range(5)
        ]
        new, updated = db.save_listings(listings)
        assert new == 5
        assert updated == 0

    def test_filter_by_criteria(self, db):
        listings = [
            Listing(id="cheap", platform="test", url="u1", title="Cheap",
                    price=100000, size_sqm=40, rooms=2, district="Harburg"),
            Listing(id="mid", platform="test", url="u2", title="Mid",
                    price=250000, size_sqm=70, rooms=3, district="Wandsbek"),
            Listing(id="expensive", platform="test", url="u3", title="Expensive",
                    price=500000, size_sqm=100, rooms=4, district="Eimsbüttel"),
        ]
        db.save_listings(listings)

        criteria = UserCriteria(budget_min=100000, budget_max=300000)
        results = db.get_listings(criteria)
        assert len(results) == 2
        assert all(l.price <= 300000 for l in results)

    def test_save_and_get_analysis(self, db, sample_listing):
        db.save_listing(sample_listing)

        analysis = AnalysisResult(
            listing_id="test_1",
            deal_score=75.5,
            gross_rental_yield_pct=5.2,
            monthly_cashflow=150,
            undervalue_reasons=["Below market", "Rising district"],
        )
        db.save_analysis(analysis)

        top = db.get_top_deals(limit=10)
        assert len(top) == 1
        listing, result = top[0]
        assert result.deal_score == 75.5
        assert len(result.undervalue_reasons) == 2

    def test_user_criteria_crud(self, db):
        criteria = UserCriteria(
            chat_id=12345,
            budget_min=100000,
            budget_max=400000,
            districts=["Harburg", "Wandsbek"],
        )
        db.save_user_criteria(criteria)

        loaded = db.get_user_criteria(12345)
        assert loaded is not None
        assert loaded.budget_max == 400000
        assert "Harburg" in loaded.districts

    def test_nonexistent_user(self, db):
        assert db.get_user_criteria(99999) is None


class TestModels:
    def test_listing_to_from_dict(self):
        listing = Listing(
            id="t1", platform="test", url="url", title="Title",
            price=200000, size_sqm=60, rooms=3,
            image_urls=["img1.jpg", "img2.jpg"],
            balcony=True,
        )
        d = listing.to_dict()
        restored = Listing.from_dict(d)
        assert restored.id == listing.id
        assert restored.image_urls == ["img1.jpg", "img2.jpg"]
        assert restored.balcony is True

    def test_listing_price_per_sqm(self):
        listing = Listing(
            id="t1", platform="test", url="", title="",
            price=200000, size_sqm=80, rooms=3,
        )
        assert listing.price_per_sqm == 2500.0

    def test_criteria_to_from_dict(self):
        criteria = UserCriteria(
            districts=["Altona", "Harburg"],
            property_types=["apartment", "house"],
            chat_id=123,
        )
        d = criteria.to_dict()
        restored = UserCriteria.from_dict(d)
        assert restored.districts == ["Altona", "Harburg"]
        assert restored.property_types == ["apartment", "house"]
