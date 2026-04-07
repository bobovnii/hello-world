"""Abstract base class for all scrapers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import requests

from src.database.models import Listing, UserCriteria
from .utils import RateLimiter, get_headers, can_fetch, fetch_page

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """Base class for real estate platform scrapers."""

    PLATFORM_NAME: str = ""
    BASE_URL: str = ""

    def __init__(self, rate_limit_min: float = 2.0, rate_limit_max: float = 5.0):
        self.session = requests.Session()
        self.session.headers.update(get_headers())
        self.rate_limiter = RateLimiter(rate_limit_min, rate_limit_max)
        self.logger = logging.getLogger(f"scraper.{self.PLATFORM_NAME}")

    def fetch(self, url: str) -> str | None:
        """Fetch a URL with rate limiting and robots.txt check."""
        try:
            if not can_fetch(url):
                self.logger.warning(f"Blocked by robots.txt: {url}")
                return None
        except Exception:
            pass  # robots.txt check failure should not block scraping

        self.rate_limiter.wait()
        return fetch_page(url, self.session)

    @abstractmethod
    def build_search_url(self, criteria: UserCriteria, page: int = 1) -> str:
        """Build the search URL from user criteria."""
        ...

    @abstractmethod
    def parse_search_results(self, html: str) -> list[dict]:
        """Parse search results HTML into raw listing dicts."""
        ...

    @abstractmethod
    def parse_listing_detail(self, html: str, url: str) -> Listing | None:
        """Parse a single listing detail page into a Listing object."""
        ...

    def search(
        self, criteria: UserCriteria, max_pages: int = 5
    ) -> list[Listing]:
        """Run a search and return parsed listings."""
        all_listings: list[Listing] = []
        seen_urls: set[str] = set()

        for page in range(1, max_pages + 1):
            url = self.build_search_url(criteria, page)
            self.logger.info(f"Scraping page {page}: {url}")

            html = self.fetch(url)
            if not html:
                self.logger.warning(f"Failed to fetch page {page}, stopping")
                break

            results = self.parse_search_results(html)
            if not results:
                self.logger.info(f"No results on page {page}, stopping")
                break

            for item in results:
                detail_url = item.get("url", "")
                if not detail_url or detail_url in seen_urls:
                    continue
                seen_urls.add(detail_url)

                # Try to get full details
                detail_html = self.fetch(detail_url)
                if detail_html:
                    listing = self.parse_listing_detail(detail_html, detail_url)
                    if listing and self._matches_criteria(listing, criteria):
                        # For multi-family search, validate MFH classification
                        if "multi_family" in criteria.property_types:
                            if listing.property_type != "multi_family":
                                continue
                            # A single apartment or small house is not an MFH
                            # MFH must have: explicit MFH property_type AND either
                            # large size (>120m²) or many rooms (>5) or explicit MFH keywords in title
                            title_lower = (listing.title or "").lower()
                            desc_lower = (listing.description or "").lower()
                            text = f"{title_lower} {desc_lower}"
                            has_mfh_keyword = any(kw in text for kw in (
                                "mehrfamilienhaus", "zinshaus", "wohneinheiten",
                                "miethaus", "apartmenthaus", "wohnanlage",
                                "zwei wohneinheiten", "zwei einheiten",
                                "zweifamilienhaus", "dreifamilienhaus",
                            ))
                            if not has_mfh_keyword and listing.size_sqm < 120:
                                continue
                        all_listings.append(listing)
                        self.logger.info(
                            f"  Found: {listing.title[:50]} - "
                            f"€{listing.price:,.0f} / {listing.size_sqm}m²"
                        )

        self.logger.info(
            f"{self.PLATFORM_NAME}: Found {len(all_listings)} valid listings"
        )
        return all_listings

    @staticmethod
    def _matches_criteria(listing: Listing, criteria: UserCriteria) -> bool:
        """Post-filter: check listing matches basic criteria."""
        if listing.price <= 0 or listing.size_sqm <= 0:
            return False
        if listing.price < criteria.budget_min or listing.price > criteria.budget_max * 1.05:
            return False
        if listing.size_sqm < criteria.min_size_sqm * 0.9:
            return False
        # Room filter: strict minimum (no tolerance - 3 rooms means 3+)
        # For multi-family, rooms may be absent or represent units, so skip
        is_mfh = "multi_family" in criteria.property_types or listing.property_type == "multi_family"
        if not is_mfh and criteria.min_rooms > 1:
            if listing.rooms < criteria.min_rooms:
                return False
        # District filter
        if criteria.districts and listing.district:
            if listing.district not in criteria.districts:
                return False
        return True

    def close(self):
        self.session.close()
