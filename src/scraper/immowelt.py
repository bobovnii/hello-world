"""Immowelt.de scraper for Hamburg real estate listings.

Immowelt search pages return full listing data in data-testid elements.
Detail pages return 403 for automated requests, so we extract all data
directly from the search result cards.
"""

from __future__ import annotations

import re
import threading
import time

import requests
from bs4 import BeautifulSoup

from src.database.models import Listing, UserCriteria
from .base import BaseScraper
from .city_registry import get_city
from .utils import clean_price, clean_size, clean_rooms, detect_district, MFH_KEYWORDS


class ImmoweltScraper(BaseScraper):
    PLATFORM_NAME = "immowelt"
    BASE_URL = "https://www.immowelt.de"
    HAMBURG_LOCATION_ID = "AD08DE1113"
    BACKOFF_SECONDS = 20
    # Immowelt 403s as soon as the UA changes within a cookie session.
    ROTATE_HEADERS = False
    # DataDome 403s back-to-back searches; one shared session and a run-wide
    # gap between requests keeps every search answering 200.
    MIN_GAP_SECONDS = 30.0
    _shared_session: requests.Session | None = None
    _last_request_at: float = 0.0
    _pace_lock = threading.Lock()

    def __init__(self, rate_limit_min: float = 2.0, rate_limit_max: float = 5.0):
        super().__init__(rate_limit_min, rate_limit_max)
        cls = ImmoweltScraper
        with cls._pace_lock:
            if cls._shared_session is None:
                cls._shared_session = self.session
            else:
                self.session.close()
        self.session = cls._shared_session

    def fetch(self, url: str) -> str | None:
        cls = ImmoweltScraper
        with cls._pace_lock:
            wait = cls._last_request_at + self.MIN_GAP_SECONDS - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                return super().fetch(url)
            finally:
                cls._last_request_at = time.time()

    def close(self):
        pass  # shared session lives for the whole run

    def _passes_region_filter(self, listing: Listing, criteria: UserCriteria) -> bool:
        """Zip-range gate (shared with kleinanzeigen). Immowelt location ids
        are municipality-level so this mostly guards against the Heide id
        (AD08DE6) pulling in neighbouring Dithmarschen villages."""
        return self._zip_region_filter(listing, criteria)

    def build_search_url(self, criteria: UserCriteria, page: int = 1) -> str:
        """Build an immowelt ``classified-search`` URL (site relaunch, Nov 2026).

        The old ``/liste/<city>/wohnungen/kaufen?pmin=…`` URLs now 301 to a
        SEO page that silently drops every query parameter, so filters and
        pagination only work through ``/classified-search``. Verified live:
        ``priceMin``/``priceMax``/``numberOfRoomsMin``/``spaceMin``/``page``
        are honoured; there is no recognised multi-family ``estateTypes``
        value, so MFH searches use ``House`` and rely on the post-filter in
        ``search()``.
        """
        prop_type = criteria.property_types[0] if criteria.property_types else "apartment"
        estate_type = {
            "apartment": "Apartment",
            "house": "House",
            "multi_family": "House",
        }.get(prop_type, "Apartment")

        try:
            city = get_city(getattr(criteria, "city", "hamburg") or "hamburg")
            location_id = city.immowelt_location_id or self.HAMBURG_LOCATION_ID
        except ValueError:
            location_id = self.HAMBURG_LOCATION_ID

        params = [
            "distributionTypes=Buy",
            f"estateTypes={estate_type}",
            f"locations={location_id}",
        ]
        if criteria.budget_min > 0:
            params.append(f"priceMin={int(criteria.budget_min)}")
        if criteria.budget_max < 50_000_000:
            params.append(f"priceMax={int(criteria.budget_max)}")
        if criteria.min_size_sqm > 0:
            params.append(f"spaceMin={int(criteria.min_size_sqm)}")
        if criteria.min_rooms > 1 and prop_type != "multi_family":
            params.append(f"numberOfRoomsMin={int(criteria.min_rooms)}")
        if page > 1:
            params.append(f"page={page}")
        return f"{self.BASE_URL}/classified-search?{'&'.join(params)}"

    def _fetch_results_page(self, criteria: UserCriteria, page: int) -> str | None:
        """Fetch one results page; one back-off retry on failure, then give up.

        Immowelt sits behind DataDome, which answers 403 + captcha once an IP
        looks automated. We respect that: back off once, and if it still
        refuses, stop this search rather than trying to route around it.
        """
        url = self.build_search_url(criteria, page)
        self.logger.info(f"Scraping page {page}: {url}")
        html = self.fetch(url)
        if html:
            return html
        self.logger.warning(f"Failed to fetch page {page}, backing off {self.BACKOFF_SECONDS}s")
        time.sleep(self.BACKOFF_SECONDS)
        return self.fetch(url)

    def search(self, criteria: UserCriteria, max_pages: int = 5) -> list[Listing]:
        """Override search to extract data directly from search result cards.

        Immowelt detail pages return 403 for automated requests, so we
        parse all listing data directly from the search page cards.
        """
        all_listings: list[Listing] = []
        seen_urls: set[str] = set()

        for page in range(1, max_pages + 1):
            html = self._fetch_results_page(criteria, page)
            if not html:
                self.logger.warning(f"Failed to fetch page {page}, stopping")
                break

            # Parse listings directly from search cards
            listings = self._parse_search_cards(html)
            if not listings:
                self.logger.info(f"No results on page {page}, stopping")
                break

            for listing in listings:
                if listing.url not in seen_urls and self._matches_criteria(listing, criteria):
                    # For multi-family search, validate MFH
                    if "multi_family" in criteria.property_types:
                        if listing.property_type != "multi_family":
                            continue
                        text = f"{(listing.title or '').lower()} {(listing.description or '').lower()}"
                        has_mfh_keyword = any(kw in text for kw in MFH_KEYWORDS)
                        if not has_mfh_keyword and listing.size_sqm < 120:
                            continue
                    # Off-plan / cooperative-share filter — immowelt overrides
                    # BaseScraper.search() so the hooks must be re-called.
                    if self._is_off_plan_or_coop(listing):
                        self.logger.info(
                            f"  Skipped (off-plan/coop): "
                            f"{(listing.title or '')[:60]}"
                        )
                        continue
                    if not self._passes_region_filter(listing, criteria):
                        self.logger.info(
                            f"  Skipped (out-of-region): "
                            f"{(listing.address or listing.title or '')[:60]}"
                        )
                        continue
                    seen_urls.add(listing.url)
                    all_listings.append(listing)
                    self.logger.info(
                        f"  Found: {listing.title[:50]} - "
                        f"€{listing.price:,.0f} / {listing.size_sqm}m²"
                    )

        self.logger.info(f"immowelt: Found {len(all_listings)} valid listings")
        return all_listings

    def _parse_search_cards(self, html: str) -> list[Listing]:
        """Parse listing data from search result cards using data-testid attributes.

        Card structure:
        <div data-testid="serp-core-classified-card-testid">
          <a href="/expose/{id}">
          <div data-testid="cardmfe-price-testid">269.000 € 5.839 €/m²</div>
          <div data-testid="cardmfe-keyfacts-testid">2 Zimmer · 46,1 m² · 3. Geschoss</div>
          <div data-testid="cardmfe-description-box-address">Street, District, Hamburg (ZIP)</div>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        listings: list[Listing] = []

        cards = soup.select('[data-testid="serp-core-classified-card-testid"]')
        if not cards:
            # Fallback: find any container with expose links
            cards = soup.select('[data-testid*="classified-card"]')

        for card in cards:
            listing = self._parse_single_card(card)
            if listing:
                listings.append(listing)

        return listings

    def _parse_single_card(self, card) -> Listing | None:
        """Parse a single search result card into a Listing."""
        # URL from expose link
        link = card.select_one("a[href*='/expose/']")
        if not link:
            return None
        href = link.get("href", "")
        if not href.startswith("http"):
            href = f"{self.BASE_URL}{href}"

        eid_match = re.search(r"/expose/([\w-]+)", href)
        eid = eid_match.group(1) if eid_match else str(hash(href))
        listing_id = f"immowelt_{eid}"

        # Price from cardmfe-price-testid
        price = None
        price_el = card.select_one('[data-testid="cardmfe-price-testid"]')
        if price_el:
            price_text = price_el.get_text(strip=True)
            # Text is like "269.000 €5.839 €/m²" - take the first price
            price_match = re.match(r"([\d.]+(?:,\d+)?)\s*€", price_text)
            if price_match:
                price = clean_price(price_match.group(1) + " €")

        if not price:
            return None

        # Keyfacts: "2 Zimmer · 46,1 m² · 3. Geschoss"
        rooms = 0.0
        size = 0.0
        floor = None
        keyfacts_el = card.select_one('[data-testid="cardmfe-keyfacts-testid"]')
        if keyfacts_el:
            keyfacts_text = keyfacts_el.get_text(strip=True)
        else:
            # Nov 2026 markup: keyfacts live in a hashed-class div
            # ("4 Zimmer · 102 m² · 3. Geschoss"). Locate it via the text
            # node containing "Zimmer" or "m²" (but not the "€/m²" price).
            keyfacts_text = ""
            for node in card.find_all(string=re.compile(r"Zimmer|m²")):
                txt = node.parent.get_text(" ", strip=True) if node.parent else str(node)
                if "€/m²" in txt:
                    continue
                keyfacts_text = txt
                break
        if keyfacts_text:
            # Parse rooms
            rooms_match = re.search(r"([\d,]+)\s*Zimmer", keyfacts_text)
            if rooms_match:
                rooms = clean_rooms(rooms_match.group(1)) or 0

            # Parse size
            size_match = re.search(r"([\d.,]+)\s*m²", keyfacts_text)
            if size_match:
                size = clean_size(size_match.group(1)) or 0

            # Parse floor
            floor_match = re.search(r"(\d+)\.\s*(?:Geschoss|OG|Stock)", keyfacts_text)
            if floor_match:
                floor = int(floor_match.group(1))

        if not size:
            return None

        # Address: "Straße, Stadtteil, Hamburg (22089)"
        address = ""
        zip_code = ""
        addr_el = card.select_one('[data-testid="cardmfe-description-box-address"]')
        if addr_el:
            address = addr_el.get_text(strip=True)
            zip_match = re.search(r"\((\d{5})\)", address)
            if zip_match:
                zip_code = zip_match.group(1)

        # Title from description box
        title = ""
        # Try the main text content of the card
        desc_el = card.select_one('[data-testid="cardmfe-description-box-text-test-id"]')
        if desc_el:
            full_text = desc_el.get_text(strip=True)
            # Title is typically everything after the price/keyfacts
            title = address or full_text[:80]

        # Energy rating
        energy_el = card.select_one('[data-testid="card-mfe-energy-performance-class"]')
        energy_rating = energy_el.get_text(strip=True) if energy_el else None

        # Description snippet from bottom of card (~200 chars)
        description = None
        desc_el = card.select_one('[data-testid="cardmfe-bottom-test-id"]')
        if not desc_el:
            desc_el = card.select_one('[data-testid="cardmfe-description-text-test-id"]')
        if desc_el:
            description = desc_el.get_text(strip=True)

        # Agent/publisher name
        agent_el = card.select_one('[data-testid="cardmfe-agency-publisher-xl-test-id"]')

        # Detect property type from card text
        property_type = "apartment"
        full_text = card.get_text(" ").lower()
        type_label_match = re.search(r"([a-zäöüß-]+)\s+zum kauf", full_text)
        type_label = type_label_match.group(1) if type_label_match else ""
        if type_label in ("mehrfamilienhaus", "anlageimmobilie", "wohn-/geschäftshaus", "zweifamilienhaus"):
            property_type = "multi_family"
        elif "haus" in type_label or type_label in ("villa", "bungalow"):
            property_type = "house"
        elif not type_label:
            if "mehrfamilienhaus" in full_text or "anlageimmobilie" in full_text:
                property_type = "multi_family"
            elif any(w in full_text for w in ("einfamilienhaus", "doppelhaushälfte", "reihenhaus", "reihenendhaus", "villa")):
                property_type = "house"

        # Detect tags (Neu, etc.)
        tags = [t.get_text(strip=True) for t in card.select('[data-testid*="cardmfe-tag"]')]

        # Detect features from description/card text
        balcony = "balkon" in full_text
        garden = "garten" in full_text
        parking = any(w in full_text for w in ("stellplatz", "garage", "parkplatz", "tiefgarage"))

        district = detect_district(address, zip_code)

        # Build title from property type + address
        type_label = {"apartment": "Wohnung", "house": "Haus", "multi_family": "Mehrfamilienhaus"}.get(property_type, "Immobilie")
        title = f"{type_label} in {district}" if district else address

        return Listing(
            id=listing_id,
            platform="immowelt",
            url=href,
            title=title,
            price=price,
            size_sqm=size,
            rooms=rooms,
            address=address,
            district=district,
            zip_code=zip_code,
            floor=floor,
            energy_rating=energy_rating,
            property_type=property_type,
            description=description,
            balcony=balcony,
            garden=garden,
            parking=parking,
        )

    # These are kept for API compatibility but search() no longer calls them
    def parse_search_results(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        results = []
        for link in soup.select("a[href*='/expose/']"):
            href = link.get("href", "")
            if not href.startswith("http"):
                href = f"{self.BASE_URL}{href}"
            eid_match = re.search(r"/expose/([\w-]+)", href)
            eid = eid_match.group(1) if eid_match else ""
            if href not in [r["url"] for r in results]:
                results.append({"url": href, "id": eid})
        return results

    def parse_listing_detail(self, html: str, url: str) -> Listing | None:
        # Detail pages return 403 - this is kept as a no-op for compatibility
        return None
