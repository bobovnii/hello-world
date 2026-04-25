"""Immowelt.de scraper for Hamburg real estate listings.

Immowelt search pages return full listing data in data-testid elements.
Detail pages return 403 for automated requests, so we extract all data
directly from the search result cards.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from src.database.models import Listing, UserCriteria
from .base import BaseScraper
from .city_registry import get_city
from .utils import clean_price, clean_size, clean_rooms, detect_district, MFH_KEYWORDS


class ImmoweltScraper(BaseScraper):
    PLATFORM_NAME = "immowelt"
    BASE_URL = "https://www.immowelt.de"

    def build_search_url(self, criteria: UserCriteria, page: int = 1) -> str:
        prop_type = criteria.property_types[0] if criteria.property_types else "apartment"
        prop_map = {
            "apartment": "wohnungen",
            "house": "haeuser",
            "multi_family": "haeuser",  # MFH listed under houses on Immowelt
        }
        prop_path = prop_map.get(prop_type, "wohnungen")

        params = []
        if criteria.budget_min > 0:
            params.append(f"pmin={int(criteria.budget_min)}")
        if criteria.budget_max < 50_000_000:
            params.append(f"pmax={int(criteria.budget_max)}")
        if criteria.min_size_sqm > 0:
            params.append(f"amin={int(criteria.min_size_sqm)}")
        if criteria.min_rooms > 1:
            params.append(f"rmin={int(criteria.min_rooms)}")
        if page > 1:
            params.append(f"sp={page}")

        # CITY-SUPPORT-1: per-city path slug. Falls back to Hamburg if
        # the criteria doesn't carry a known city.
        try:
            city = get_city(getattr(criteria, "city", "hamburg") or "hamburg")
            city_path = city.immowelt_path
        except ValueError:
            city_path = "hamburg"

        base = f"{self.BASE_URL}/liste/{city_path}/{prop_path}/kaufen"
        if params:
            return f"{base}?{'&'.join(params)}"
        return base

    def search(self, criteria: UserCriteria, max_pages: int = 5) -> list[Listing]:
        """Override search to extract data directly from search result cards.

        Immowelt detail pages return 403 for automated requests, so we
        parse all listing data directly from the search page cards.
        """
        all_listings: list[Listing] = []
        seen_urls: set[str] = set()

        for page in range(1, max_pages + 1):
            url = self.build_search_url(criteria, page)
            self.logger.info(f"Scraping page {page}: {url}")

            html = self.fetch(url)
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
        full_text = card.get_text().lower()
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
