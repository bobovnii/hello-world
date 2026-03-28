"""Immowelt.de scraper for Hamburg real estate listings."""

from __future__ import annotations

import re
import json
import logging

from bs4 import BeautifulSoup

from src.database.models import Listing, UserCriteria
from .base import BaseScraper
from .utils import clean_price, clean_size, clean_rooms, detect_district, get_headers

logger = logging.getLogger(__name__)


class ImmoweltScraper(BaseScraper):
    PLATFORM_NAME = "immowelt"
    BASE_URL = "https://www.immowelt.de"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._playwright = None
        self._browser = None

    def build_search_url(self, criteria: UserCriteria, page: int = 1) -> str:
        prop_type = criteria.property_types[0] if criteria.property_types else "apartment"
        prop_map = {
            "apartment": "wohnungen",
            "house": "haeuser",
            "multi_family": "mehrfamilienhaeuser",
        }
        prop_path = prop_map.get(prop_type, "wohnungen")

        params = []
        if criteria.budget_min > 0:
            params.append(f"pmin={int(criteria.budget_min)}")
        if criteria.budget_max < 1_000_000:
            params.append(f"pmax={int(criteria.budget_max)}")
        if criteria.min_size_sqm > 0:
            params.append(f"amin={int(criteria.min_size_sqm)}")
        if criteria.min_rooms > 1:
            params.append(f"rmin={int(criteria.min_rooms)}")
        if page > 1:
            params.append(f"sp={page}")

        base = f"{self.BASE_URL}/liste/hamburg/{prop_path}/kaufen"
        if params:
            return f"{base}?{'&'.join(params)}"
        return base

    def fetch(self, url: str) -> str | None:
        """Immowelt often needs JS rendering. Try requests first, fall back to Playwright."""
        html = super().fetch(url)
        if html and self._has_content(html):
            return html

        # Fallback to Playwright
        return self._fetch_with_playwright(url)

    def _has_content(self, html: str) -> bool:
        """Check if HTML has actual listing content vs JS-only shell."""
        soup = BeautifulSoup(html, "lxml")
        # Look for listing elements
        return bool(
            soup.select("[class*='listitem'], [class*='estate'], [data-test*='result']")
            or "estateid" in html.lower()
        )

    def _fetch_with_playwright(self, url: str) -> str | None:
        """Fetch page using Playwright for JS-rendered content."""
        try:
            if not self._playwright:
                from playwright.sync_api import sync_playwright
                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch(headless=True)

            page = self._browser.new_page()
            page.set_extra_http_headers({
                "User-Agent": self.session.headers.get("User-Agent", ""),
                "Accept-Language": "de-DE,de;q=0.9",
            })
            page.goto(url, wait_until="networkidle", timeout=30000)
            html = page.content()
            page.close()
            return html
        except Exception as e:
            logger.warning(f"Playwright fetch failed for {url}: {e}")
            return None

    def parse_search_results(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        results = []

        # Immowelt listing cards
        for item in soup.select("[class*='listitem'], [class*='EstateItem'], a[href*='/expose/']"):
            link = item if item.name == "a" else item.select_one("a[href*='/expose/']")
            if not link:
                continue

            href = link.get("href", "")
            if not href.startswith("http"):
                href = f"{self.BASE_URL}{href}"

            # Extract estate ID
            eid_match = re.search(r"/expose/(\w+)", href)
            eid = eid_match.group(1) if eid_match else ""

            results.append({"url": href, "id": eid})

        # Also try extracting from embedded JSON/Next.js data
        if not results:
            results = self._parse_nextjs_data(soup)

        return results

    def _parse_nextjs_data(self, soup: BeautifulSoup) -> list[dict]:
        """Immowelt may use Next.js with __NEXT_DATA__."""
        results = []
        script = soup.select_one("script#__NEXT_DATA__")
        if script and script.string:
            try:
                data = json.loads(script.string)
                # Navigate typical Next.js structure
                props = data.get("props", {}).get("pageProps", {})
                estates = props.get("estates", props.get("results", []))
                if isinstance(estates, list):
                    for estate in estates:
                        eid = estate.get("id", estate.get("estateId", ""))
                        if eid:
                            results.append({
                                "url": f"{self.BASE_URL}/expose/{eid}",
                                "id": str(eid),
                                "data": estate,  # Pre-parsed data
                            })
            except (json.JSONDecodeError, TypeError):
                pass
        return results

    def parse_listing_detail(self, html: str, url: str) -> Listing | None:
        soup = BeautifulSoup(html, "lxml")

        # Try Next.js data first
        listing = self._parse_from_nextjs(soup, url)
        if listing:
            return listing

        # HTML parsing fallback
        return self._parse_html_detail(soup, url)

    def _parse_from_nextjs(self, soup: BeautifulSoup, url: str) -> Listing | None:
        """Parse from __NEXT_DATA__ script."""
        script = soup.select_one("script#__NEXT_DATA__")
        if not script or not script.string:
            return None

        try:
            data = json.loads(script.string)
            estate = data.get("props", {}).get("pageProps", {}).get("estate", {})
            if not estate:
                return None

            eid = estate.get("id", estate.get("estateId", ""))
            listing_id = f"immowelt_{eid}" if eid else f"immowelt_{hash(url)}"

            price = float(estate.get("price", {}).get("value", 0))
            if not price:
                return None

            size = float(estate.get("areas", {}).get("livingArea", {}).get("value", 0))
            if not size:
                return None

            address_data = estate.get("address", {})
            address = f"{address_data.get('street', '')} {address_data.get('houseNumber', '')}, {address_data.get('zipCode', '')} {address_data.get('city', '')}".strip()
            zip_code = str(address_data.get("zipCode", ""))

            rooms = float(estate.get("rooms", {}).get("numberOfRooms", 0))
            year_built = estate.get("constructionYear")

            hausgeld = None
            costs = estate.get("costs", {})
            if "maintenanceCosts" in costs:
                hausgeld = float(costs["maintenanceCosts"].get("value", 0))

            return Listing(
                id=listing_id,
                platform="immowelt",
                url=url,
                title=estate.get("title", ""),
                price=price,
                size_sqm=size,
                rooms=rooms,
                address=address,
                district=detect_district(address, zip_code),
                zip_code=zip_code,
                year_built=int(year_built) if year_built else None,
                hausgeld=hausgeld,
            )
        except (json.JSONDecodeError, TypeError, ValueError, KeyError):
            return None

    def _parse_html_detail(self, soup: BeautifulSoup, url: str) -> Listing | None:
        """Fallback HTML parsing for Immowelt expose pages."""
        eid_match = re.search(r"/expose/(\w+)", url)
        listing_id = f"immowelt_{eid_match.group(1)}" if eid_match else f"immowelt_{hash(url)}"

        title_el = soup.select_one("h1")
        title = title_el.get_text(strip=True) if title_el else ""

        # Price
        price = None
        for selector in ["[data-test='price']", ".hardfact:has(.hardfact__label:contains('Kaufpreis'))"]:
            try:
                el = soup.select_one(selector)
                if el:
                    price = clean_price(el.get_text())
                    if price:
                        break
            except Exception:
                continue

        if not price:
            for el in soup.find_all(string=re.compile(r"Kaufpreis")):
                parent = el.find_parent()
                if parent:
                    price = clean_price(parent.get_text())
                    if price and price > 10000:
                        break

        if not price:
            return None

        # Size
        size = None
        for el in soup.find_all(string=re.compile(r"Wohnfläche|Fläche")):
            parent = el.find_parent()
            if parent:
                size = clean_size(parent.get_text())
                if size:
                    break

        if not size:
            return None

        # Rooms
        rooms = 0.0
        for el in soup.find_all(string=re.compile(r"Zimmer")):
            parent = el.find_parent()
            if parent:
                r = clean_rooms(parent.get_text())
                if r:
                    rooms = r
                    break

        # Address
        addr_el = soup.select_one("[data-test='address'], .location")
        address = addr_el.get_text(strip=True) if addr_el else ""
        zip_match = re.search(r"\b(\d{5})\b", address)
        zip_code = zip_match.group(1) if zip_match else ""

        # Description
        desc_el = soup.select_one("[data-test='description'], .section_objectDescription")
        description = desc_el.get_text(strip=True)[:2000] if desc_el else None

        page_text = soup.get_text().lower()

        return Listing(
            id=listing_id,
            platform="immowelt",
            url=url,
            title=title,
            price=price,
            size_sqm=size,
            rooms=rooms,
            address=address,
            district=detect_district(address, zip_code),
            zip_code=zip_code,
            balcony="balkon" in page_text,
            garden="garten" in page_text,
            parking=any(w in page_text for w in ("stellplatz", "garage", "parkplatz")),
            description=description,
        )

    def close(self):
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        super().close()
