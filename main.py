#!/usr/bin/env python3
"""Hamburg Real Estate Deal Finder - Entry point."""

import argparse
import logging
import sys


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_cli(args):
    """Run the interactive CLI bot."""
    from src.bot.cli import CLIBot
    bot = CLIBot()
    bot.run()


def cmd_telegram(args):
    """Start the Telegram bot."""
    from src.bot.telegram_bot import TelegramBot
    bot = TelegramBot(token=args.token)
    bot.run()


def cmd_scrape(args):
    """Run a batch scrape without interactive mode."""
    from concurrent.futures import ThreadPoolExecutor
    from src.database.models import UserCriteria
    from src.database.db import Database
    from src.scraper.immoscout import ImmoScoutScraper
    from src.scraper.kleinanzeigen import KleinanzeigenScraper
    from src.scraper.immowelt import ImmoweltScraper
    from src.scraper.ohne_makler import OhneMaklerScraper
    from src.analyzer.market_data import HamburgMarketData
    from src.analyzer.scorer import DealScorer

    criteria = UserCriteria(
        budget_min=args.min_price,
        budget_max=args.max_price,
        min_size_sqm=args.min_size,
        min_rooms=args.min_rooms,
    )

    db = Database()
    market_data = HamburgMarketData()
    scorer = DealScorer(market_data)

    scrapers = [ImmoScoutScraper(), KleinanzeigenScraper(), ImmoweltScraper(), OhneMaklerScraper()]
    all_listings = []

    print(f"Scraping Hamburg listings (budget: {criteria.budget_min:,.0f}-{criteria.budget_max:,.0f} EUR)...")

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(s.search, criteria, args.pages): s for s in scrapers}
        for future in futures:
            try:
                listings = future.result(timeout=300)
                all_listings.extend(listings)
                print(f"  {futures[future].PLATFORM_NAME}: {len(listings)} listings")
            except Exception as e:
                print(f"  {futures[future].PLATFORM_NAME}: ERROR - {e}")
            finally:
                futures[future].close()

    print(f"\nTotal: {len(all_listings)} listings found")

    if all_listings:
        db.save_listings(all_listings)
        results = scorer.score_and_rank(all_listings, criteria)
        for _, analysis in results:
            db.save_analysis(analysis)

        if args.export == "csv":
            import csv
            path = "data/deals_export.csv"
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Score", "Price", "Size", "Rooms", "District", "Yield", "CF", "URL"])
                for listing, analysis in results[:args.top]:
                    writer.writerow([
                        analysis.deal_score, listing.price, listing.size_sqm,
                        listing.rooms, listing.district, analysis.gross_rental_yield_pct,
                        analysis.monthly_cashflow, listing.url,
                    ])
            print(f"Exported top {args.top} deals to {path}")
        elif args.export == "json":
            import json
            path = "data/deals_export.json"
            data = [
                {"listing": l.to_dict(), "analysis": a.to_dict()}
                for l, a in results[:args.top]
            ]
            with open(path, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"Exported top {args.top} deals to {path}")
        else:
            print(f"\nTop {min(args.top, len(results))} deals:")
            for i, (listing, analysis) in enumerate(results[:args.top], 1):
                print(
                    f"  {i}. [{analysis.deal_score:.0f}] "
                    f"€{listing.price:,.0f} | {listing.size_sqm:.0f}m² | "
                    f"{listing.district} | yield {analysis.gross_rental_yield_pct:.1f}% | "
                    f"CF €{analysis.monthly_cashflow:+,.0f}/mo"
                )


def main():
    parser = argparse.ArgumentParser(
        description="Hamburg Real Estate Deal Finder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py cli                     # Interactive CLI
  python main.py telegram                # Start Telegram bot
  python main.py scrape --max-price 300000 --export csv
        """,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    subparsers = parser.add_subparsers(dest="command", help="Mode to run")

    # CLI mode
    cli_parser = subparsers.add_parser("cli", help="Interactive CLI")
    cli_parser.set_defaults(func=cmd_cli)

    # Telegram mode
    tg_parser = subparsers.add_parser("telegram", help="Telegram bot")
    tg_parser.add_argument("--token", help="Telegram bot token (or set TELEGRAM_BOT_TOKEN env)")
    tg_parser.set_defaults(func=cmd_telegram)

    # Batch scrape mode
    scrape_parser = subparsers.add_parser("scrape", help="Batch scrape")
    scrape_parser.add_argument("--min-price", type=float, default=50000)
    scrape_parser.add_argument("--max-price", type=float, default=500000)
    scrape_parser.add_argument("--min-size", type=float, default=30)
    scrape_parser.add_argument("--min-rooms", type=float, default=2)
    scrape_parser.add_argument("--pages", type=int, default=3)
    scrape_parser.add_argument("--top", type=int, default=10)
    scrape_parser.add_argument("--export", choices=["csv", "json"], help="Export format")
    scrape_parser.set_defaults(func=cmd_scrape)

    args = parser.parse_args()

    if not args.command:
        # Default to CLI mode
        args.command = "cli"
        args.func = cmd_cli

    setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()
