"""Kleinanzeigen.de (formerly eBay Kleinanzeigen) scraper for Hamburg real estate."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString

from src.database.models import Listing, UserCriteria
from .base import BaseScraper
from .utils import clean_price, clean_size, clean_rooms, detect_district


class KleinanzeigenScraper(BaseScraper):
    PLATFORM_NAME = "kleinanzeigen"
    BASE_URL = "https://www.kleinanzeigen.de"

    # Kleinanzeigen location ID for Hamburg
    HAMBURG_LOCATION_ID = "l9409"

    def build_search_url(self, criteria: UserCriteria, page: int = 1) -> str:
        # Categories:
        #   c196 = Eigentumswohnungen (apartments for sale)
        #   c208 = Wohnung kaufen (all property types incl. houses & MFH)
        #   c209 = Häuser kaufen (houses for sale)
        prop_type = criteria.property_types[0] if criteria.property_types else "apartment"
        if prop_type == "apartment":
            category = "c196"
            path_segment = "s-eigentumswohnung"
        elif prop_type == "house":
            category = "c209"
            path_segment = "s-haus-kaufen"
        elif prop_type == "multi_family":
            # c208 covers MFH/Zinshäuser; filter by price range to get buy listings
            category = "c208"
            path_segment = "s-wohnung-kaufen"
        else:
            category = "c208"
            path_segment = "s-wohnung-kaufen"

        # Use location ID to restrict to Hamburg
        base = f"{self.BASE_URL}/{path_segment}/hamburg/{category}{self.HAMBURG_LOCATION_ID}"

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

        # Kleinanzeigen uses article.aditem with data-adid
        for item in soup.select("article.aditem"):
            ad_id = item.get("data-adid", "")
            link = item.select_one("a[href*='/s-anzeige/']")
            if not link:
                continue

            href = link.get("href", "")
            if not href.startswith("http"):
                href = f"{self.BASE_URL}{href}"

            results.append({"url": href, "id": ad_id})

        # Fallback: try list items
        if not results:
            for item in soup.select("li.ad-listitem"):
                link = item.select_one("a[href*='/s-anzeige/']")
                if not link:
                    continue
                href = link.get("href", "")
                if not href.startswith("http"):
                    href = f"{self.BASE_URL}{href}"
                ad_id = item.get("data-adid", "")
                results.append({"url": href, "id": ad_id})

        return results

    def parse_listing_detail(self, html: str, url: str) -> Listing | None:
        soup = BeautifulSoup(html, "lxml")

        # Extract ad ID from URL
        lid_match = re.search(r"/(\d+)-", url)
        listing_id = f"kleinanzeigen_{lid_match.group(1)}" if lid_match else f"kleinanzeigen_{hash(url)}"

        # Title
        title_el = soup.select_one("h1#viewad-title, h1")
        title = title_el.get_text(strip=True) if title_el else ""

        # Price (from #viewad-price)
        price = None
        price_el = soup.select_one("#viewad-price")
        if price_el:
            price = clean_price(price_el.get_text())

        if not price:
            return None

        # Parse attribute details from .addetailslist--detail items
        # Structure: <li class="addetailslist--detail">LabelText<span class="addetailslist--detail--value">Value</span></li>
        details = self._parse_details(soup)

        size = details.get("size")
        rooms = details.get("rooms", 0)

        if not size:
            return None

        # Address / Location
        addr_el = soup.select_one("#viewad-locality")
        address = addr_el.get_text(strip=True) if addr_el else ""
        zip_match = re.search(r"\b(\d{5})\b", address)
        zip_code = zip_match.group(1) if zip_match else ""

        # Description
        desc_el = soup.select_one("#viewad-description-text")
        description = desc_el.get_text(strip=True)[:2000] if desc_el else None

        # Detect MFH from title/description keywords
        combined_text = f"{title} {description or ''}".lower()
        mfh_keywords = [
            "mehrfamilienhaus", "mehrfamilien", "zinshaus", "renditeobjekt",
            "anlageimmobilie", "wohnanlage", "apartmenthaus", "mietshaus",
            "kapitalanlage", "wohneinheiten", "mieteinnahmen", "mietobjekt",
        ]
        if any(kw in combined_text for kw in mfh_keywords):
            details["property_type"] = "multi_family"

        # Features from page text
        page_text = ((description or "") + " " + soup.get_text()).lower()
        balcony = "balkon" in page_text
        garden = "garten" in page_text
        parking = any(w in page_text for w in ("stellplatz", "garage", "parkplatz"))

        # === Extract enriched fields for price justification ===
        combined = f"{title} {description or ''}".lower()

        # Erbbaurecht detection
        is_erbbaurecht = any(kw in combined for kw in ("erbbaurecht", "erbpacht", "erbbau"))

        # Rented / Kapitalanlage detection
        is_rented = any(kw in combined for kw in (
            "vermietet", "kapitalanlage", "mieteinnahmen", "aktuelle miete",
            "rendite", "anlageobjekt", "anlageimmobilie",
        ))

        # Extract actual rent amount if mentioned
        current_rent = None
        rent_match = re.search(
            r"(?:miete|mieteinnahmen|kaltmiete|nkm)[\s:]*(?:ca\.?\s*)?(?:€\s*)?([\d.,]+)\s*(?:€|eur|/mo)",
            combined,
        )
        if not rent_match:
            rent_match = re.search(r"(\d[\d.]*(?:,\d+)?)\s*(?:€|eur)\s*(?:kalt|netto|monatlich|/mon|p\.?\s*m)", combined)
        if rent_match:
            current_rent = clean_price(rent_match.group(1))
            if current_rent and current_rent > 5000:
                current_rent = None  # Likely parsed wrong

        # WBS / social housing
        is_wbs = any(kw in combined for kw in ("wbs", "wohnberechtigungsschein", "sozialbindung", "preisgebunden"))

        # Dachgeschoss
        is_dachgeschoss = any(kw in combined for kw in ("dachgeschoss", "dachschräge", "spitzboden", "mansarde"))

        # Ausbau needed
        is_ausbau = any(kw in combined for kw in ("ausbaureserve", "ausbaufähig", "rohbau", "ausbaupotenzial", "entwicklungspotenzial"))

        # Number of units in building
        num_units = None
        units_match = re.search(r"(\d+)\s*(?:wohneinheiten|wohnungen|einheiten|parteien|we\b)", combined)
        if units_match:
            num_units = int(units_match.group(1))

        # Images
        image_urls = []
        for img in soup.select("#viewad-image img, .galleryimage img"):
            src = img.get("data-src") or img.get("src", "")
            if src and "placeholder" not in src:
                image_urls.append(src)

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
            year_built=details.get("year_built"),
            property_type=details.get("property_type", "apartment"),
            hausgeld=details.get("hausgeld"),
            balcony=balcony,
            garden=garden,
            parking=parking,
            description=description,
            image_urls=image_urls[:10],
            is_erbbaurecht=is_erbbaurecht,
            is_rented=is_rented,
            current_rent_monthly=current_rent,
            is_wbs=is_wbs,
            is_dachgeschoss=is_dachgeschoss,
            is_ausbau_needed=is_ausbau,
            num_units_in_building=num_units,
        )

    def _parse_details(self, soup: BeautifulSoup) -> dict:
        """Extract structured details from Kleinanzeigen detail page.

        The HTML structure is:
        <li class="addetailslist--detail">
            Wohnfläche              <-- bare text node (label)
            <span class="addetailslist--detail--value">67,79 m²</span>
        </li>
        """
        details: dict = {}

        for item in soup.select(".addetailslist--detail"):
            # Get the value span
            value_el = item.select_one(".addetailslist--detail--value")
            if not value_el:
                continue
            value = value_el.get_text(strip=True)

            # Get the label from the bare text nodes (before the span)
            label_parts = []
            for child in item.children:
                if isinstance(child, NavigableString):
                    text = child.strip()
                    if text:
                        label_parts.append(text)
            label = " ".join(label_parts).lower()

            if "wohnfläche" in label or label == "fläche":
                details["size"] = clean_size(value)
            elif "grundstücksfläche" in label:
                details["plot_size"] = clean_size(value)  # Don't use as living area
            elif label == "zimmer":
                details["rooms"] = clean_rooms(value) or 0
            elif "baujahr" in label:
                match = re.search(r"(\d{4})", value)
                if match:
                    details["year_built"] = int(match.group(1))
            elif "hausgeld" in label:
                details["hausgeld"] = clean_price(value)
            elif "haustyp" in label or "wohnungstyp" in label or "art" in label:
                vl = value.lower()
                if any(w in vl for w in ("wohnung", "etagenwohnung", "apartment", "dachgeschoss", "erdgeschoss", "penthouse")):
                    details["property_type"] = "apartment"
                elif any(w in vl for w in ("haus", "einfamilienhaus", "reihenhaus", "doppelhaushälfte", "bungalow", "villa")):
                    details["property_type"] = "house"
                elif "mehrfamilien" in vl:
                    details["property_type"] = "multi_family"

        return details
