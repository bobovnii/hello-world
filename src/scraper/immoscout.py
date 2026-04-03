"""ImmoScout24 scraper using the mobile API.

Uses the undocumented IS24 mobile app API endpoint which does NOT
have WAF/bot protection. This replaces the HTML scraper approach
which is blocked by AWS WAF from datacenter IPs.

Based on the approach used by flathunter (github.com/flathunters/flathunter).
"""

from __future__ import annotations

import re
import logging
from urllib.parse import urlencode

import requests

from src.database.models import Listing, UserCriteria
from .base import BaseScraper
from .utils import clean_price, clean_size, clean_rooms, detect_district

logger = logging.getLogger(__name__)

# Hamburg center coordinates (Rathaus)
HAMBURG_LAT = 53.5511
HAMBURG_LNG = 9.9937
HAMBURG_RADIUS_KM = 20

MOBILE_API_URL = "https://api.mobile.immobilienscout24.de/search/list?"
MOBILE_HEADERS = {
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "ImmoScout_27.3_26.0_._",
}
MOBILE_BODY = {"supportedResultListType": [], "userData": {}}


class ImmoScoutScraper(BaseScraper):
    PLATFORM_NAME = "immoscout"
    BASE_URL = "https://www.immobilienscout24.de"

    def build_search_url(self, criteria: UserCriteria, page: int = 1) -> str:
        property_map = {
            "apartment": "apartmentbuy",
            "house": "housebuy",
            "multi_family": "housebuy",
        }
        prop_type = property_map.get(
            criteria.property_types[0] if criteria.property_types else "apartment",
            "apartmentbuy",
        )

        params = {
            "searchType": "radius",
            "realestatetype": prop_type,
            "geocoordinates": f"{HAMBURG_LAT};{HAMBURG_LNG};{HAMBURG_RADIUS_KM}",
            "pagesize": "20",
            "pagenumber": str(page),
        }

        if criteria.budget_max < 10_000_000:
            price_str = ""
            if criteria.budget_min > 0:
                price_str = f"{int(criteria.budget_min)}-"
            price_str += f"{int(criteria.budget_max)}"
            if not price_str.startswith("-"):
                price_str = f"-{int(criteria.budget_max)}" if criteria.budget_min <= 0 else price_str
            params["price"] = price_str

        if criteria.min_size_sqm > 0:
            params["livingspace"] = f"{int(criteria.min_size_sqm)}-"

        if criteria.min_rooms > 1:
            params["numberofrooms"] = f"{criteria.min_rooms}-"

        return MOBILE_API_URL + urlencode(params)

    def search(self, criteria: UserCriteria, max_pages: int = 5) -> list[Listing]:
        """Search via IS24 mobile API. No WAF, no captcha."""
        all_listings: list[Listing] = []
        seen_ids: set[str] = set()
        is_mfh = "multi_family" in criteria.property_types

        for page in range(1, max_pages + 1):
            url = self.build_search_url(criteria, page)
            self.logger.info(f"Scraping page {page} via mobile API")

            self.rate_limiter.wait()

            try:
                resp = requests.post(
                    url, headers=MOBILE_HEADERS, json=MOBILE_BODY, timeout=30
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                self.logger.warning(f"Mobile API request failed: {e}")
                break

            total = data.get("totalResults", 0)
            items = data.get("resultListItems", [])

            if not items:
                break

            for item in items:
                if item.get("type") != "EXPOSE_RESULT":
                    continue

                listing = self._parse_api_item(item.get("item", {}))
                if not listing or listing.id in seen_ids:
                    continue

                if not self._matches_criteria(listing, criteria):
                    continue

                # For MFH search, filter by keyword detection
                if is_mfh and listing.property_type != "multi_family":
                    continue

                seen_ids.add(listing.id)
                all_listings.append(listing)
                self.logger.info(
                    f"  Found: {listing.title[:50]} - "
                    f"€{listing.price:,.0f} / {listing.size_sqm}m²"
                )

            # Check if we've reached the end
            num_pages = data.get("numberOfPages", 1)
            if page >= num_pages:
                break

        self.logger.info(f"immoscout: Found {len(all_listings)} listings via mobile API")
        return all_listings

    def _parse_api_item(self, item: dict) -> Listing | None:
        """Parse a single result from the mobile API response."""
        if not item:
            return None

        listing_id = f"immoscout_{item.get('id', '')}"
        title = item.get("title", "")

        # Parse attributes: [price, size, rooms]
        attrs = item.get("attributes", [])
        price = 0.0
        size = 0.0
        rooms = 0.0

        for i, attr in enumerate(attrs):
            val = attr.get("value", "")
            if i == 0:  # Price
                price = clean_price(val) or 0
            elif i == 1:  # Size
                size = clean_size(val) or 0
            elif i == 2:  # Rooms
                rooms = clean_rooms(val) or 0

        if price <= 0 or size <= 0:
            return None

        # Address
        addr_data = item.get("address", {})
        address = addr_data.get("line", "")
        zip_code = addr_data.get("postcode", "")

        # Filter: must be Hamburg area
        if address and "hamburg" not in address.lower() and zip_code:
            zip_prefix = zip_code[:2]
            if zip_prefix not in ("20", "21", "22"):
                return None

        # Energy rating
        energy = item.get("energyEfficiencyClass")

        # Property type detection
        re_type = item.get("realEstateType", "").lower()
        property_type = "apartment"
        if "house" in re_type:
            property_type = "house"

        # MFH detection from title
        title_lower = title.lower()
        mfh_keywords = [
            "mehrfamilienhaus", "zinshaus", "renditeobjekt",
            "kapitalanlage", "miethaus", "wohnanlage",
            "apartmenthaus", "wohneinheiten", "anlageimmobilie",
        ]
        if any(kw in title_lower for kw in mfh_keywords):
            property_type = "multi_family"

        district = detect_district(address, zip_code)

        # Published date
        published = item.get("published", "")

        # Expose URL
        eid = item.get("id", "")
        url = f"{self.BASE_URL}/expose/{eid}"

        return Listing(
            id=listing_id,
            platform="immoscout",
            url=url,
            title=title,
            price=price,
            size_sqm=size,
            rooms=rooms,
            address=address,
            district=district,
            zip_code=zip_code,
            energy_rating=energy,
            property_type=property_type,
            listing_date=published,
        )

    # Required by BaseScraper ABC
    def parse_search_results(self, html: str) -> list[dict]:
        return []

    def parse_listing_detail(self, html: str, url: str) -> Listing | None:
        return None
