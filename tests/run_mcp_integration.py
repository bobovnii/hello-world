#!/usr/bin/env python3
"""MCP Server integration tests - simulates agent tool calls and saves results."""

import asyncio
import json
import sys
import os
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.context import app_lifespan
from mcp_server.jobs import JobStore
from mcp_server.models.outputs import *
from src.database.models import UserCriteria
from src.analyzer.market_data import HamburgMarketData
from src.analyzer.scorer import DealScorer


class MockContext:
    """Simulates MCP Context for direct tool testing."""
    def __init__(self, lifespan_ctx):
        self.request_context = type('RC', (), {'lifespan_context': lifespan_ctx})()


def format_deal(d: DealResult, idx: int) -> str:
    lines = [
        f"### Deal #{idx}",
        f"- **Title**: {d.title}",
        f"- **URL**: {d.url}",
        f"- **Platform**: {d.platform}",
        f"- **Price**: EUR {d.price:,.0f}",
        f"- **Size**: {d.size_sqm} m² | **Rooms**: {d.rooms}",
        f"- **District**: {d.district}",
        f"- **Address**: {d.address}",
        f"- **Property type**: {d.property_type}",
        f"- **Deal Score**: {d.deal_score}/100",
        f"- **Price/m²**: EUR {d.price_per_sqm:,.0f} ({d.price_vs_market_pct:+.1f}% vs market)",
        f"- **Gross Yield**: {d.gross_yield_pct:.2f}%",
        f"- **Monthly Cashflow**: EUR {d.monthly_cashflow:+,.0f}",
    ]
    if d.description_snippet:
        lines.append(f"- **Description**: {d.description_snippet}")
    if d.undervalue_reasons:
        lines.append("- **Undervalue Signals**:")
        for r in d.undervalue_reasons:
            lines.append(f"  - {r}")
    return "\n".join(lines)


def format_analysis(a: DealAnalysis) -> str:
    lines = [
        format_deal(a.listing, 1),
        "",
        "#### Full Financial Analysis",
        f"- Total Purchase Cost: EUR {a.total_purchase_cost:,.0f}",
        f"- Equity Required: EUR {a.equity_required:,.0f}",
        f"- Mortgage Payment: EUR {a.mortgage_monthly:,.0f}/mo",
        f"- Estimated Rent: EUR {a.estimated_rent_monthly:,.0f}/mo",
        f"- Net Yield: {a.net_yield_pct:.2f}%",
        f"- Cap Rate: {a.cap_rate_pct:.2f}%",
        f"- Cash-on-Cash Return: {a.cash_on_cash_return_pct:.2f}%",
        f"- District Avg Price/m²: EUR {a.district_avg_price_sqm:,.0f}",
    ]
    if a.year_built:
        lines.append(f"- Year Built: {a.year_built}")
    if a.condition:
        lines.append(f"- Condition: {a.condition}")
    if a.features:
        lines.append(f"- Features: {', '.join(a.features)}")
    if a.description:
        lines.append(f"- **Full Description**: {a.description[:500]}...")
    return "\n".join(lines)


async def run_search_and_wait(job_store, scorer, criteria, max_results=10,
                               equity_pct=20, interest_rate_pct=3.5,
                               loan_term_years=25, risk_tolerance="moderate"):
    """Start a search job and wait for completion."""
    from src.scraper.immoscout import ImmoScoutScraper
    from src.scraper.kleinanzeigen import KleinanzeigenScraper
    from src.scraper.immowelt import ImmoweltScraper
    from src.scraper.ohne_makler import OhneMaklerScraper

    scrapers = [
        ImmoScoutScraper(rate_limit_min=1, rate_limit_max=2),
        KleinanzeigenScraper(rate_limit_min=2, rate_limit_max=3),
        ImmoweltScraper(rate_limit_min=2, rate_limit_max=3),
        OhneMaklerScraper(rate_limit_min=2, rate_limit_max=3),
    ]

    job = job_store.start_job(criteria, scrapers)
    print(f"  Job {job.job_id} started...")

    # Wait for completion
    for _ in range(120):
        await asyncio.sleep(1)
        if job.status != "running":
            break

    print(f"  Job {job.status}: {job.progress_message} ({job.duration_seconds:.0f}s)")

    # Score results
    from mcp_server.tools.search import _score_job_results
    deals = _score_job_results(
        job, scorer, equity_pct, interest_rate_pct,
        loan_term_years, risk_tolerance, max_results,
    )

    return job, deals


async def test_1_budget_apartment():
    """Test 1: Budget apartment search - under 250k, 2+ rooms, Harburg/Bergedorf."""
    print("\n=== TEST 1: Budget Apartment (Harburg/Bergedorf, <250k, 2+ rooms) ===")

    async with app_lifespan(None) as lc:
        criteria = UserCriteria(
            budget_min=50000, budget_max=250000, min_size_sqm=30, min_rooms=2,
            property_types=["apartment"], districts=["Harburg", "Bergedorf"],
        )
        job, deals = await run_search_and_wait(
            lc["job_store"], lc["scorer"], criteria,
            max_results=5, risk_tolerance="conservative",
        )

        md = [
            "# Test 1: Budget Apartment Search",
            f"**Date**: {datetime.now().isoformat()[:19]}",
            "",
            "## Request",
            "```",
            "Tool: start_search + get_search_results",
            "budget_max: 250,000 EUR",
            "budget_min: 50,000 EUR",
            "min_rooms: 2",
            "min_size_sqm: 30",
            "property_type: apartment",
            "districts: [Harburg, Bergedorf]",
            "risk_tolerance: conservative",
            "```",
            "",
            "## Search Metadata",
            f"- **Platforms searched**: {', '.join(job.platforms_done)}",
            f"- **Platforms failed**: {', '.join(f'{p} ({e})' for p, e in job.platforms_failed) or 'none'}",
            f"- **Total listings found**: {len(job.listings)}",
            f"- **Duration**: {job.duration_seconds:.0f}s",
            f"- **Deals scored**: {len(deals)}",
            "",
            "## Results",
        ]
        if deals:
            for i, d in enumerate(deals, 1):
                md.append(format_deal(d, i))
                md.append("")
        else:
            md.append("*No deals found matching criteria.*")

        return "\n".join(md), job, deals


async def test_2_premium_eimsbuettel():
    """Test 2: Premium apartment in Eimsbüttel under 500k."""
    print("\n=== TEST 2: Premium Eimsbüttel Apartment (<500k, 3+ rooms) ===")

    async with app_lifespan(None) as lc:
        criteria = UserCriteria(
            budget_min=200000, budget_max=500000, min_size_sqm=50, min_rooms=3,
            property_types=["apartment"], districts=["Eimsbüttel"],
        )
        job, deals = await run_search_and_wait(
            lc["job_store"], lc["scorer"], criteria,
            max_results=5, equity_pct=25, interest_rate_pct=3.5,
            risk_tolerance="moderate",
        )

        md = [
            "# Test 2: Premium Eimsbüttel Apartment",
            f"**Date**: {datetime.now().isoformat()[:19]}",
            "",
            "## Request",
            "```",
            "Tool: start_search + get_search_results",
            "budget_max: 500,000 EUR",
            "budget_min: 200,000 EUR",
            "min_rooms: 3",
            "min_size_sqm: 50",
            "property_type: apartment",
            "districts: [Eimsbüttel]",
            "equity_pct: 25%",
            "risk_tolerance: moderate",
            "```",
            "",
            "## Search Metadata",
            f"- **Platforms searched**: {', '.join(job.platforms_done)}",
            f"- **Platforms failed**: {', '.join(f'{p} ({e})' for p, e in job.platforms_failed) or 'none'}",
            f"- **Total listings found**: {len(job.listings)}",
            f"- **Duration**: {job.duration_seconds:.0f}s",
            f"- **Deals scored**: {len(deals)}",
            "",
            "## Results",
        ]
        if deals:
            for i, d in enumerate(deals, 1):
                md.append(format_deal(d, i))
                md.append("")
        else:
            md.append("*No deals found matching criteria.*")

        return "\n".join(md), job, deals


async def test_3_mfh_investment():
    """Test 3: Mehrfamilienhaus under 1.5M."""
    print("\n=== TEST 3: Mehrfamilienhaus (<1.5M) ===")

    async with app_lifespan(None) as lc:
        criteria = UserCriteria(
            budget_min=300000, budget_max=1500000, min_size_sqm=80, min_rooms=2,
            property_types=["multi_family"],
        )
        job, deals = await run_search_and_wait(
            lc["job_store"], lc["scorer"], criteria,
            max_results=5, equity_pct=30, interest_rate_pct=3.5,
            risk_tolerance="aggressive",
        )

        md = [
            "# Test 3: Mehrfamilienhaus Investment",
            f"**Date**: {datetime.now().isoformat()[:19]}",
            "",
            "## Request",
            "```",
            "Tool: start_search + get_search_results",
            "budget_max: 1,500,000 EUR",
            "budget_min: 300,000 EUR",
            "min_rooms: 2",
            "min_size_sqm: 80",
            "property_type: multi_family",
            "districts: all Hamburg",
            "equity_pct: 30%",
            "risk_tolerance: aggressive",
            "```",
            "",
            "## Search Metadata",
            f"- **Platforms searched**: {', '.join(job.platforms_done)}",
            f"- **Platforms failed**: {', '.join(f'{p} ({e})' for p, e in job.platforms_failed) or 'none'}",
            f"- **Total listings found**: {len(job.listings)}",
            f"- **Duration**: {job.duration_seconds:.0f}s",
            f"- **Deals scored**: {len(deals)}",
            "",
            "## Results",
        ]
        if deals:
            for i, d in enumerate(deals, 1):
                md.append(format_deal(d, i))
                md.append("")
        else:
            md.append("*No MFH deals found matching criteria.*")

        return "\n".join(md), job, deals


async def test_4_market_data():
    """Test 4: Market data overview + investment estimates for each district."""
    print("\n=== TEST 4: Market Data & Investment Estimates ===")

    async with app_lifespan(None) as lc:
        market = lc["market_data"]
        scorer = lc["scorer"]

        md = [
            "# Test 4: Market Data & Investment Estimates",
            f"**Date**: {datetime.now().isoformat()[:19]}",
            "",
            "## Request 1: get_market_data()",
            "```",
            "Tool: get_market_data",
            "districts: null (all Hamburg)",
            "```",
            "",
            "## All Districts Overview",
            "",
            "| District | Avg Price/m² | Avg Rent/m² | Yield | Trend |",
            "|----------|-------------|-------------|-------|-------|",
        ]

        for name in market.districts:
            data = market.get_district_data(name)
            md.append(
                f"| {name} | EUR {data['avg_price_sqm_buy']:,.0f} | "
                f"EUR {data['avg_rent_sqm_month']:.2f} | "
                f"{data['avg_gross_yield_pct']:.1f}% | {data.get('trend', 'stable')} |"
            )

        md.extend([
            "",
            f"**Total purchase costs**: {market.total_purchase_costs_pct:.2f}%",
            "",
            "## Request 2: estimate_investment (200k apartment per district)",
            "```",
            "Tool: estimate_investment",
            "purchase_price: 200,000 EUR",
            "size_sqm: 60",
            "equity_pct: 20%",
            "interest_rate: 3.5%",
            "loan_term: 25 years",
            "```",
            "",
            "| District | Est Rent/mo | Mortgage/mo | Cashflow/mo | Gross Yield | vs Market |",
            "|----------|-----------|------------|------------|-------------|-----------|",
        ])

        from src.analyzer.metrics import MetricsCalculator
        from src.database.models import Listing, UserCriteria as UC
        calc = MetricsCalculator(market)

        for name in market.districts:
            listing = Listing(id='est', platform='estimate', url='', title='',
                             price=200000, size_sqm=60, rooms=2, district=name)
            criteria = UC(equity_pct=20, interest_rate_pct=3.5, loan_term_years=25)
            r = calc.calculate(listing, criteria)
            md.append(
                f"| {name} | EUR {r.estimated_rent_monthly:,.0f} | "
                f"EUR {r.mortgage_monthly:,.0f} | "
                f"EUR {r.monthly_cashflow:+,.0f} | "
                f"{r.gross_rental_yield_pct:.1f}% | "
                f"{r.price_vs_market_pct:+.0f}% |"
            )

        return "\n".join(md), None, None


async def test_5_aggressive_renovation():
    """Test 5: Aggressive search for renovation bargains under 200k."""
    print("\n=== TEST 5: Aggressive Renovation Deals (<200k, all Hamburg) ===")

    async with app_lifespan(None) as lc:
        criteria = UserCriteria(
            budget_min=50000, budget_max=200000, min_size_sqm=25, min_rooms=1,
            property_types=["apartment"],
        )
        job, deals = await run_search_and_wait(
            lc["job_store"], lc["scorer"], criteria,
            max_results=5, equity_pct=15, interest_rate_pct=4.0,
            risk_tolerance="aggressive",
        )

        # Also run analyze_listing on top deal if available
        analysis_md = ""
        if deals:
            top = deals[0]
            # Run analysis
            from src.database.models import Listing, UserCriteria as UC
            # Find the listing object
            for l in job.listings:
                if l.id == top.listing_id:
                    criteria_a = UC(equity_pct=15, interest_rate_pct=4.0, loan_term_years=25)
                    analysis, score = lc["scorer"].score_listing(l, criteria_a)

                    from mcp_server.models.outputs import DealAnalysis as DA
                    features = []
                    if l.balcony: features.append("balcony")
                    if l.garden: features.append("garden")
                    if l.parking: features.append("parking")

                    da = DA(
                        listing=top,
                        total_purchase_cost=analysis.total_purchase_cost,
                        equity_required=analysis.equity_required,
                        mortgage_monthly=analysis.mortgage_monthly,
                        estimated_rent_monthly=analysis.estimated_rent_monthly,
                        net_yield_pct=analysis.net_rental_yield_pct,
                        cap_rate_pct=analysis.cap_rate_pct,
                        cash_on_cash_return_pct=analysis.cash_on_cash_return_pct,
                        district_avg_price_sqm=analysis.district_avg_price_sqm,
                        year_built=l.year_built,
                        condition=l.condition,
                        description=l.description[:500] if l.description else None,
                        property_type=l.property_type,
                        features=features,
                    )
                    analysis_md = (
                        "\n## Deep Analysis: Top Deal (analyze_listing)\n"
                        "```\n"
                        f"Tool: analyze_listing\n"
                        f"listing_id: {top.listing_id}\n"
                        f"equity_pct: 15%\n"
                        f"interest_rate_pct: 4.0%\n"
                        "```\n\n" +
                        format_analysis(da)
                    )
                    break

        md = [
            "# Test 5: Aggressive Renovation Deals",
            f"**Date**: {datetime.now().isoformat()[:19]}",
            "",
            "## Request",
            "```",
            "Tool: start_search + get_search_results",
            "budget_max: 200,000 EUR",
            "budget_min: 50,000 EUR",
            "min_rooms: 1",
            "min_size_sqm: 25",
            "property_type: apartment",
            "districts: all Hamburg",
            "equity_pct: 15%",
            "interest_rate: 4.0%",
            "risk_tolerance: aggressive",
            "```",
            "",
            "## Search Metadata",
            f"- **Platforms searched**: {', '.join(job.platforms_done)}",
            f"- **Platforms failed**: {', '.join(f'{p} ({e})' for p, e in job.platforms_failed) or 'none'}",
            f"- **Total listings found**: {len(job.listings)}",
            f"- **Duration**: {job.duration_seconds:.0f}s",
            f"- **Deals scored**: {len(deals)}",
            "",
            "## Results",
        ]
        if deals:
            for i, d in enumerate(deals, 1):
                md.append(format_deal(d, i))
                md.append("")
        else:
            md.append("*No deals found matching criteria.*")

        if analysis_md:
            md.append(analysis_md)

        return "\n".join(md), job, deals


async def main():
    results_dir = Path("tests/mcp_results")
    results_dir.mkdir(parents=True, exist_ok=True)

    tests = [
        ("test_1_budget_apartment.md", test_1_budget_apartment),
        ("test_2_premium_eimsbuettel.md", test_2_premium_eimsbuettel),
        ("test_3_mfh_investment.md", test_3_mfh_investment),
        ("test_4_market_data.md", test_4_market_data),
        ("test_5_aggressive_renovation.md", test_5_aggressive_renovation),
    ]

    for filename, test_fn in tests:
        try:
            md_content, job, deals = await test_fn()
            filepath = results_dir / filename
            filepath.write_text(md_content)
            deal_count = len(deals) if deals else "N/A"
            print(f"  Saved: {filepath} ({deal_count} deals)")
        except Exception as e:
            print(f"  ERROR in {filename}: {e}")
            import traceback
            traceback.print_exc()

    print("\n=== All tests complete ===")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
