"""ImmoScout24 scraper using the mobile API.

Uses the undocumented IS24 mobile app API endpoint which does NOT
have WAF/bot protection. This replaces the HTML scraper approach
which is blocked by AWS WAF from datacenter IPs.

Based on the approach used by flathunter (github.com/flathunters/flathunter).
"""

from __future__ import annotations

from urllib.parse import urlencode

import requests

from src.database.models import Listing, UserCriteria
from .base import BaseScraper
from .city_registry import CityInfo, get_city
from .utils import (
    clean_price, clean_size, clean_rooms, detect_district,
    MFH_KEYWORDS, ERBBAURECHT_KEYWORDS, RENTED_KEYWORDS,
    DACHGESCHOSS_KEYWORDS, AUSBAU_KEYWORDS, WBS_KEYWORDS,
)

# Hamburg center coordinates (Rathaus). Kept as module-level constants
# for backward compat with any external imports; the active value used
# inside the scraper now comes from ``city_registry.get_city(...)``.
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

        # CITY-SUPPORT-1: per-city radius search. Defaults to Hamburg if
        # an unknown city slipped through validation.
        city = self._city_for(criteria)
        lat, lng, radius = city.geocode_radius

        params = {
            "searchType": "radius",
            "realestatetype": prop_type,
            "geocoordinates": f"{lat};{lng};{int(radius)}",
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
        city = self._city_for(criteria)

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

                listing = self._parse_api_item(item.get("item", {}), city)
                if not listing or listing.id in seen_ids:
                    continue

                if not self._matches_criteria(listing, criteria):
                    continue

                # For MFH search, filter by keyword detection
                if is_mfh and listing.property_type != "multi_family":
                    continue

                # Off-plan / cooperative-share filter (shared with base class).
                # ImmoScout's mobile-API loop overrides BaseScraper.search()
                # and so MUST re-call this hook explicitly — without it,
                # Op'n Holm Wohngenossenschaft and Bauträger projects leak.
                if self._is_off_plan_or_coop(listing):
                    self.logger.info(
                        f"  Skipped (off-plan/coop): "
                        f"{(listing.title or '')[:60]}"
                    )
                    continue
                # Per-city region filter (no-op by default; subclasses may
                # override). Same reason as above for re-calling here.
                if not self._passes_region_filter(listing, criteria):
                    self.logger.info(
                        f"  Skipped (out-of-region): "
                        f"{(listing.address or listing.title or '')[:60]}"
                    )
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

    def _city_for(self, criteria: UserCriteria) -> CityInfo:
        """Resolve the CityInfo for criteria, falling back to Hamburg.

        Defaults exist so tests/scripts that build a bare ``UserCriteria()``
        without a city still get the original Hamburg behaviour.
        """
        try:
            return get_city(getattr(criteria, "city", "hamburg") or "hamburg")
        except ValueError:
            self.logger.warning(
                "unknown city %r on criteria — falling back to hamburg",
                getattr(criteria, "city", None),
            )
            return get_city("hamburg")

    def _parse_api_item(self, item: dict, city: CityInfo | None = None) -> Listing | None:
        """Parse a single result from the mobile API response.

        ``city`` defaults to Hamburg so existing tests that call this
        helper with a single arg keep working.
        """
        if not item:
            return None
        if city is None:
            city = get_city("hamburg")

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

        # CITY-SUPPORT-1: filter to in-city listings using the city's
        # postal-code ranges and address-token allowlist. The radius search
        # pulls in suburbs whose zip prefix overlaps neighbouring towns,
        # so we need the post-fetch filter regardless of which city we're
        # targeting.
        addr_lower = address.lower()
        # Fast path: if any city-name token appears in the address, accept
        # (matches old Hamburg behaviour). Otherwise demand a zip in range.
        city_name_in_addr = any(tok in addr_lower for tok in city.address_tokens)
        if not city_name_in_addr:
            # Excluded suburb names (Hamburg shares zip prefixes with
            # several Schleswig-Holstein/Niedersachsen towns).
            if any(tok in addr_lower for tok in city.exclude_address_tokens):
                return None
            # Zip must fall in one of the registered ranges.
            if zip_code:
                try:
                    z = int(zip_code)
                    in_range = any(low <= z <= hi for low, hi in city.zip_ranges)
                    if not in_range:
                        return None
                except ValueError:
                    pass
            # No zip + no city-name match: drop. Listings without either
            # are too risky to keep when we're city-filtering.
            elif city.zip_ranges:
                return None

        # Energy rating
        energy = item.get("energyEfficiencyClass")

        # Property type detection
        re_type = item.get("realEstateType", "").lower()
        property_type = "apartment"
        if "house" in re_type:
            property_type = "house"

        # MFH detection from title - strict keywords only
        title_lower = title.lower()
        if any(kw in title_lower for kw in MFH_KEYWORDS):
            property_type = "multi_family"

        district = detect_district(address, zip_code)

        # Published date
        published = item.get("published", "")

        # Expose URL
        eid = item.get("id", "")
        url = f"{self.BASE_URL}/expose/{eid}"

        # Enriched fields from title (IS24 mobile API only gives title + attributes)
        is_erbbaurecht = any(kw in title_lower for kw in ERBBAURECHT_KEYWORDS)
        is_rented = any(kw in title_lower for kw in RENTED_KEYWORDS)
        is_dachgeschoss = any(kw in title_lower for kw in DACHGESCHOSS_KEYWORDS)
        is_ausbau = any(kw in title_lower for kw in AUSBAU_KEYWORDS)
        is_wbs = any(kw in title_lower for kw in WBS_KEYWORDS)

        # Private seller flag
        is_private = item.get("isPrivate", False)

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
            is_erbbaurecht=is_erbbaurecht,
            is_rented=is_rented,
            is_dachgeschoss=is_dachgeschoss,
            is_ausbau_needed=is_ausbau,
            is_wbs=is_wbs,
        )

    # Required by BaseScraper ABC
    def parse_search_results(self, html: str) -> list[dict]:
        return []

    def parse_listing_detail(self, html: str, url: str) -> Listing | None:
        return None
