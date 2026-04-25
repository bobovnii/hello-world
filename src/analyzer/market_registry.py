"""Registry for per-city market data, loaded from ``data/cities/*.json``.

Replaces the single global ``HamburgMarketData()`` instance with a
slug-keyed registry. The Telegram digest constructs one ``MarketRegistry``
at startup, then resolves ``criteria.city`` to a per-city instance for
every search — so a Berlin Lichtenberg listing is benchmarked against
Berlin's €5 300/m² baseline, not Hamburg's €4 771/m².

Loaded once at construction (not lazy) so a missing or malformed JSON
fails fast at startup rather than mid-run.
"""

from __future__ import annotations

from pathlib import Path

from .market_data import CityMarketData


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CITIES_DIR = _REPO_ROOT / "data" / "cities"


class MarketRegistry:
    """Loads every ``data/cities/*.json`` and exposes ``.get(slug)``."""

    def __init__(self, cities_dir: Path | str | None = None):
        self._cities_dir = Path(cities_dir) if cities_dir else _DEFAULT_CITIES_DIR
        self._by_slug: dict[str, CityMarketData] = {}

        if not self._cities_dir.exists():
            # Legacy fallback: the only city we know about is Hamburg
            # via the old ``data/hamburg_market_data.json`` location.
            self._by_slug["hamburg"] = CityMarketData("hamburg")
            return

        for json_path in sorted(self._cities_dir.glob("*.json")):
            slug = json_path.stem.lower()
            try:
                self._by_slug[slug] = CityMarketData(slug)
            except Exception as e:
                # Re-raise with context so a corrupt JSON file doesn't
                # silently disappear behind a generic JSONDecodeError.
                raise ValueError(
                    f"failed to load city market data {json_path}: {e}"
                ) from e

        if "hamburg" not in self._by_slug:
            # Legacy Hamburg path is still around — load it so the
            # default ``criteria.city = "hamburg"`` keeps working even
            # if someone hasn't moved the file yet.
            try:
                self._by_slug["hamburg"] = CityMarketData("hamburg")
            except FileNotFoundError:
                pass

    @property
    def slugs(self) -> list[str]:
        return sorted(self._by_slug.keys())

    def get(self, city_slug: str) -> CityMarketData:
        """Resolve a slug to its ``CityMarketData``. ``ValueError`` on miss.

        The caller is the Telegram digest, which already validates that
        the slug exists in ``city_registry.CITIES`` before scraping. A
        miss here means the search YAML targets a city we have a scraper
        for but no market data — the right call is to fail loudly so
        the operator notices the gap, not to silently fall back to
        Hamburg.
        """
        if not city_slug:
            raise ValueError("city slug is empty")
        key = city_slug.lower().strip()
        if key not in self._by_slug:
            raise ValueError(
                f"unknown city slug {city_slug!r}; "
                f"known: {sorted(self._by_slug.keys())}"
            )
        return self._by_slug[key]
