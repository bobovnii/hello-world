#!/usr/bin/env python3
"""Daily deal digest - scrapes all platforms and generates a markdown report.

Usage:
    python scripts/daily_digest.py              # run once
    crontab: 0 7 * * * cd /path/to/repo && python scripts/daily_digest.py
"""

import sys
import os
import logging
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")

from src.database.models import UserCriteria
from src.database.db import Database
from src.scraper.immoscout import ImmoScoutScraper
from src.scraper.kleinanzeigen import KleinanzeigenScraper
from src.scraper.immowelt import ImmoweltScraper
from src.scraper.ohne_makler import OhneMaklerScraper
from src.analyzer.market_data import HamburgMarketData
from src.analyzer.scorer import DealScorer

DESY_DISTRICTS = ["Altona", "Eimsbüttel"]

# Configure your saved searches here
SEARCHES = [
    {
        "name": "2-Room near DESY <250k",
        "criteria": UserCriteria(
            budget_max=250000, min_rooms=2, min_size_sqm=30,
            property_types=["apartment"], districts=DESY_DISTRICTS,
            equity_pct=20, interest_rate_pct=3.5, loan_term_years=25,
            risk_tolerance="moderate",
        ),
    },
    {
        "name": "3-Room near DESY <300k",
        "criteria": UserCriteria(
            budget_max=300000, min_rooms=3, min_size_sqm=50,
            property_types=["apartment"], districts=DESY_DISTRICTS,
            equity_pct=20, interest_rate_pct=3.5, loan_term_years=25,
            risk_tolerance="moderate",
        ),
    },
    {
        "name": "MFH near DESY <1.5M",
        "criteria": UserCriteria(
            budget_min=200000, budget_max=1500000, min_size_sqm=80,
            property_types=["multi_family"], districts=DESY_DISTRICTS,
            equity_pct=25, interest_rate_pct=3.5, loan_term_years=25,
            risk_tolerance="moderate",
        ),
    },
]


def create_scrapers():
    return [
        ImmoScoutScraper(rate_limit_min=2, rate_limit_max=4),
        KleinanzeigenScraper(rate_limit_min=2, rate_limit_max=4),
        ImmoweltScraper(rate_limit_min=2, rate_limit_max=4),
        OhneMaklerScraper(rate_limit_min=2, rate_limit_max=4),
    ]


def run_search(criteria, max_pages=3):
    scrapers = create_scrapers()
    all_listings = []
    platforms = []

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(s.search, criteria, max_pages): s for s in scrapers}
        for f in as_completed(futures):
            scraper = futures[f]
            try:
                listings = f.result(timeout=180)
                all_listings.extend(listings)
                platforms.append(f"{scraper.PLATFORM_NAME}({len(listings)})")
            except Exception as e:
                platforms.append(f"{scraper.PLATFORM_NAME}(ERR)")
            finally:
                scraper.close()

    return all_listings, platforms


def format_deal(listing, analysis, idx):
    flags = []
    if listing.is_erbbaurecht:
        flags.append("ERBBAU")
    if listing.is_rented:
        rent = f" €{listing.current_rent_monthly:,.0f}/mo" if listing.current_rent_monthly else ""
        flags.append(f"RENTED{rent}")
    if listing.is_wbs:
        flags.append("WBS")
    if listing.is_dachgeschoss:
        flags.append("DACH")
    if listing.is_ausbau_needed:
        flags.append("AUSBAU")
    if listing.energy_rating and listing.energy_rating.upper() in ("F", "G", "H"):
        flags.append(f"ENERGY:{listing.energy_rating}")

    flag_str = f" | `{'` `'.join(flags)}`" if flags else ""

    opp = [r for r in analysis.undervalue_reasons if r.startswith("[+]")]
    red = [r for r in analysis.undervalue_reasons if r.startswith("[!]")]

    lines = [
        f"### {idx}. [{analysis.deal_score:.0f}/100] EUR {listing.price:,.0f} | "
        f"{listing.size_sqm:.0f}m² | {listing.rooms:.0f}rm | {listing.district}{flag_str}",
        f"**{listing.title}**",
        f"- Platform: {listing.platform} | Hausgeld: {'EUR '+str(int(listing.hausgeld))+'/mo' if listing.hausgeld else '?'} | "
        f"Erbbaurecht: {'YES' if listing.is_erbbaurecht else 'No'}",
        f"- EUR/m²: {analysis.price_per_sqm:,.0f} ({analysis.price_vs_market_pct:+.0f}% vs market) | "
        f"Yield: {analysis.gross_rental_yield_pct:.1f}% | CF: EUR {analysis.monthly_cashflow:+,.0f}/mo",
        f"- {listing.url}",
    ]
    if opp:
        lines.append("- **Opportunities:** " + "; ".join(r.replace("[+] ", "") for r in opp[:3]))
    if red:
        lines.append("- **Red flags:** " + "; ".join(r.replace("[!] ", "").split(" - ")[0] for r in red[:3]))

    desc = (listing.description or "")[:150]
    if desc:
        lines.append(f"- *{desc}...*")

    return "\n".join(lines)


def main():
    market = HamburgMarketData()
    scorer = DealScorer(market)
    db = Database()
    date = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H:%M")

    report = [
        f"# Daily Deal Digest - {date}",
        f"Generated at {time_str} UTC\n",
    ]

    total_deals = 0

    for search in SEARCHES:
        print(f"Searching: {search['name']}...")
        listings, platforms = run_search(search["criteria"])
        results = scorer.score_and_rank(listings, search["criteria"])

        # Save to DB
        db.save_listings(listings)
        for _, a in results:
            db.save_analysis(a)

        total_deals += len(results)

        report.append(f"## {search['name']}")
        report.append(f"Platforms: {', '.join(platforms)} | **{len(results)} deals**\n")

        if results:
            for i, (l, a) in enumerate(results[:5], 1):
                report.append(format_deal(l, a, i))
                report.append("")
        else:
            report.append("*No deals found matching criteria.*\n")

        report.append("---\n")

    report.append(f"**Total: {total_deals} deals across {len(SEARCHES)} searches.**")

    # Save report
    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"digest_{date}.md"
    path.write_text("\n".join(report))
    print(f"\nSaved: {path} ({total_deals} deals)")


if __name__ == "__main__":
    main()
