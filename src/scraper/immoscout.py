"""ImmoScout24 scraper for Hamburg real estate listings.

ImmoScout24 uses AWS WAF with JavaScript challenges. This scraper uses
Playwright (headless Chromium) to bypass the bot protection. If Playwright
is unavailable or the challenge cannot be solved (e.g. datacenter IP),
it falls back gracefully and logs a warning.
"""

from __future__ import annotations

import re
import json
import time
import logging
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from src.database.models import Listing, UserCriteria
from .base import BaseScraper
from .utils import clean_price, clean_size, clean_rooms, detect_district

logger = logging.getLogger(__name__)


class ImmoScoutScraper(BaseScraper):
    PLATFORM_NAME = "immoscout"
    BASE_URL = "https://www.immobilienscout24.de"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pw = None
        self._browser = None
        self._use_playwright = True

    def _init_playwright(self) -> bool:
        """Initialize Playwright browser. Returns False if unavailable."""
        if self._browser:
            return True
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-blink-features=AutomationControlled"],
            )
            return True
        except Exception as e:
            logger.warning(f"Playwright not available: {e}")
            self._use_playwright = False
            return False

    def fetch(self, url: str) -> str | None:
        """Fetch using Playwright to handle AWS WAF challenge."""
        self.rate_limiter.wait()

        if self._use_playwright and self._init_playwright():
            return self._fetch_playwright(url)

        # Fallback to requests (will likely get 401 from datacenter IPs)
        html = super().fetch(url)
        if html and "Ich bin kein Roboter" not in html:
            return html

        logger.warning(
            "ImmoScout blocked this request (bot detection). "
            "Requires Playwright from a residential IP."
        )
        return None

    def _fetch_playwright(self, url: str) -> str | None:
        """Fetch page with Playwright, waiting for WAF challenge to resolve."""
        try:
            context = self._browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/125.0.0.0 Safari/537.36",
                locale="de-DE",
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()

            # Navigate (commit = don't wait for full load)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except Exception:
                # Timeout is OK, page may still be loading after challenge
                pass

            # Wait for the WAF challenge to resolve (polls for real content)
            html = None
            for attempt in range(10):
                time.sleep(2)
                try:
                    content = page.content()
                    if not content or len(content) < 100:
                        continue
                    if "Ich bin kein Roboter" in content or "challenge.js" in content:
                        continue
                    # Check for actual listing content
                    if "/expose/" in content or "resultListEntries" in content:
                        html = content
                        break
                except Exception:
                    continue

            page.close()
            context.close()

            if not html:
                logger.warning(
                    "ImmoScout WAF challenge not solved. This typically happens "
                    "on datacenter IPs. Try running from a residential connection."
                )
            return html

        except Exception as e:
            logger.warning(f"Playwright fetch failed: {e}")
            return None

    def build_search_url(self, criteria: UserCriteria, page: int = 1) -> str:
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
        if criteria.budget_min > 0 and criteria.budget_max < 1_000_000:
            params["price"] = f"{int(criteria.budget_min)}-{int(criteria.budget_max)}"
        elif criteria.budget_max < 1_000_000:
            params["price"] = f"-{int(criteria.budget_max)}"
        elif criteria.budget_min > 0:
            params["price"] = f"{int(criteria.budget_min)}-"
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
        """Parse ImmoScout search results."""
        soup = BeautifulSoup(html, "lxml")
        results = []

        # Method 1: data-id attributes on result items
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

        # Method 2: direct expose links
        if not results:
            seen = set()
            for link in soup.select("a[href*='/expose/']"):
                href = link.get("href", "")
                if not href.startswith("http"):
                    href = f"{self.BASE_URL}{href}"
                if href not in seen:
                    seen.add(href)
                    eid = re.search(r"/expose/(\d+)", href)
                    if eid:
                        results.append({"url": href, "id": eid.group(1)})

        # Method 3: embedded JSON data
        if not results:
            results = self._parse_from_scripts(soup)

        return results

    def _parse_from_scripts(self, soup: BeautifulSoup) -> list[dict]:
        results = []
        for script in soup.find_all("script"):
            text = script.string or ""
            if "resultListEntries" in text or "searchResponseModel" in text or "@id" in text:
                for match in re.finditer(r'"@id"\s*:\s*"(\d+)"', text):
                    listing_id = match.group(1)
                    results.append({
                        "url": f"{self.BASE_URL}/expose/{listing_id}",
                        "id": listing_id,
                    })
        return results

    def parse_listing_detail(self, html: str, url: str) -> Listing | None:
        """Parse a single ImmoScout expose page."""
        soup = BeautifulSoup(html, "lxml")

        listing = self._parse_json_ld(soup, url)
        if listing:
            return listing
        return self._parse_html_detail(soup, url)

    def _parse_json_ld(self, soup: BeautifulSoup, url: str) -> Listing | None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    data = next(
                        (d for d in data if d.get("@type") in
                         ("Residence", "Apartment", "House", "Product", "SingleFamilyResidence")),
                        None,
                    )
                if not data:
                    continue

                price = None
                if "offers" in data and isinstance(data["offers"], dict):
                    price = float(data["offers"].get("price", 0))
                if not price:
                    continue

                lid_match = re.search(r"/expose/(\d+)", url)
                listing_id = f"immoscout_{lid_match.group(1)}" if lid_match else f"immoscout_{hash(url)}"

                address_data = data.get("address", {})
                address = address_data.get("streetAddress", "") if isinstance(address_data, dict) else str(address_data)
                zip_code = address_data.get("postalCode", "") if isinstance(address_data, dict) else ""

                size = 0.0
                fs = data.get("floorSize")
                if isinstance(fs, dict):
                    size = float(fs.get("value", 0))

                return Listing(
                    id=listing_id,
                    platform="immoscout",
                    url=url,
                    title=data.get("name", ""),
                    price=price,
                    size_sqm=size,
                    rooms=float(data.get("numberOfRooms", 0)),
                    address=address,
                    zip_code=zip_code,
                    district=detect_district(address, zip_code),
                )
            except (json.JSONDecodeError, TypeError, ValueError, KeyError):
                continue
        return None

    def _parse_html_detail(self, soup: BeautifulSoup, url: str) -> Listing | None:
        lid_match = re.search(r"/expose/(\d+)", url)
        listing_id = f"immoscout_{lid_match.group(1)}" if lid_match else f"immoscout_{hash(url)}"

        title_el = soup.select_one("h1, #expose-title")
        title = title_el.get_text(strip=True) if title_el else ""

        price = None
        for selector in [".is24qa-kaufpreis", ".is24qa-preis", "[data-qa='kaufpreis']"]:
            el = soup.select_one(selector)
            if el:
                price = clean_price(el.get_text())
                if price:
                    break
        if not price:
            for el in soup.find_all(string=re.compile(r"[\d.]+\s*[€EUR]")):
                p = clean_price(str(el))
                if p and p > 10000:
                    price = p
                    break
        if not price:
            return None

        size = None
        for selector in [".is24qa-wohnflaeche-ca", ".is24qa-flaeche", "[data-qa='wohnflaeche']"]:
            el = soup.select_one(selector)
            if el:
                size = clean_size(el.get_text())
                if size:
                    break
        if not size:
            return None

        rooms = 0.0
        for selector in [".is24qa-zi", ".is24qa-zimmer", "[data-qa='zimmer']"]:
            el = soup.select_one(selector)
            if el:
                r = clean_rooms(el.get_text())
                if r:
                    rooms = r
                    break

        addr_el = soup.select_one(".address-block, .zip-region-and-country")
        address = addr_el.get_text(strip=True) if addr_el else ""
        zip_match = re.search(r"\b(\d{5})\b", address)
        zip_code = zip_match.group(1) if zip_match else ""

        hausgeld = None
        hg_el = soup.select_one(".is24qa-hausgeld")
        if hg_el:
            hausgeld = clean_price(hg_el.get_text())

        year_built = None
        yb_el = soup.select_one(".is24qa-baujahr")
        if yb_el:
            m = re.search(r"(\d{4})", yb_el.get_text())
            if m:
                year_built = int(m.group(1))

        condition = None
        cond_el = soup.select_one(".is24qa-objektzustand")
        if cond_el:
            condition = cond_el.get_text(strip=True)

        desc_el = soup.select_one(".is24qa-objektbeschreibung, #objectDescription")
        description = desc_el.get_text(strip=True)[:2000] if desc_el else None

        page_text = soup.get_text().lower()

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
            balcony="balkon" in page_text,
            garden="garten" in page_text,
            parking=any(w in page_text for w in ("stellplatz", "garage", "parkplatz")),
            description=description,
        )

    def close(self):
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
        super().close()
