#!/usr/bin/env python3
"""Send a daily real-estate deal digest to a Telegram channel.

Reads a YAML config listing one or more saved searches, runs each via the
existing scraper + analyzer, dedups against previously posted deals (by
channel), and posts the top N per search to the configured channel.

Usage:
    # Live run (posts to Telegram)
    python scripts/send_telegram_digest.py

    # Custom config path
    python scripts/send_telegram_digest.py --config config/my.yaml

    # Dry run: print what would be sent, do not post, do not mark seen
    python scripts/send_telegram_digest.py --dry-run

    # Limit how many pages each scraper fetches (faster, less data)
    python scripts/send_telegram_digest.py --max-pages 2

Designed to run from cron or a scheduled GitHub Action. Exits non-zero on
config errors or when ALL searches fail; a single failing search logs a
warning but does not abort the run.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # type: ignore[import-untyped]

from src.analyzer.market_data import HamburgMarketData
from src.analyzer.scorer import DealScorer
from src.database.db import Database
from src.database.models import Listing, UserCriteria
from src.scraper.immoscout import ImmoScoutScraper
from src.scraper.immowelt import ImmoweltScraper
from src.scraper.kleinanzeigen import KleinanzeigenScraper
from src.scraper.ohne_makler import OhneMaklerScraper

logger = logging.getLogger("telegram_digest")

# Flags to suppress unless explicitly set in config.
_VALID_PROPERTY_TYPES = {"apartment", "house", "multi_family"}
_VALID_RISK = {"conservative", "moderate", "aggressive"}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict[str, Any]:
    """Load and lightly validate the YAML config. Raises on fatal errors."""
    if not path.exists():
        raise SystemExit(
            f"Config file not found: {path}\n"
            f"Copy config/searches.example.yaml to {path} and edit."
        )
    with path.open() as f:
        cfg = yaml.safe_load(f)

    if not isinstance(cfg, dict):
        raise SystemExit(f"Config root must be a mapping, got {type(cfg).__name__}")

    # Telegram section
    tg = cfg.get("telegram") or {}
    token_env = tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
    channel = tg.get("channel")
    if not channel:
        raise SystemExit("config.telegram.channel is required (e.g. '@HamburgDealsDemo')")

    token = os.environ.get(token_env, "")
    if not token:
        raise SystemExit(
            f"Environment variable {token_env} is empty. "
            f"Get a bot token from @BotFather on Telegram and export it."
        )

    # Searches
    searches = cfg.get("searches") or []
    if not searches:
        raise SystemExit("config.searches must contain at least one entry")
    for s in searches:
        if not s.get("slug"):
            raise SystemExit(f"search missing required 'slug': {s}")
        if not s.get("name"):
            raise SystemExit(f"search missing required 'name': {s}")
        pt = s.get("property_type", "apartment")
        if pt not in _VALID_PROPERTY_TYPES:
            raise SystemExit(
                f"search '{s['slug']}' property_type={pt} not in {_VALID_PROPERTY_TYPES}"
            )
        rt = s.get("risk_tolerance", "moderate")
        if rt not in _VALID_RISK:
            raise SystemExit(
                f"search '{s['slug']}' risk_tolerance={rt} not in {_VALID_RISK}"
            )

    cfg["_bot_token"] = token
    return cfg


def search_to_criteria(s: dict[str, Any]) -> UserCriteria:
    """Translate a config search entry to a UserCriteria dataclass."""
    return UserCriteria(
        budget_min=float(s.get("budget_min", 0)),
        budget_max=float(s.get("budget_max", 10_000_000)),
        min_size_sqm=float(s.get("min_size_sqm", 0)),
        min_rooms=float(s.get("min_rooms", 1)),
        property_types=[s.get("property_type", "apartment")],
        districts=list(s.get("districts") or []),
        equity_pct=float(s.get("equity_pct", 20)),
        interest_rate_pct=float(s.get("interest_rate_pct", 3.5)),
        loan_term_years=int(s.get("loan_term_years", 25)),
        risk_tolerance=s.get("risk_tolerance", "moderate"),
    )


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def run_search(criteria: UserCriteria, max_pages: int) -> tuple[list[Listing], list[str]]:
    """Run all scrapers in parallel, return (listings, platform_status_lines)."""
    scrapers = [
        ImmoScoutScraper(rate_limit_min=2, rate_limit_max=4),
        KleinanzeigenScraper(rate_limit_min=2, rate_limit_max=4),
        ImmoweltScraper(rate_limit_min=2, rate_limit_max=4),
        OhneMaklerScraper(rate_limit_min=2, rate_limit_max=4),
    ]
    all_listings: list[Listing] = []
    status_lines: list[str] = []

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(s.search, criteria, max_pages): s for s in scrapers}
        for fut in as_completed(futures):
            scraper = futures[fut]
            try:
                listings = fut.result(timeout=300)
                all_listings.extend(listings)
                status_lines.append(f"{scraper.PLATFORM_NAME}={len(listings)}")
            except Exception as e:
                status_lines.append(f"{scraper.PLATFORM_NAME}=ERR({type(e).__name__})")
                logger.warning("%s failed: %s", scraper.PLATFORM_NAME, e)
            finally:
                try:
                    scraper.close()
                except Exception:
                    pass

    return all_listings, status_lines


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _flags(listing: Listing) -> list[str]:
    f = []
    if listing.is_erbbaurecht:
        f.append("ERBBAU")
    if listing.is_rented:
        rent = f" €{listing.current_rent_monthly:,.0f}/mo" if listing.current_rent_monthly else ""
        f.append(f"RENTED{rent}")
    if listing.is_wbs:
        f.append("WBS")
    if listing.is_dachgeschoss:
        f.append("DACH")
    if listing.is_ausbau_needed:
        f.append("AUSBAU")
    er = (listing.energy_rating or "").upper()
    if er in ("F", "G", "H"):
        f.append(f"ENERGY:{er}")
    return f


def format_header(search: dict[str, Any], count: int, status: str) -> str:
    """Header message sent once per search before the individual deal cards."""
    name = search["name"]
    if count == 0:
        return (
            f"🏠 *{name}*\n\n"
            f"_No new deals today._ ({status})"
        )
    return (
        f"🏠 *{name}*\n"
        f"*{count}* new deal{'s' if count != 1 else ''} today."
    )


def format_deal(listing: Listing, analysis) -> str:
    """One message per deal, Telegram MarkdownV1 (legacy) style."""
    flag_str = " · ".join(_flags(listing))
    flag_line = f"\n🚩 {flag_str}" if flag_str else ""

    # Short opportunity summary (top 2 signals only)
    signals = [r for r in (analysis.undervalue_reasons or []) if r.startswith("[+]")]
    sig_line = ""
    if signals:
        # Strip the [+] prefix
        short = [s.replace("[+] ", "") for s in signals[:2]]
        sig_line = "\n💡 " + " · ".join(short)

    cf_sign = "+" if analysis.monthly_cashflow >= 0 else ""
    district = listing.district or "Hamburg"

    # Limit title to one line
    title = (listing.title or "")[:90]

    msg = (
        f"🏠 *{district}* · {listing.rooms:.0f} Zi · {listing.size_sqm:.0f} m²\n"
        f"💰 *{listing.price:,.0f} €*  ({analysis.price_per_sqm:,.0f} €/m² · "
        f"{analysis.price_vs_market_pct:+.0f}%)\n"
        f"🎯 Score {analysis.deal_score:.0f}/100 · "
        f"Rendite {analysis.gross_rental_yield_pct:.1f}% · "
        f"CF {cf_sign}{analysis.monthly_cashflow:,.0f}€/Mo"
        f"{flag_line}"
        f"{sig_line}"
        f"\n\n{title}"
        f"\n\n🔗 [Inserat]({listing.url})"
    )
    return msg


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

async def send_messages(
    bot_token: str,
    channel: str,
    messages: list[str],
    dry_run: bool,
) -> int:
    """Send messages to the channel. Returns the count actually sent.

    Uses Telegram's Bot API directly via python-telegram-bot. Sleeps 0.05s
    between messages to stay under the 30/s global rate limit.
    """
    if dry_run:
        print("=== DRY RUN ===")
        for m in messages:
            print(m)
            print("---")
        return 0

    # Import here so a dry run works even if python-telegram-bot is not installed
    try:
        from telegram import Bot
        from telegram.constants import ParseMode
    except ImportError:
        raise SystemExit(
            "python-telegram-bot not installed. Run: pip install 'python-telegram-bot>=21.0'"
        )

    bot = Bot(token=bot_token)
    sent = 0
    for m in messages:
        try:
            await bot.send_message(
                chat_id=channel,
                text=m,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False,
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error("send failed: %s", e)
    return sent


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

async def main_async(args) -> int:
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config(Path(args.config))
    channel = cfg["telegram"]["channel"]
    token = cfg["_bot_token"]
    max_deals = int(cfg.get("output", {}).get("max_deals_per_search", 5))
    skip_seen = bool(cfg.get("output", {}).get("skip_seen", True))

    db = Database()
    market = HamburgMarketData()
    scorer = DealScorer(market)

    total_sent = 0
    total_searches_ok = 0

    for search in cfg["searches"]:
        slug = search["slug"]
        logger.info("running search: %s", slug)
        criteria = search_to_criteria(search)

        try:
            listings, status_lines = run_search(criteria, args.max_pages)
        except Exception as e:
            logger.error("%s failed: %s", slug, e)
            continue

        status = ", ".join(status_lines) if status_lines else "no platforms"

        # Dedup
        seen_before = db.get_seen_listing_ids(channel) if skip_seen else set()
        listings = [l for l in listings if l.id not in seen_before]

        # Persist listings first (required for seen_deals FK)
        if listings:
            db.save_listings(listings)

        # Score and pick top N
        scored = scorer.score_and_rank(listings, criteria)
        top = scored[:max_deals]

        # Persist analyses
        for _, analysis in top:
            db.save_analysis(analysis)

        # Build messages
        messages = [format_header(search, len(top), status)]
        for listing, analysis in top:
            messages.append(format_deal(listing, analysis))

        sent = await send_messages(token, channel, messages, args.dry_run)
        total_sent += sent
        total_searches_ok += 1

        # Mark seen only on successful send (or in dry-run, leave unmarked
        # so next real run posts them).
        if not args.dry_run and top:
            db.mark_seen(channel, [l.id for l, _ in top], search_slug=slug)

        logger.info(
            "%s: scraped=%d new=%d sent=%d platforms=[%s]",
            slug, len(listings) + len(seen_before & {l.id for l in listings}),
            len(listings), sent, status,
        )

    print(
        f"digest_complete searches_ok={total_searches_ok}/{len(cfg['searches'])} "
        f"messages_sent={total_sent} channel={channel} dry_run={args.dry_run}"
    )

    if total_searches_ok == 0:
        return 1
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Send Hamburg real estate digest to Telegram")
    ap.add_argument("--config", default="config/searches.yaml", help="Path to searches YAML")
    ap.add_argument("--dry-run", action="store_true", help="Print messages, do not send or mark seen")
    ap.add_argument("--max-pages", type=int, default=3, help="Max pages per scraper per search")
    ap.add_argument("-v", "--verbose", action="store_true", help="INFO-level logging")
    args = ap.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
