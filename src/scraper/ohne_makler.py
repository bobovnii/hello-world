"""Ohne-Makler.net scraper for commission-free Hamburg real estate listings."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from src.database.models import Listing, UserCriteria
from .base import BaseScraper
from .utils import clean_price, clean_size, clean_rooms, detect_district


class OhneMaklerScraper(BaseScraper):
    PLATFORM_NAME = "ohne-makler"
    BASE_URL = "https://www.ohne-makler.net"

    def build_search_url(self, criteria: UserCriteria, page: int = 1) -> str:
        # Ohne-Makler uses a single page per city, no server-side filters
        base = f"{self.BASE_URL}/immobilien/hamburg/hamburg/"
        if page > 1:
            base = f"{base}?page={page}"
        return base

    def search(self, criteria: UserCriteria, max_pages: int = 5) -> list[Listing]:
        """Override: parse listings directly from search page cards.

        Ohne-Makler doesn't have server-side filtering, so we fetch the
        page and filter buy listings client-side by price and keywords.
        """
        all_listings: list[Listing] = []
        seen_urls: set[str] = set()

        for page in range(1, max_pages + 1):
            url = self.build_search_url(criteria, page)
            self.logger.info(f"Scraping page {page}: {url}")

            html = self.fetch(url)
            if not html:
                break

            cards = self._parse_search_cards(html, criteria)
            if not cards:
                self.logger.info(f"No buy listings on page {page}, stopping")
                break

            for listing in cards:
                if listing.url not in seen_urls:
                    # Enrich from detail page
                    detail_html = self.fetch(listing.url)
                    if detail_html:
                        enriched = self._enrich_from_detail(listing, detail_html)
                        if enriched and self._matches_criteria(enriched, criteria):
                            if "multi_family" in criteria.property_types and enriched.property_type != "multi_family":
                                continue
                            seen_urls.add(enriched.url)
                            all_listings.append(enriched)
                            self.logger.info(
                                f"  Found: {enriched.title[:50]} - "
                                f"€{enriched.price:,.0f} / {enriched.size_sqm}m²"
                            )

            # Ohne-Makler typically shows all on one page
            if page >= 2:
                break

        self.logger.info(f"ohne-makler: Found {len(all_listings)} valid listings")
        return all_listings

    def _parse_search_cards(self, html: str, criteria: UserCriteria) -> list[Listing]:
        """Parse listing cards from search page, filtering for buy listings."""
        soup = BeautifulSoup(html, "lxml")
        listings: list[Listing] = []

        for card in soup.select('a[href*="/immobilie/"]'):
            card_text = card.get_text(strip=True)

            # Skip rental listings
            if "/ Monat" in card_text or "Pauschalmiete" in card_text:
                continue

            listing = self._parse_card(card)
            if listing and listing.price >= criteria.budget_min and listing.price <= criteria.budget_max * 1.05:
                listings.append(listing)

        return listings

    def _parse_card(self, card) -> Listing | None:
        """Parse a single search result card."""
        href = card.get("href", "")
        if not href.startswith("http"):
            href = f"{self.BASE_URL}{href}"

        # ID from URL
        id_match = re.search(r"/immobilie/(\d+)/", href)
        listing_id = f"ohne-makler_{id_match.group(1)}" if id_match else f"ohne-makler_{hash(href)}"

        # Price - first .block.font-semibold with €
        price = None
        price_el = card.select_one(".font-semibold")
        if price_el:
            price = clean_price(price_el.get_text())
        if not price or price < 10000:
            return None

        # Title
        title_el = card.select_one("[class*='line-clamp']")
        title = title_el.get_text(strip=True) if title_el else ""

        # Location (zip + city)
        loc_el = card.select_one(".text-slate-800")
        address = loc_el.get_text(strip=True) if loc_el else ""
        zip_match = re.search(r"(\d{5})", address)
        zip_code = zip_match.group(1) if zip_match else ""

        # Rooms and size from .block.text-slate-700.font-medium elements
        # Order: rooms (pure number), Wohnfläche (first m²), Grundstück (second m²)
        data_els = card.select(".block.text-slate-700.font-medium")
        rooms = 0.0
        sizes: list[float] = []
        for el in data_els:
            text = el.get_text(strip=True)
            if "m²" in text:
                val = clean_size(text)
                if val:
                    sizes.append(val)
            else:
                try:
                    rooms = float(text.replace(",", "."))
                except ValueError:
                    pass

        # First m² is Wohnfläche, second (if present) is Grundstücksfläche
        size = sizes[0] if sizes else 0.0

        return Listing(
            id=listing_id,
            platform="ohne-makler",
            url=href,
            title=title,
            price=price,
            size_sqm=size if size > 0 else 1,  # Placeholder, enriched from detail
            rooms=rooms,
            address=address,
            district=detect_district(address, zip_code),
            zip_code=zip_code,
        )

    def _enrich_from_detail(self, listing: Listing, html: str) -> Listing:
        """Enrich a listing with data from its detail page."""
        soup = BeautifulSoup(html, "lxml")

        # Parse all table rows into a dict
        details: dict[str, str] = {}
        for table in soup.select("table"):
            for row in table.select("tr"):
                cells = row.select("td, th")
                if len(cells) >= 2:
                    key = cells[0].get_text(strip=True).lower()
                    val = cells[1].get_text(strip=True)
                    details[key] = val

        # Price (more accurate from detail)
        if "kaufpreis" in details:
            price = clean_price(details["kaufpreis"])
            if price:
                listing.price = price

        # Wohnfläche - try explicit fields, but prefer card value if already set
        for key in ["wohnfläche", "wohnflaeche"]:
            if key in details:
                size = clean_size(details[key])
                if size and size > 0:
                    listing.size_sqm = size
                    break
        # Only use Nutzfläche if we don't have a better value
        if listing.size_sqm <= 1 and "nutzfläche" in details:
            size = clean_size(details["nutzfläche"])
            if size and size > 0:
                listing.size_sqm = size

        # Rooms
        for key in ["zimmer", "zimmer (anzahl)", "anzahl zimmer"]:
            if key in details:
                rooms = clean_rooms(details[key])
                if rooms:
                    listing.rooms = rooms
                    break

        # If no Zimmer, try Schlafzimmer + 1 (living room)
        if listing.rooms == 0 and "schlafzimmer (anzahl)" in details:
            bedrooms = clean_rooms(details["schlafzimmer (anzahl)"])
            if bedrooms:
                listing.rooms = bedrooms + 1  # + living room

        # Year built
        if "baujahr" in details:
            match = re.search(r"(\d{4})", details["baujahr"])
            if match:
                listing.year_built = int(match.group(1))

        # Condition
        if "zustand" in details:
            listing.condition = details["zustand"]

        # Hausgeld
        if "hausgeld mtl." in details:
            listing.hausgeld = clean_price(details["hausgeld mtl."])

        # Property type
        objektart = details.get("objektart", "").lower()
        objekttyp = details.get("objekttyp", "").lower()
        combined = f"{objektart} {objekttyp}"
        if "mehrfamilienhaus" in combined or "zinshaus" in combined or "anlage" in combined:
            listing.property_type = "multi_family"
        elif "wohnung" in combined:
            listing.property_type = "apartment"
        elif any(w in combined for w in ("haus", "bungalow", "villa", "reihenhaus", "doppelhaushälfte")):
            listing.property_type = "house"

        # Also check description for MFH keywords
        desc_el = soup.select_one(".prose")
        if desc_el:
            desc_text = desc_el.get_text(strip=True)[:2000]
            listing.description = desc_text
            mfh_keywords = [
                "mehrfamilienhaus", "zinshaus", "renditeobjekt", "kapitalanlage",
                "wohneinheiten", "mieteinnahmen", "apartmenthaus", "mietshaus",
            ]
            if any(kw in desc_text.lower() for kw in mfh_keywords):
                listing.property_type = "multi_family"

        # Features
        ausstattung = details.get("ausstattung", "").lower()
        listing.balcony = "balkon" in ausstattung
        listing.garden = "garten" in ausstattung
        listing.parking = any(
            w in details
            for w in ["anzahl garagen", "anzahl stellplätze", "anzahl tiefgaragen"]
        )

        # Floor
        if "stockwerk" in details:
            floor_match = re.search(r"(\d+)", details["stockwerk"])
            if floor_match:
                listing.floor = int(floor_match.group(1))

        # Energy rating
        if "energieeffizienzklasse" in details:
            listing.energy_rating = details["energieeffizienzklasse"]

        return listing

    # Required by BaseScraper ABC but not used (search() is overridden)
    def parse_search_results(self, html: str) -> list[dict]:
        return []

    def parse_listing_detail(self, html: str, url: str) -> Listing | None:
        return None
