"""Scraping utilities: user-agent rotation, rate limiting, text parsing."""

from __future__ import annotations

import re
import time
import random
import logging
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]


def get_random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def get_headers() -> dict[str, str]:
    return {
        "User-Agent": get_random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }


class RateLimiter:
    """Simple rate limiter with configurable delay."""

    def __init__(self, min_delay: float = 2.0, max_delay: float = 5.0):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._last_request: float = 0

    def wait(self):
        elapsed = time.time() - self._last_request
        delay = random.uniform(self.min_delay, self.max_delay)
        if elapsed < delay:
            sleep_time = delay - elapsed
            logger.debug(f"Rate limiting: sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)
        self._last_request = time.time()


_robots_cache: dict[str, RobotFileParser] = {}


def can_fetch(url: str) -> bool:
    """Check robots.txt for the given URL. Cached per domain."""
    parsed = urlparse(url)
    domain = f"{parsed.scheme}://{parsed.netloc}"

    if domain not in _robots_cache:
        rp = RobotFileParser()
        robots_url = f"{domain}/robots.txt"
        try:
            rp.set_url(robots_url)
            rp.read()
            _robots_cache[domain] = rp
        except Exception:
            logger.warning(f"Could not fetch robots.txt for {domain}")
            return True  # Allow if robots.txt unavailable

    return _robots_cache[domain].can_fetch("*", url)


def clean_price(text: str) -> float | None:
    """Parse German price format: '250.000 EUR' or '250.000 €' -> 250000.0"""
    if not text:
        return None
    # Remove currency symbols and whitespace
    cleaned = re.sub(r"[€EUR\s]", "", text.strip())
    # Handle German number format: 250.000,50 -> 250000.50
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    elif "." in cleaned:
        # Check if dot is thousand separator (e.g., "250.000")
        # German format: digits.3digits -> thousand sep
        parts = cleaned.split(".")
        if len(parts) == 2 and len(parts[1]) == 3 and parts[1].isdigit():
            cleaned = cleaned.replace(".", "")
        elif len(parts) > 2:
            # Multiple dots = thousand separators
            cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def clean_size(text: str) -> float | None:
    """Parse size: '75,5 m²' -> 75.5"""
    if not text:
        return None
    cleaned = re.sub(r"[m²qm\s]", "", text.strip())
    cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def clean_rooms(text: str) -> float | None:
    """Parse rooms: '3,5 Zimmer' -> 3.5"""
    if not text:
        return None
    match = re.search(r"(\d+[,.]?\d*)", text)
    if match:
        val = match.group(1).replace(",", ".")
        try:
            return float(val)
        except ValueError:
            return None
    return None


def fetch_page(url: str, session: requests.Session | None = None, timeout: int = 30) -> str | None:
    """Fetch a page with proper headers. Returns HTML or None on error."""
    requester = session or requests
    try:
        resp = requester.get(url, headers=get_headers(), timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return None


def detect_district(address: str, zip_code: str = "") -> str:
    """Map an address or zip code to a Hamburg district (Bezirk)."""
    # Complete Hamburg zip-to-district mapping (all Hamburg PLZ)
    zip_to_district = {
        # Hamburg-Mitte
        "20038": "Hamburg-Mitte", "20095": "Hamburg-Mitte", "20097": "Hamburg-Mitte",
        "20099": "Hamburg-Mitte",
        "20148": "Hamburg-Mitte",  # Rotherbaum (shared with Eimsbüttel)
        "20354": "Hamburg-Mitte", "20355": "Hamburg-Mitte",
        "20359": "Hamburg-Mitte",  # St. Pauli
        "20457": "Hamburg-Mitte", "20459": "Hamburg-Mitte",  # HafenCity
        "20535": "Hamburg-Mitte", "20537": "Hamburg-Mitte", "20539": "Hamburg-Mitte",  # Hamm/Rothenburgsort
        "21107": "Hamburg-Mitte", "21109": "Hamburg-Mitte",  # Wilhelmsburg/Veddel
        "22111": "Hamburg-Mitte", "22113": "Hamburg-Mitte",  # Billstedt/Horn
        "22115": "Hamburg-Mitte", "22117": "Hamburg-Mitte", "22119": "Hamburg-Mitte",  # Billstedt/Öjendorf
        # Altona
        "22523": "Altona",  # Eidelstedt (Bezirk Eimsbüttel, but sometimes Altona)
        "22525": "Altona",  # Stellingen
        "22527": "Altona",  # Stellingen
        "22547": "Altona",  # Lurup
        "22549": "Altona",  # Lurup
        "22559": "Altona",  # Rissen
        "22587": "Altona",  # Blankenese
        "22589": "Altona",  # Iserbrook
        "22605": "Altona",  # Othmarschen
        "22607": "Altona",  # Bahrenfeld/Groß Flottbek
        "22609": "Altona",  # Osdorf
        "22761": "Altona",  # Bahrenfeld
        "22763": "Altona",  # Ottensen
        "22765": "Altona",  # Ottensen/Altona-Altstadt
        "22767": "Altona",  # Altona-Altstadt
        "22769": "Altona",  # Altona-Nord/Sternschanze
        # Eimsbüttel
        "20144": "Eimsbüttel", "20146": "Eimsbüttel",
        "20249": "Eimsbüttel", "20251": "Eimsbüttel", "20253": "Eimsbüttel",
        "20255": "Eimsbüttel", "20257": "Eimsbüttel", "20259": "Eimsbüttel",
        "20357": "Eimsbüttel",
        "22453": "Eimsbüttel",  # Niendorf
        "22455": "Eimsbüttel",  # Niendorf
        "22457": "Eimsbüttel",  # Schnelsen
        "22459": "Eimsbüttel",  # Niendorf
        "22523": "Eimsbüttel",  # Eidelstedt
        "22525": "Eimsbüttel",  # Stellingen (overlap)
        "22527": "Eimsbüttel",  # Stellingen
        "22529": "Eimsbüttel",  # Lokstedt
        # Hamburg-Nord
        "22083": "Hamburg-Nord", "22085": "Hamburg-Nord",  # Uhlenhorst/Barmbek-Süd
        "22087": "Hamburg-Nord",  # Hohenfelde
        "22089": "Hamburg-Nord",  # Eilbek (Bezirk Wandsbek, but Nord border)
        "20249": "Hamburg-Nord",  # Eppendorf (shared)
        "22177": "Hamburg-Nord",  # Bramfeld-Nord
        "22297": "Hamburg-Nord",  # Alsterdorf/Winterhude
        "22299": "Hamburg-Nord",  # Winterhude
        "22301": "Hamburg-Nord",  # Barmbek-Nord
        "22303": "Hamburg-Nord",  # Winterhude
        "22305": "Hamburg-Nord",  # Barmbek-Nord
        "22307": "Hamburg-Nord",  # Barmbek-Nord
        "22309": "Hamburg-Nord",  # Steilshoop
        "22335": "Hamburg-Nord",  # Fuhlsbüttel
        "22337": "Hamburg-Nord",  # Ohlsdorf
        "22339": "Hamburg-Nord",  # Hummelsbüttel
        "22391": "Hamburg-Nord",  # Wellingsbüttel
        "22393": "Hamburg-Nord",  # Sasel (Bezirk Wandsbek, but usually counted Nord)
        "22395": "Hamburg-Nord",  # Bergstedt
        "22397": "Hamburg-Nord",  # Duvenstedt
        "22399": "Hamburg-Nord",  # Poppenbüttel
        "22413": "Hamburg-Nord",  # Langenhorn
        "22415": "Hamburg-Nord",  # Langenhorn
        "22417": "Hamburg-Nord",  # Langenhorn
        "22419": "Hamburg-Nord",  # Langenhorn
        # Wandsbek
        "22041": "Wandsbek",  # Wandsbek-Kern
        "22043": "Wandsbek",  # Tonndorf
        "22045": "Wandsbek",  # Tonndorf/Jenfeld
        "22047": "Wandsbek",  # Wandsbek/Tonndorf
        "22049": "Wandsbek",  # Dulsberg/Wandsbek
        "22081": "Wandsbek",  # Barmbek-Süd (Bezirk Wandsbek side)
        "22143": "Wandsbek",  # Rahlstedt
        "22145": "Wandsbek",  # Meiendorf
        "22147": "Wandsbek",  # Rahlstedt
        "22149": "Wandsbek",  # Rahlstedt
        "22159": "Wandsbek",  # Farmsen-Berne
        "22175": "Wandsbek",  # Bramfeld
        "22177": "Wandsbek",  # Bramfeld (overlap with Nord)
        "22179": "Wandsbek",  # Bramfeld
        "22359": "Wandsbek",  # Volksdorf
        "22391": "Wandsbek",  # Wellingsbüttel (overlap)
        "22393": "Wandsbek",  # Sasel (overlap)
        # Bergedorf
        "21029": "Bergedorf", "21031": "Bergedorf", "21033": "Bergedorf",
        "21035": "Bergedorf", "21037": "Bergedorf", "21039": "Bergedorf",
        # Harburg
        "21071": "Harburg", "21073": "Harburg", "21075": "Harburg",
        "21077": "Harburg", "21079": "Harburg",
        "21149": "Harburg",  # Neugraben-Fischbek
    }

    # Try zip code first
    if zip_code and zip_code in zip_to_district:
        return zip_to_district[zip_code]

    # Try extracting zip from address
    match = re.search(r"\b(\d{5})\b", address)
    if match and match.group(1) in zip_to_district:
        return zip_to_district[match.group(1)]

    # Try district name in address
    district_names = [
        "Altona", "Eimsbüttel", "Hamburg-Mitte", "Hamburg-Nord",
        "Wandsbek", "Bergedorf", "Harburg",
        "Ottensen", "Eppendorf", "Winterhude", "Barmbek",
        "Eilbek", "St. Pauli", "St. Georg", "HafenCity",
        "Wilhelmsburg", "Volksdorf", "Blankenese",
    ]
    sub_to_bezirk = {
        "Ottensen": "Altona", "Blankenese": "Altona",
        "Eppendorf": "Hamburg-Nord", "Winterhude": "Hamburg-Nord",
        "Barmbek": "Hamburg-Nord",
        "Eilbek": "Wandsbek", "Volksdorf": "Wandsbek",
        "St. Pauli": "Hamburg-Mitte", "St. Georg": "Hamburg-Mitte",
        "HafenCity": "Hamburg-Mitte", "Wilhelmsburg": "Hamburg-Mitte",
    }

    for name in district_names:
        if name.lower() in address.lower():
            return sub_to_bezirk.get(name, name)

    return "Hamburg"  # fallback
