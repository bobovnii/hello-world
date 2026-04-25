"""City registry for multi-city scraping (CITY-SUPPORT-1).

Minimal registry that maps a lowercase city slug (used in YAML) to the
per-platform tokens the existing scrapers need to fetch listings for that
city. Intentionally tiny — district detection, market data, and the
generalised scraper framework are out of scope for this iteration.

Adding a city = adding one entry to ``CITIES``. No other code change is
required as long as the new city's URL patterns match the four supported
platforms (immoscout / immowelt / kleinanzeigen / ohne-makler).

Caveats / known gaps for non-Hamburg cities:
- ``market_data.detect_district`` is Hamburg-only, so districts on
  Berlin/Dresden/Heide listings will be empty or wrong.
- The yield/price-vs-market figures fall back to the city-wide average
  defined in ``HamburgMarketData`` regardless of the listing's actual
  city, which means non-Hamburg signals are noisy.
- These are *expected* gaps for this iteration and surfaced deliberately
  so the cross-region digest can stress-test the format.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CityInfo:
    """Per-city scraping tokens.

    All fields are required except ``immoscout_geocodes``, which is
    optional because not every city has a verified IS24 mobile-API
    geocode. When omitted, the scraper falls back to a radius search
    centred on (lat, lon) plus the post-fetch ``zip_ranges`` filter.
    """

    slug: str  # "hamburg" — lowercase, used in YAML
    immowelt_path: str  # path slug for immowelt URL, e.g. "hamburg"
    kleinanzeigen_path: str  # path slug for kleinanzeigen URL, e.g. "hamburg"
    kleinanzeigen_location_id: str  # e.g. "l9409" — REQUIRED for kleinanzeigen URL
    ohne_makler_path: str  # path under /immobilien/{X}/, e.g. "hamburg/hamburg" or "schleswig-holstein/heide"
    zip_ranges: list[tuple[int, int]]  # ImmoScout postal-code filter, inclusive
    # ImmoScout mobile API: (lat, lon, radius_km). The existing scraper does
    # a radius search around these coords; the post-fetch zip_ranges filter
    # then narrows to in-city listings.
    geocode_radius: tuple[float, float, float]
    # Optional list of tokens to *also* substring-match against the address
    # (lowercased) when the address has no zip and we want to keep it. The
    # primary check is zip_ranges; this is just the "city name appears in
    # address" fallback that the Hamburg scraper used to do hard-coded.
    address_tokens: list[str] = field(default_factory=list)
    # Surrounding-city suburbs to EXCLUDE when zip ranges overlap. Hamburg
    # needs this because Norderstedt/Pinneberg/etc share zip prefixes.
    # Berlin/Dresden/Heide don't currently have known overlaps, so empty
    # lists are fine — we can tighten later if false positives appear.
    exclude_address_tokens: list[str] = field(default_factory=list)


# Hamburg geocode comes from the existing immoscout.py constants
# (HAMBURG_LAT, HAMBURG_LNG, HAMBURG_RADIUS_KM). Berlin/Dresden/Heide
# values are picked manually from city centres; the radius is generous
# enough to cover the full city plus suburbs we'll then filter by zip.
CITIES: dict[str, CityInfo] = {
    "hamburg": CityInfo(
        slug="hamburg",
        immowelt_path="hamburg",
        kleinanzeigen_path="hamburg",
        kleinanzeigen_location_id="l9409",
        ohne_makler_path="hamburg/hamburg",
        zip_ranges=[(20000, 22769), (21029, 21149)],
        geocode_radius=(53.5511, 9.9937, 20.0),
        address_tokens=["hamburg"],
        exclude_address_tokens=[
            "norderstedt", "seevetal", "reinbek", "pinneberg",
            "ahrensburg", "schenefeld", "wedel", "glinde",
            "barsbüttel", "oststeinbek", "halstenbek", "rellingen",
            "tangstedt", "henstedt", "quickborn", "elmshorn",
            "geesthacht", "lauenburg", "wentorf", "aumühle",
            "börnsen", "escheburg", "stelle", "winsen",
        ],
    ),
    "berlin": CityInfo(
        slug="berlin",
        immowelt_path="berlin",
        kleinanzeigen_path="berlin",
        # l3331 = Berlin (verified from kleinanzeigen URL pattern
        # /berlin/c<cat>l3331). Used by every Berlin sale ad.
        kleinanzeigen_location_id="l3331",
        ohne_makler_path="berlin/berlin",
        zip_ranges=[(10115, 14199)],
        # Berlin centre (Brandenburg Gate ~52.5163, 13.3777). 25 km
        # radius covers the whole city.
        geocode_radius=(52.5200, 13.4050, 25.0),
        address_tokens=["berlin"],
    ),
    "dresden": CityInfo(
        slug="dresden",
        immowelt_path="dresden",
        kleinanzeigen_path="dresden",
        # l4030 = Dresden (verified from kleinanzeigen URL pattern).
        kleinanzeigen_location_id="l4030",
        ohne_makler_path="sachsen/dresden",
        # Dresden zips are 01067-01328. The leading zero matters for the
        # int comparison: int("01067") == 1067, so the ranges below are
        # numerically correct (1067, 1328). We compare zip codes parsed
        # via ``int()`` in immoscout.py, so this works.
        zip_ranges=[(1067, 1328)],
        # Dresden centre. 15 km covers the city + close suburbs.
        geocode_radius=(51.0504, 13.7373, 15.0),
        address_tokens=["dresden"],
    ),
    "heide": CityInfo(
        slug="heide",
        immowelt_path="heide",
        kleinanzeigen_path="heide",
        # l1814 = Kreis Dithmarschen on kleinanzeigen. Heide itself
        # doesn't have a town-level location ID; the district-level ID is
        # the closest match. UNCERTAINTY: not independently verified end-
        # to-end; if Heide returns 0 listings the URL might need a
        # different ID (e.g. Kreis Dithmarschen could be l1809 or similar
        # depending on the kleinanzeigen taxonomy).
        kleinanzeigen_location_id="l1814",
        # Ohne-Makler URL structure is /immobilien/{Bundesland}/{Stadt}/.
        # Heide is in Schleswig-Holstein.
        ohne_makler_path="schleswig-holstein/heide",
        zip_ranges=[(25746, 25746)],
        # Heide town centre. Small town, so 10 km radius covers it.
        geocode_radius=(54.1953, 9.0958, 10.0),
        address_tokens=["heide"],
    ),
}


def get_city(slug: str) -> CityInfo:
    """Return the CityInfo for a slug (case-insensitive). ValueError if unknown."""
    if not slug:
        raise ValueError("city slug is empty")
    key = slug.lower().strip()
    info = CITIES.get(key)
    if info is None:
        raise ValueError(
            f"unknown city slug {slug!r}; known: {sorted(CITIES.keys())}"
        )
    return info
