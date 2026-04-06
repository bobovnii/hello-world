#!/usr/bin/env python3
"""MCP integration tests v2 - with enriched red flag analysis."""

import asyncio
import json
import sys
import os
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database.models import UserCriteria, Listing, AnalysisResult
from src.analyzer.market_data import HamburgMarketData
from src.analyzer.scorer import DealScorer
from src.scraper.immoscout import ImmoScoutScraper
from src.scraper.kleinanzeigen import KleinanzeigenScraper
from src.scraper.immowelt import ImmoweltScraper
from src.scraper.ohne_makler import OhneMaklerScraper


def run_search(criteria, max_pages=2):
    """Run all scrapers and return scored results."""
    scrapers = [
        ImmoScoutScraper(rate_limit_min=1, rate_limit_max=2),
        KleinanzeigenScraper(rate_limit_min=2, rate_limit_max=3),
        ImmoweltScraper(rate_limit_min=2, rate_limit_max=3),
        OhneMaklerScraper(rate_limit_min=2, rate_limit_max=3),
    ]

    all_listings = []
    platforms_ok = []
    platforms_fail = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(s.search, criteria, max_pages): s for s in scrapers}
        for future in as_completed(futures):
            scraper = futures[future]
            try:
                listings = future.result(timeout=180)
                all_listings.extend(listings)
                platforms_ok.append(f"{scraper.PLATFORM_NAME} ({len(listings)})")
            except Exception as e:
                platforms_fail.append(f"{scraper.PLATFORM_NAME} ({str(e)[:50]})")
            finally:
                scraper.close()

    market = HamburgMarketData()
    scorer = DealScorer(market)
    results = scorer.score_and_rank(all_listings, criteria)

    return all_listings, results, platforms_ok, platforms_fail


def format_deal_md(listing: Listing, analysis: AnalysisResult, idx: int) -> str:
    """Format a deal as markdown with enriched signals."""
    opp = [r for r in analysis.undervalue_reasons if r.startswith("[+]")]
    flags = [r for r in analysis.undervalue_reasons if r.startswith("[!]")]

    lines = [f"### Deal #{idx} — Score: {analysis.deal_score:.0f}/100"]

    # Header table
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|-------|-------|")
    lines.append(f"| **Title** | {listing.title} |")
    lines.append(f"| **URL** | {listing.url} |")
    lines.append(f"| **Platform** | {listing.platform} |")
    lines.append(f"| **Price** | EUR {listing.price:,.0f} |")
    lines.append(f"| **Size** | {listing.size_sqm:.0f} m² |")
    lines.append(f"| **Rooms** | {listing.rooms:.1f} |")
    lines.append(f"| **District** | {listing.district} |")
    lines.append(f"| **Address** | {listing.address} |")
    if listing.year_built:
        lines.append(f"| **Year built** | {listing.year_built} |")
    if listing.condition:
        lines.append(f"| **Condition** | {listing.condition} |")
    if listing.energy_rating:
        lines.append(f"| **Energy rating** | {listing.energy_rating} |")
    if listing.hausgeld:
        lines.append(f"| **Hausgeld** | EUR {listing.hausgeld:,.0f}/mo |")

    # Financial metrics
    lines.append("")
    lines.append("**Financial Metrics:**")
    lines.append(f"- Price/m²: EUR {analysis.price_per_sqm:,.0f} (district avg: EUR {analysis.district_avg_price_sqm:,.0f}) = **{analysis.price_vs_market_pct:+.0f}%**")
    lines.append(f"- Gross Yield: {analysis.gross_rental_yield_pct:.2f}%")
    lines.append(f"- Monthly Cashflow: EUR {analysis.monthly_cashflow:+,.0f}")

    # Red flag indicators
    flag_badges = []
    if listing.is_erbbaurecht:
        flag_badges.append("ERBBAURECHT")
    if listing.is_rented:
        rent_str = f" EUR {listing.current_rent_monthly:,.0f}/mo" if listing.current_rent_monthly else ""
        flag_badges.append(f"TENANTED{rent_str}")
    if listing.is_wbs:
        flag_badges.append("WBS")
    if listing.is_dachgeschoss:
        flag_badges.append("DACHGESCHOSS")
    if listing.is_ausbau_needed:
        flag_badges.append("AUSBAU NEEDED")
    if listing.energy_rating and listing.energy_rating.upper() in ("F", "G", "H"):
        flag_badges.append(f"ENERGY {listing.energy_rating.upper()}")

    if flag_badges:
        lines.append("")
        lines.append(f"**Flags:** `{'` `'.join(flag_badges)}`")

    # Opportunity signals
    if opp:
        lines.append("")
        lines.append("**Opportunities (+):**")
        for r in opp:
            lines.append(f"- {r}")

    # Red flags
    if flags:
        lines.append("")
        lines.append("**Red Flags (!):**")
        for r in flags:
            lines.append(f"- {r}")

    # Description snippet
    desc = (listing.description or "")[:300]
    if desc:
        lines.append("")
        lines.append(f"**Description:** {desc}...")

    return "\n".join(lines)


def build_test_md(title, request_block, all_listings, results, platforms_ok, platforms_fail, max_deals=5):
    """Build full test markdown."""
    md = [
        f"# {title}",
        f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Request",
        "```",
        request_block,
        "```",
        "",
        "## Search Metadata",
        f"- **Platforms OK**: {', '.join(platforms_ok) or 'none'}",
        f"- **Platforms failed**: {', '.join(platforms_fail) or 'none'}",
        f"- **Total listings scraped**: {len(all_listings)}",
        f"- **Deals scored**: {len(results)}",
        "",
        "## Results",
        "",
    ]

    if results:
        for i, (listing, analysis) in enumerate(results[:max_deals], 1):
            md.append(format_deal_md(listing, analysis, i))
            md.append("")
            md.append("---")
            md.append("")
    else:
        md.append("*No deals found matching criteria.*")

    return "\n".join(md)


def test_1():
    """Budget apartment near DESY/Altona+Eimsbüttel <400k."""
    print("Test 1: Budget apartment Altona/Eimsbüttel <400k...")
    criteria = UserCriteria(
        budget_max=400000, min_rooms=2, min_size_sqm=30,
        property_types=["apartment"], districts=["Altona", "Eimsbüttel"],
        equity_pct=20, interest_rate_pct=3.5, loan_term_years=25,
        risk_tolerance="moderate",
    )
    listings, results, ok, fail = run_search(criteria)
    return build_test_md(
        "Test 1: Budget Apartment near DESY (Altona + Eimsbüttel, <400k)",
        "budget_max: 400,000 | min_rooms: 2 | districts: [Altona, Eimsbüttel]\n"
        "property_type: apartment | risk: moderate | equity: 20%",
        listings, results, ok, fail,
    )


def test_2():
    """All Hamburg apartments <250k, aggressive scoring."""
    print("Test 2: Cheapest Hamburg apartments <250k aggressive...")
    criteria = UserCriteria(
        budget_max=250000, min_rooms=1, min_size_sqm=25,
        property_types=["apartment"],
        equity_pct=15, interest_rate_pct=4.0, loan_term_years=25,
        risk_tolerance="aggressive",
    )
    listings, results, ok, fail = run_search(criteria)
    return build_test_md(
        "Test 2: Cheapest Hamburg Apartments (<250k, Aggressive)",
        "budget_max: 250,000 | min_rooms: 1 | districts: all\n"
        "property_type: apartment | risk: aggressive | equity: 15% | rate: 4.0%",
        listings, results, ok, fail,
    )


def test_3():
    """MFH / Mehrfamilienhaus <1.5M."""
    print("Test 3: Mehrfamilienhaus <1.5M...")
    criteria = UserCriteria(
        budget_min=300000, budget_max=1500000, min_rooms=2, min_size_sqm=80,
        property_types=["multi_family"],
        equity_pct=30, interest_rate_pct=3.5, loan_term_years=25,
        risk_tolerance="aggressive",
    )
    listings, results, ok, fail = run_search(criteria, max_pages=3)
    return build_test_md(
        "Test 3: Mehrfamilienhaus Investment (<1.5M)",
        "budget: 300k-1.5M | min_size: 80m² | property_type: multi_family\n"
        "risk: aggressive | equity: 30%",
        listings, results, ok, fail,
    )


def test_4():
    """Market data + investment estimates (no scraping)."""
    print("Test 4: Market data overview...")
    market = HamburgMarketData()
    from src.analyzer.metrics import MetricsCalculator
    calc = MetricsCalculator(market)

    md = [
        "# Test 4: Market Data & Investment Estimates",
        f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Request",
        "```",
        "Tool: get_market_data(districts=None)  # all Hamburg",
        "Tool: estimate_investment(200k, 60m², per district)",
        "```",
        "",
        "## Hamburg Districts Overview",
        "",
        "| District | Avg Price/m² | Avg Rent/m² | Yield | Trend |",
        "|----------|:-----------:|:-----------:|:-----:|:-----:|",
    ]

    for name in market.districts:
        data = market.get_district_data(name)
        md.append(
            f"| {name} | EUR {data['avg_price_sqm_buy']:,.0f} | "
            f"EUR {data['avg_rent_sqm_month']:.2f} | "
            f"{data['avg_gross_yield_pct']:.1f}% | {data.get('trend', '?')} |"
        )

    md.extend([
        "",
        f"**Purchase costs**: {market.total_purchase_costs_pct:.2f}% "
        f"({', '.join(f'{k}: {v}%' for k, v in market.purchase_costs_breakdown.items())})",
        "",
        "## Investment Estimate: EUR 200k apartment (60m²) per district",
        "",
        "| District | Rent/mo | Mortgage/mo | Cashflow/mo | Yield | vs Market |",
        "|----------|:-------:|:-----------:|:-----------:|:-----:|:---------:|",
    ])

    for name in market.districts:
        listing = Listing(id="est", platform="est", url="", title="",
                         price=200000, size_sqm=60, rooms=2, district=name)
        c = UserCriteria(equity_pct=20, interest_rate_pct=3.5, loan_term_years=25)
        r = calc.calculate(listing, c)
        md.append(
            f"| {name} | EUR {r.estimated_rent_monthly:,.0f} | "
            f"EUR {r.mortgage_monthly:,.0f} | "
            f"EUR {r.monthly_cashflow:+,.0f} | "
            f"{r.gross_rental_yield_pct:.1f}% | "
            f"{r.price_vs_market_pct:+.0f}% |"
        )

    return "\n".join(md)


def test_5():
    """Premium 3+ room Eimsbüttel/Hamburg-Nord <500k, conservative."""
    print("Test 5: Premium Eimsbüttel/Nord <500k conservative...")
    criteria = UserCriteria(
        budget_min=200000, budget_max=500000, min_rooms=3, min_size_sqm=60,
        property_types=["apartment"], districts=["Eimsbüttel", "Hamburg-Nord"],
        equity_pct=25, interest_rate_pct=3.5, loan_term_years=25,
        risk_tolerance="conservative",
    )
    listings, results, ok, fail = run_search(criteria)
    return build_test_md(
        "Test 5: Premium Apartment (Eimsbüttel/Nord, 3+ rooms, <500k)",
        "budget: 200k-500k | min_rooms: 3 | min_size: 60m²\n"
        "districts: [Eimsbüttel, Hamburg-Nord] | risk: conservative | equity: 25%",
        listings, results, ok, fail,
    )


def main():
    import logging
    logging.basicConfig(level=logging.WARNING)

    results_dir = Path("tests/mcp_results")
    results_dir.mkdir(parents=True, exist_ok=True)

    tests = [
        ("test_1_altona_budget.md", test_1),
        ("test_2_cheapest_aggressive.md", test_2),
        ("test_3_mfh_investment.md", test_3),
        ("test_4_market_data.md", test_4),
        ("test_5_premium_conservative.md", test_5),
    ]

    for filename, test_fn in tests:
        try:
            md = test_fn()
            path = results_dir / filename
            path.write_text(md)
            print(f"  -> Saved: {path}")
        except Exception as e:
            print(f"  -> ERROR: {e}")
            import traceback
            traceback.print_exc()

    print("\nAll tests complete.")


if __name__ == "__main__":
    main()
