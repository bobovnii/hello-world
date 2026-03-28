"""Kleinanzeigen.de (formerly eBay Kleinanzeigen) scraper for Hamburg real estate."""

from __future__ import annotations

import re
import json

from bs4 import BeautifulSoup

from src.database.models import Listing, UserCriteria
from .base import BaseScraper
from .utils import clean_price, clean_size, clean_rooms, detect_district


class KleinanzeigenScraper(BaseScraper):
    PLATFORM_NAME = "kleinanzeigen"
    BASE_URL = "https://www.kleinanzeigen.de"

    def build_search_url(self, criteria: UserCriteria, page: int = 1) -> str:
        # Kleinanzeigen uses category-based URLs
        # c208 = Wohnung kaufen, c209 = Haus kaufen
        prop_type = criteria.property_types[0] if criteria.property_types else "apartment"
        category = "c208" if prop_type == "apartment" else "c209"

        base = f"{self.BASE_URL}/s-wohnung-kaufen/hamburg/{category}"

        params = []
        if criteria.budget_min > 0:
            params.append(f"preis.von={int(criteria.budget_min)}")
        if criteria.budget_max < 1_000_000:
            params.append(f"preis.bis={int(criteria.budget_max)}")
        if criteria.min_size_sqm > 0:
            params.append(f"wohnflaeche.von={int(criteria.min_size_sqm)}")
        if criteria.min_rooms > 1:
            params.append(f"zimmer.von={int(criteria.min_rooms)}")
        if page > 1:
            params.append(f"seite:{page}")

        if params:
            return f"{base}?{'&'.join(params)}"
        return base

    def parse_search_results(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        results = []

        # Kleinanzeigen uses article tags or ad-listitem class
        for item in soup.select("article.aditem, li.ad-listitem, [data-adid]"):
            ad_id = item.get("data-adid", "") or item.get("data-id", "")

            link = item.select_one("a[href*='/s-anzeige/']")
            if not link:
                link = item.select_one("a.ellipsis")
            if not link:
                continue

            href = link.get("href", "")
            if not href.startswith("http"):
                href = f"{self.BASE_URL}{href}"

            results.append({"url": href, "id": ad_id})

        return results

    def parse_listing_detail(self, html: str, url: str) -> Listing | None:
        soup = BeautifulSoup(html, "lxml")

        # Extract ad ID from URL
        lid_match = re.search(r"/(\d+)$", url.rstrip("/"))
        listing_id = f"kleinanzeigen_{lid_match.group(1)}" if lid_match else f"kleinanzeigen_{hash(url)}"

        # Title
        title_el = soup.select_one("h1#viewad-title, h1")
        title = title_el.get_text(strip=True) if title_el else ""

        # Price
        price = None
        price_el = soup.select_one("#viewad-price, .addetailslist--detail--value")
        if price_el:
            price = clean_price(price_el.get_text())

        if not price:
            # Try from attributes section
            for el in soup.find_all(string=re.compile(r"Kaufpreis|Preis")):
                parent = el.find_parent()
                if parent:
                    sibling = parent.find_next_sibling()
                    if sibling:
                        price = clean_price(sibling.get_text())
                        if price:
                            break

        if not price:
            return None

        # Parse attribute details
        details = self._parse_details(soup)

        size = details.get("size")
        rooms = details.get("rooms", 0)

        if not size:
            return None

        # Address / Location
        addr_el = soup.select_one("#viewad-locality, .addetailslist--detail")
        address = addr_el.get_text(strip=True) if addr_el else ""

        zip_match = re.search(r"\b(\d{5})\b", address)
        zip_code = zip_match.group(1) if zip_match else ""

        # Description
        desc_el = soup.select_one("#viewad-description-text, .describecontent")
        description = desc_el.get_text(strip=True)[:2000] if desc_el else None

        # Features from description
        page_text = (description or "").lower() + " " + soup.get_text().lower()
        balcony = "balkon" in page_text
        garden = "garten" in page_text
        parking = any(w in page_text for w in ("stellplatz", "garage", "parkplatz"))

        # Images
        image_urls = []
        for img in soup.select("#viewad-image img, .galleryimage img"):
            src = img.get("data-src") or img.get("src", "")
            if src and "placeholder" not in src:
                image_urls.append(src)

        # Year built from description
        year_built = details.get("year_built")

        return Listing(
            id=listing_id,
            platform="kleinanzeigen",
            url=url,
            title=title,
            price=price,
            size_sqm=size,
            rooms=rooms,
            address=address,
            district=detect_district(address, zip_code),
            zip_code=zip_code,
            year_built=year_built,
            property_type=details.get("property_type", "apartment"),
            hausgeld=details.get("hausgeld"),
            balcony=balcony,
            garden=garden,
            parking=parking,
            description=description,
            image_urls=image_urls[:10],
        )

    def _parse_details(self, soup: BeautifulSoup) -> dict:
        """Extract structured details from Kleinanzeigen detail sections."""
        details: dict = {}

        # Look for detail items (Kleinanzeigen uses various layouts)
        for item in soup.select(".addetailslist--detail, .attributelist--attribute"):
            label_el = item.select_one(".addetailslist--detail--key, dt")
            value_el = item.select_one(".addetailslist--detail--value, dd")

            if not label_el or not value_el:
                continue

            label = label_el.get_text(strip=True).lower()
            value = value_el.get_text(strip=True)

            if "wohnfläche" in label or "fläche" in label:
                details["size"] = clean_size(value)
            elif "zimmer" in label:
                details["rooms"] = clean_rooms(value) or 0
            elif "baujahr" in label:
                match = re.search(r"(\d{4})", value)
                if match:
                    details["year_built"] = int(match.group(1))
            elif "hausgeld" in label:
                details["hausgeld"] = clean_price(value)
            elif "art" in label:
                if "wohnung" in value.lower():
                    details["property_type"] = "apartment"
                elif "haus" in value.lower():
                    details["property_type"] = "house"
                elif "mehrfamilien" in value.lower():
                    details["property_type"] = "multi_family"

        return details
