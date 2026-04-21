"""Listing analysis tool."""

from __future__ import annotations

import logging

from mcp.server.fastmcp import Context

from src.database.models import Listing, UserCriteria
from mcp_server.models.outputs import DealAnalysis, DealResult
from mcp_server.context import get_db

logger = logging.getLogger(__name__)


def register(mcp):
    @mcp.tool()
    async def analyze_listing(
        listing_id: str | None = None,
        listing_url: str | None = None,
        equity_pct: float = 20,
        interest_rate_pct: float = 3.5,
        loan_term_years: int = 25,
        ctx: Context = None,
    ) -> DealAnalysis:
        """Get detailed financial analysis for a specific property.

        Provide a listing_id from search results, or a URL to scrape fresh.
        Returns complete investment breakdown: price vs market, rental yield,
        cap rate, monthly cashflow, cash-on-cash return, equity needed,
        mortgage payment, full description, and all undervalue signals.
        """
        lc = ctx.request_context.lifespan_context
        market_data = lc["market_data"]
        scorer = lc["scorer"]

        listing = None

        # Try to load from DB by ID
        if listing_id:
            try:
                db = await get_db(ctx)
                row = await db.execute(
                    "SELECT * FROM listings WHERE id = ?", (listing_id,)
                )
                row = await row.fetchone()
                if row:
                    listing = Listing.from_dict(dict(row))
                await db.close()
            except Exception as e:
                logger.warning(f"DB lookup failed: {e}")

        # If not found and URL provided, try scraping
        if not listing and listing_url:
            listing = _scrape_single_listing(listing_url)

        if not listing:
            # Return empty analysis with error info
            return DealAnalysis(
                listing=DealResult(
                    listing_id=listing_id or "unknown",
                    title="Listing not found",
                    url=listing_url or "",
                    platform="unknown",
                    price=0, size_sqm=0, rooms=0,
                    district="", address="",
                    deal_score=0, price_per_sqm=0,
                    price_vs_market_pct=0, gross_yield_pct=0,
                    monthly_cashflow=0,
                ),
                total_purchase_cost=0, equity_required=0,
                mortgage_monthly=0, estimated_rent_monthly=0,
                net_yield_pct=0, cap_rate_pct=0,
                cash_on_cash_return_pct=0, district_avg_price_sqm=0,
            )

        # Run analysis
        criteria = UserCriteria(
            equity_pct=equity_pct,
            interest_rate_pct=interest_rate_pct,
            loan_term_years=loan_term_years,
        )
        analysis, score = scorer.score_listing(listing, criteria)

        desc = listing.description or ""
        features = []
        if listing.balcony:
            features.append("balcony")
        if listing.garden:
            features.append("garden")
        if listing.parking:
            features.append("parking")

        deal_result = DealResult(
            listing_id=listing.id,
            title=listing.title,
            url=listing.url,
            platform=listing.platform,
            price=listing.price,
            size_sqm=listing.size_sqm,
            rooms=listing.rooms,
            district=listing.district,
            address=listing.address,
            property_type=listing.property_type,
            description_snippet=desc[:200] if desc else None,
            deal_score=analysis.deal_score,
            price_per_sqm=analysis.price_per_sqm,
            price_vs_market_pct=analysis.price_vs_market_pct,
            gross_yield_pct=analysis.gross_rental_yield_pct,
            monthly_cashflow=analysis.monthly_cashflow,
            undervalue_reasons=analysis.undervalue_reasons,
        )

        return DealAnalysis(
            listing=deal_result,
            total_purchase_cost=analysis.total_purchase_cost,
            equity_required=analysis.equity_required,
            mortgage_monthly=analysis.mortgage_monthly,
            estimated_rent_monthly=analysis.estimated_rent_monthly,
            net_yield_pct=analysis.net_rental_yield_pct,
            cap_rate_pct=analysis.cap_rate_pct,
            cash_on_cash_return_pct=analysis.cash_on_cash_return_pct,
            district_avg_price_sqm=analysis.district_avg_price_sqm,
            year_built=listing.year_built,
            condition=listing.condition,
            description=desc[:2000] if desc else None,
            property_type=listing.property_type,
            features=features,
        )


def _scrape_single_listing(url: str) -> Listing | None:
    """Try to scrape a single listing from its URL."""
    from src.scraper.utils import fetch_page

    html = fetch_page(url)
    if not html:
        return None

    # Detect platform from URL and use appropriate parser
    if "immobilienscout24.de" in url:
        from src.scraper.immoscout import ImmoScoutScraper
        return ImmoScoutScraper().parse_listing_detail(html, url)
    elif "kleinanzeigen.de" in url:
        from src.scraper.kleinanzeigen import KleinanzeigenScraper
        return KleinanzeigenScraper().parse_listing_detail(html, url)
    elif "ohne-makler.net" in url:
        from src.scraper.ohne_makler import OhneMaklerScraper
        scraper = OhneMaklerScraper()
        # Create a stub listing and enrich from detail page
        stub = Listing(id=f"ohne-makler_{hash(url)}", platform="ohne-makler",
                       url=url, title="", price=0, size_sqm=0, rooms=0)
        return scraper._enrich_from_detail(stub, html)

    return None
