"""ImmoScout24 scraper for Hamburg real estate listings."""

from __future__ import annotations

import re
import json
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from src.database.models import Listing, UserCriteria
from .base import BaseScraper
from .utils import clean_price, clean_size, clean_rooms, detect_district


class ImmoScoutScraper(BaseScraper):
    PLATFORM_NAME = "immoscout"
    BASE_URL = "https://www.immobilienscout24.de"

    def build_search_url(self, criteria: UserCriteria, page: int = 1) -> str:
        # ImmoScout uses path-based search params
        property_map = {
            "apartment": "wohnung-kaufen",
            "house": "haus-kaufen",
            "multi_family": "mehrfamilienhaus-kaufen",
        }
        prop_type = property_map.get(
            criteria.property_types[0] if criteria.property_types else "apartment",
            "wohnung-kaufen",
        )

        params = {}
        if criteria.budget_max < 1_000_000:
            params["price"] = f"-{int(criteria.budget_max)}"
        if criteria.budget_min > 0:
            params["price"] = f"{int(criteria.budget_min)}-" + params.get("price", "").lstrip("-")
        if criteria.min_size_sqm > 0:
            params["livingspace"] = f"{int(criteria.min_size_sqm)}-"
        if criteria.min_rooms > 1:
            params["numberofrooms"] = f"{criteria.min_rooms}-"
        if page > 1:
            params["pagenumber"] = str(page)

        base = f"{self.BASE_URL}/Suche/de/hamburg/hamburg/{prop_type}"
        if params:
            return f"{base}?{urlencode(params)}"
        return base

    def parse_search_results(self, html: str) -> list[dict]:
        """Parse ImmoScout search result page for listing links and basic data."""
        soup = BeautifulSoup(html, "lxml")
        results = []

        # ImmoScout uses result list items with data attributes
        for item in soup.select("[data-id]"):
            listing_id = item.get("data-id", "")
            if not listing_id:
                continue

            link = item.select_one("a[href*='/expose/']")
            if not link:
                continue

            href = link.get("href", "")
            if not href.startswith("http"):
                href = f"{self.BASE_URL}{href}"

            results.append({"url": href, "id": listing_id})

        # Also try JSON-LD or inline script data
        if not results:
            results = self._parse_from_scripts(soup)

        return results

    def _parse_from_scripts(self, soup: BeautifulSoup) -> list[dict]:
        """Try to extract listing data from embedded JSON in scripts."""
        results = []
        for script in soup.find_all("script", type="application/json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict):
                    self._extract_from_json(data, results)
            except (json.JSONDecodeError, TypeError):
                continue

        # Also look for IS24 specific script patterns
        for script in soup.find_all("script"):
            text = script.string or ""
            if "resultListEntries" in text or "searchResponseModel" in text:
                # Try to find JSON objects in script
                for match in re.finditer(r'\{[^{}]*"@id"\s*:\s*"(\d+)"[^{}]*\}', text):
                    listing_id = match.group(1)
                    results.append({
                        "url": f"{self.BASE_URL}/expose/{listing_id}",
                        "id": listing_id,
                    })

        return results

    def _extract_from_json(self, data: dict, results: list[dict]):
        """Recursively extract listing URLs from JSON data."""
        if "@id" in data and isinstance(data["@id"], str) and data["@id"].isdigit():
            results.append({
                "url": f"{self.BASE_URL}/expose/{data['@id']}",
                "id": data["@id"],
            })
        for value in data.values():
            if isinstance(value, dict):
                self._extract_from_json(value, results)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._extract_from_json(item, results)

    def parse_listing_detail(self, html: str, url: str) -> Listing | None:
        """Parse a single ImmoScout expose page."""
        soup = BeautifulSoup(html, "lxml")

        # Try to get structured data from JSON-LD
        listing = self._parse_json_ld(soup, url)
        if listing:
            return listing

        # Fallback to HTML parsing
        return self._parse_html_detail(soup, url)

    def _parse_json_ld(self, soup: BeautifulSoup, url: str) -> Listing | None:
        """Parse listing from JSON-LD structured data."""
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    data = next((d for d in data if d.get("@type") in ("Residence", "Apartment", "House", "Product")), None)
                if not data:
                    continue

                price = None
                if "offers" in data and "price" in data["offers"]:
                    price = float(data["offers"]["price"])

                if not price:
                    continue

                # Extract listing ID from URL
                lid_match = re.search(r"/expose/(\d+)", url)
                listing_id = f"immoscout_{lid_match.group(1)}" if lid_match else f"immoscout_{hash(url)}"

                address_data = data.get("address", {})
                address = address_data.get("streetAddress", "") if isinstance(address_data, dict) else str(address_data)
                zip_code = address_data.get("postalCode", "") if isinstance(address_data, dict) else ""

                return Listing(
                    id=listing_id,
                    platform="immoscout",
                    url=url,
                    title=data.get("name", ""),
                    price=price,
                    size_sqm=float(data.get("floorSize", {}).get("value", 0)) if isinstance(data.get("floorSize"), dict) else 0,
                    rooms=float(data.get("numberOfRooms", 0)),
                    address=address,
                    zip_code=zip_code,
                    district=detect_district(address, zip_code),
                )
            except (json.JSONDecodeError, TypeError, ValueError, KeyError):
                continue
        return None

    def _parse_html_detail(self, soup: BeautifulSoup, url: str) -> Listing | None:
        """Parse listing details from HTML elements."""
        lid_match = re.search(r"/expose/(\d+)", url)
        listing_id = f"immoscout_{lid_match.group(1)}" if lid_match else f"immoscout_{hash(url)}"

        # Title
        title_el = soup.select_one("h1, #expose-title")
        title = title_el.get_text(strip=True) if title_el else ""

        # Price - look for common IS24 selectors
        price = None
        for selector in [".is24qa-kaufpreis", ".is24qa-preis", "[data-qa='kaufpreis']"]:
            el = soup.select_one(selector)
            if el:
                price = clean_price(el.get_text())
                if price:
                    break

        # Also try generic price patterns
        if not price:
            for el in soup.find_all(string=re.compile(r"[\d.]+\s*€")):
                p = clean_price(str(el))
                if p and p > 10000:
                    price = p
                    break

        if not price:
            return None

        # Size
        size = None
        for selector in [".is24qa-wohnflaeche-ca", ".is24qa-flaeche", "[data-qa='wohnflaeche']"]:
            el = soup.select_one(selector)
            if el:
                size = clean_size(el.get_text())
                if size:
                    break

        if not size:
            return None

        # Rooms
        rooms = 0.0
        for selector in [".is24qa-zi", ".is24qa-zimmer", "[data-qa='zimmer']"]:
            el = soup.select_one(selector)
            if el:
                r = clean_rooms(el.get_text())
                if r:
                    rooms = r
                    break

        # Address
        addr_el = soup.select_one(".address-block, .zip-region-and-country")
        address = addr_el.get_text(strip=True) if addr_el else ""

        zip_match = re.search(r"\b(\d{5})\b", address)
        zip_code = zip_match.group(1) if zip_match else ""

        # Hausgeld
        hausgeld = None
        hg_el = soup.select_one(".is24qa-hausgeld")
        if hg_el:
            hausgeld = clean_price(hg_el.get_text())

        # Year built
        year_built = None
        yb_el = soup.select_one(".is24qa-baujahr")
        if yb_el:
            match = re.search(r"(\d{4})", yb_el.get_text())
            if match:
                year_built = int(match.group(1))

        # Condition
        condition = None
        cond_el = soup.select_one(".is24qa-objektzustand")
        if cond_el:
            condition = cond_el.get_text(strip=True)

        # Description
        desc_el = soup.select_one(".is24qa-objektbeschreibung, #objectDescription")
        description = desc_el.get_text(strip=True)[:2000] if desc_el else None

        # Features
        page_text = soup.get_text().lower()
        balcony = "balkon" in page_text
        garden = "garten" in page_text
        parking = any(w in page_text for w in ("stellplatz", "garage", "parkplatz", "tiefgarage"))

        # Images
        image_urls = []
        for img in soup.select("img[data-src], img[src]"):
            src = img.get("data-src") or img.get("src", "")
            if "pictures" in src or "immobilienscout" in src:
                image_urls.append(src)

        return Listing(
            id=listing_id,
            platform="immoscout",
            url=url,
            title=title,
            price=price,
            size_sqm=size,
            rooms=rooms,
            address=address,
            district=detect_district(address, zip_code),
            zip_code=zip_code,
            year_built=year_built,
            condition=condition,
            hausgeld=hausgeld,
            balcony=balcony,
            garden=garden,
            parking=parking,
            description=description,
            image_urls=image_urls[:10],
        )
