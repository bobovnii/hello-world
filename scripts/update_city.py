#!/usr/bin/env python3
"""CLI tool for updating per-city market data JSON files.

Usage:

    # Update one district
    update_city.py --city berlin --district Lichtenberg \
        --price-sqm 5100 --rent-sqm 12.5 --yield-pct 3.6 \
        --trend rising --source "ImmoScout24 Q2 2025"

    # Update city-wide defaults
    update_city.py --city berlin --set-default \
        --price-sqm 5400 --rent-sqm 13.0 --yield-pct 3.5

    # Print a JSON diff but do not write
    update_city.py --city berlin --district Pankow \
        --price-sqm 5800 --rent-sqm 14.0 --yield-pct 3.4 \
        --trend stable --source "Manual Q2 2025 estimate" --dry-run

    # List known cities + their last-update info
    update_city.py --list

Atomic write: writes to ``<file>.tmp`` then ``os.replace``. A ``.bak``
copy of the previous file is kept so a corrupt write can be rolled back.

After every successful write the tool re-loads the file via
``MarketRegistry``. If the parsed result is invalid the .bak is
restored and the script exits non-zero.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

# Make ``src.*`` importable when invoked as ``python scripts/update_city.py``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analyzer.market_registry import MarketRegistry  # noqa: E402


_REPO_ROOT = Path(__file__).resolve().parents[1]
_CITIES_DIR = _REPO_ROOT / "data" / "cities"

# Range guards. Reject values outside these ranges so a typo can't
# silently corrupt the price baseline.
_PRICE_RANGE = (500.0, 25_000.0)
_RENT_RANGE = (4.0, 40.0)
_YIELD_RANGE = (0.0, 15.0)
_VALID_TRENDS = ("rising", "stable", "declining")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _city_path(city: str, base_dir: Path = _CITIES_DIR) -> Path:
    candidate = (base_dir / f"{city.lower()}.json").resolve()
    base = base_dir.resolve()
    # Defensive: refuse a slug like "../foo" that would resolve outside the
    # cities dir. The resolved candidate must live directly under base_dir.
    if candidate.parent != base:
        raise SystemExit(f"refusing city slug {city!r}: path escapes {base}")
    return candidate


def _load_json(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def _dump_json(data: dict[str, Any]) -> str:
    """Stable, human-friendly JSON encoding used for both diffs and writes."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _check_range(name: str, value: float, low: float, high: float) -> None:
    if not (low <= value <= high):
        raise SystemExit(
            f"{name}={value} out of range [{low}, {high}]"
        )


def _atomic_write(path: Path, content: str) -> Path:
    """Write ``content`` to ``path`` atomically, keeping a ``.bak`` of the
    previous file so a downstream validation failure can roll back.
    Returns the path of the backup (None if the file didn't exist).
    """
    bak: Path | None = None
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        f.write(content)
    os.replace(tmp, path)
    return bak


def _validate_via_registry(city: str, base_dir: Path = _CITIES_DIR) -> None:
    """Re-load ``city`` via MarketRegistry to confirm the JSON parses.

    Raises ``ValueError`` (re-raised by MarketRegistry) if anything is
    wrong with the structure.
    """
    reg = MarketRegistry(base_dir)
    market = reg.get(city)
    # Touch a few fields that the digest reads — explicit smoke test
    # that the new values aren't None / missing keys.
    _ = market.city_default_price_sqm
    _ = market.city_default_rent_sqm
    _ = market.city_default_yield
    _ = market.purchase_costs_breakdown


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------


def cmd_list(base_dir: Path = _CITIES_DIR) -> int:
    if not base_dir.exists():
        print(f"no cities dir at {base_dir}")
        return 1
    rows: list[tuple[str, int, int, float]] = []
    for path in sorted(base_dir.glob("*.json")):
        try:
            data = _load_json(path)
        except Exception as e:
            print(f"{path.stem}: ERROR reading file: {e}")
            continue
        districts = data.get("districts") or {}
        years: list[int] = []
        for d in districts.values():
            y = d.get("as_of_year")
            if isinstance(y, int):
                years.append(y)
        oldest = min(years) if years else 0
        avg_age = (
            sum(_dt.date.today().year - y for y in years) / len(years)
            if years
            else 0.0
        )
        rows.append((path.stem, len(districts), oldest, avg_age))

    # Plain text table — keeps the output greppable from a shell pipeline.
    print(f"{'city':<12} {'districts':>9} {'oldest':>7} {'avg_age':>8}")
    for slug, n, oldest, age in rows:
        oldest_s = str(oldest) if oldest else "-"
        print(f"{slug:<12} {n:>9} {oldest_s:>7} {age:>8.1f}")
    return 0


def _update_district(
    data: dict[str, Any],
    district: str,
    price_sqm: float,
    rent_sqm: float,
    yield_pct: float,
    trend: str,
    source: str,
    today_year: int,
) -> dict[str, Any]:
    """Return a NEW dict with the district updated. Never mutates ``data``."""
    new = json.loads(json.dumps(data))  # deep copy via JSON round-trip
    new.setdefault("districts", {})
    existing = new["districts"].get(district, {})
    existing.update(
        {
            "avg_price_sqm_buy": price_sqm,
            "avg_rent_sqm_month": rent_sqm,
            "avg_gross_yield_pct": yield_pct,
            "trend": trend,
            "source": source,
            "as_of_year": today_year,
        }
    )
    new["districts"][district] = existing
    return new


def _set_default(
    data: dict[str, Any],
    price_sqm: float,
    rent_sqm: float,
    yield_pct: float,
) -> dict[str, Any]:
    new = json.loads(json.dumps(data))
    new["city_default_price_sqm"] = price_sqm
    new["city_default_rent_sqm"] = rent_sqm
    new["city_default_yield"] = yield_pct
    return new


def _diff_lines(before: dict[str, Any], after: dict[str, Any], filename: str) -> str:
    a = _dump_json(before).splitlines(keepends=True)
    b = _dump_json(after).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            a, b,
            fromfile=f"{filename} (before)",
            tofile=f"{filename} (after)",
            n=2,
        )
    )


def cmd_update(args: argparse.Namespace, base_dir: Path = _CITIES_DIR) -> int:
    city = args.city.lower().strip()
    path = _city_path(city, base_dir)
    if not path.exists():
        print(f"city file not found: {path}", file=sys.stderr)
        return 2

    data = _load_json(path)

    set_default = bool(args.set_default)
    is_district_update = bool(args.district)

    # Mutual-exclusion: at least one of --district or --set-default must
    # be supplied; combining them is not currently supported (would muddy
    # the diff output and the source-required rule).
    if not set_default and not is_district_update:
        print(
            "either --district DISTRICT (with values+source) or --set-default required",
            file=sys.stderr,
        )
        return 2

    if set_default:
        if (
            args.price_sqm is None
            or args.rent_sqm is None
            or args.yield_pct is None
        ):
            print(
                "--set-default requires --price-sqm, --rent-sqm, --yield-pct",
                file=sys.stderr,
            )
            return 2
        _check_range("price-sqm", args.price_sqm, *_PRICE_RANGE)
        _check_range("rent-sqm", args.rent_sqm, *_RENT_RANGE)
        _check_range("yield-pct", args.yield_pct, *_YIELD_RANGE)
        new_data = _set_default(data, args.price_sqm, args.rent_sqm, args.yield_pct)
    else:
        # District update: every value is required, AND --source is
        # mandatory. Refusing to write without a source is the easiest
        # way to keep the data-quality story honest after the fact.
        for missing in ("price_sqm", "rent_sqm", "yield_pct", "trend"):
            if getattr(args, missing) is None:
                print(f"--district update requires --{missing.replace('_','-')}",
                      file=sys.stderr)
                return 2
        if not args.source:
            print(
                "--source is required when updating a district value",
                file=sys.stderr,
            )
            return 2
        if args.trend not in _VALID_TRENDS:
            print(
                f"--trend must be one of {_VALID_TRENDS}, got {args.trend!r}",
                file=sys.stderr,
            )
            return 2
        _check_range("price-sqm", args.price_sqm, *_PRICE_RANGE)
        _check_range("rent-sqm", args.rent_sqm, *_RENT_RANGE)
        _check_range("yield-pct", args.yield_pct, *_YIELD_RANGE)
        new_data = _update_district(
            data,
            district=args.district,
            price_sqm=args.price_sqm,
            rent_sqm=args.rent_sqm,
            yield_pct=args.yield_pct,
            trend=args.trend,
            source=args.source,
            today_year=_dt.date.today().year,
        )

    diff_text = _diff_lines(data, new_data, path.name)
    if not diff_text:
        print("no changes (values identical)")
        return 0

    if args.dry_run:
        print(diff_text)
        return 0

    bak = _atomic_write(path, _dump_json(new_data))
    try:
        _validate_via_registry(city, base_dir)
    except Exception as e:
        # Roll back from .bak
        if bak and bak.exists():
            shutil.copy2(bak, path)
        print(f"validation failed after write, rolled back: {e}", file=sys.stderr)
        return 3

    print(f"wrote {path}")
    print(diff_text)
    return 0


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Update per-city market data files in data/cities/.",
    )
    p.add_argument("--list", action="store_true", help="List cities and exit")
    p.add_argument("--city", help="city slug (e.g. hamburg, berlin)")
    p.add_argument("--district", help="district name to update")
    p.add_argument(
        "--set-default",
        action="store_true",
        help="update city_default_* fields instead of a district",
    )
    p.add_argument("--price-sqm", type=float, help="avg buy price per m² (EUR)")
    p.add_argument("--rent-sqm", type=float, help="avg monthly rent per m² (EUR)")
    p.add_argument("--yield-pct", type=float, help="avg gross rental yield (%%)")
    p.add_argument(
        "--trend",
        choices=_VALID_TRENDS,
        help="market trend label",
    )
    p.add_argument("--source", help="citation for the new values (required for districts)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print JSON diff and exit without writing",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        return cmd_list()
    if not args.city:
        print("--city is required (or use --list)", file=sys.stderr)
        return 2
    return cmd_update(args)


if __name__ == "__main__":
    sys.exit(main())
