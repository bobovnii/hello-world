"""Search tools: async job-based scraping with cache."""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import Context

from src.database.models import Listing, UserCriteria, AnalysisResult
from src.scraper.immoscout import ImmoScoutScraper
from src.scraper.kleinanzeigen import KleinanzeigenScraper
from src.scraper.immowelt import ImmoweltScraper
from src.scraper.ohne_makler import OhneMaklerScraper
from mcp_server.models.outputs import (
    DealResult,
    PlatformError,
    SearchJobStatus,
    SearchResult,
)
from mcp_server.jobs import SearchJob
from mcp_server.context import get_db

logger = logging.getLogger(__name__)


def _build_criteria(
    budget_max: float,
    budget_min: float,
    min_rooms: float,
    min_size_sqm: float,
    property_type: str,
    districts: list[str] | None,
) -> UserCriteria:
    return UserCriteria(
        budget_min=budget_min,
        budget_max=budget_max,
        min_rooms=min_rooms,
        min_size_sqm=min_size_sqm,
        property_types=[property_type],
        districts=districts or [],
    )


def _create_scrapers() -> list:
    return [
        ImmoScoutScraper(rate_limit_min=1, rate_limit_max=3),
        KleinanzeigenScraper(rate_limit_min=2, rate_limit_max=4),
        ImmoweltScraper(rate_limit_min=2, rate_limit_max=4),
        OhneMaklerScraper(rate_limit_min=2, rate_limit_max=4),
    ]


def _listing_to_deal(listing: Listing, analysis: AnalysisResult) -> DealResult:
    desc = listing.description or ""
    return DealResult(
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


def _score_job_results(
    job: SearchJob,
    scorer,
    equity_pct: float,
    interest_rate_pct: float,
    loan_term_years: int,
    risk_tolerance: str,
    max_results: int,
) -> list[DealResult]:
    """Score and rank a job's listings."""
    criteria = UserCriteria(
        budget_min=job.criteria.budget_min,
        budget_max=job.criteria.budget_max,
        min_size_sqm=job.criteria.min_size_sqm,
        min_rooms=job.criteria.min_rooms,
        property_types=job.criteria.property_types,
        districts=job.criteria.districts,
        equity_pct=equity_pct,
        interest_rate_pct=interest_rate_pct,
        loan_term_years=loan_term_years,
        risk_tolerance=risk_tolerance,
    )

    results = scorer.score_and_rank(job.listings, criteria)
    return [_listing_to_deal(l, a) for l, a in results[:max_results]]


def register(mcp):
    @mcp.tool()
    async def start_search(
        budget_max: float = 500000,
        budget_min: float = 0,
        min_rooms: float = 2,
        min_size_sqm: float = 30,
        property_type: str = "apartment",
        districts: list[str] | None = None,
        max_results: int = 10,
        force_refresh: bool = False,
        ctx: Context = None,
    ) -> SearchJobStatus:
        """Start a Hamburg real estate deal search across all platforms.

        Kicks off scraping of Kleinanzeigen, Immowelt, Ohne-Makler, and
        ImmoScout24 in the background. Returns immediately with a job_id.
        Call get_search_results(job_id) to retrieve results (typically
        ready in 30-90 seconds).

        Returns cached results instantly if a matching search ran within
        the last 30 minutes. Set force_refresh=True to re-scrape.

        property_type: "apartment", "house", or "multi_family"
        districts: e.g. ["Altona", "Eimsbüttel"] or omit for all Hamburg
        """
        lc = ctx.request_context.lifespan_context
        job_store = lc["job_store"]
        scorer = lc["scorer"]

        criteria = _build_criteria(
            budget_max, budget_min, min_rooms, min_size_sqm,
            property_type, districts,
        )

        # Check cache
        if not force_refresh:
            cached_job = job_store.find_cached(criteria)
            if cached_job:
                deals = _score_job_results(
                    cached_job, scorer,
                    equity_pct=20, interest_rate_pct=3.5,
                    loan_term_years=25, risk_tolerance="moderate",
                    max_results=max_results,
                )
                return SearchJobStatus(
                    job_id=cached_job.job_id,
                    status="cached",
                    message=f"Found {len(deals)} cached results from {cached_job.completed_at:%H:%M}",
                    results=deals,
                )

        # Start new job
        scrapers = _create_scrapers()
        job = job_store.start_job(criteria, scrapers)

        return SearchJobStatus(
            job_id=job.job_id,
            status="running",
            message="Scraping 4 platforms: ImmoScout24, Kleinanzeigen, Immowelt, Ohne-Makler...",
        )

    @mcp.tool()
    async def get_search_results(
        job_id: str,
        equity_pct: float = 20,
        interest_rate_pct: float = 3.5,
        loan_term_years: int = 25,
        risk_tolerance: str = "moderate",
        max_results: int = 10,
        ctx: Context = None,
    ) -> SearchResult:
        """Get results from a search started with start_search.

        Returns the current state of the search job:
        - "running": scraping in progress, shows platforms completed so far
        - "completed": all results scored and ranked
        - "failed": all platforms failed

        Financing params (equity, rate, term) are applied at result time,
        so you can re-score the same search with different financing
        without re-scraping.

        risk_tolerance: "conservative" (cashflow focus), "moderate",
        "aggressive" (value-add/discount focus)
        """
        lc = ctx.request_context.lifespan_context
        job_store = lc["job_store"]
        scorer = lc["scorer"]

        job = job_store.get_job(job_id)
        if not job:
            return SearchResult(
                job_id=job_id,
                status="failed",
                progress="Job not found. It may have expired.",
            )

        # Score whatever listings we have so far
        deals = []
        if job.listings:
            deals = _score_job_results(
                job, scorer,
                equity_pct, interest_rate_pct, loan_term_years,
                risk_tolerance, max_results,
            )

        # Save to DB if completed
        if job.status == "completed" and deals:
            try:
                db = await get_db(ctx)
                for listing in job.listings:
                    d = listing.to_dict()
                    cols = ", ".join(d.keys())
                    placeholders = ", ".join(["?"] * len(d))
                    await db.execute(
                        f"INSERT OR REPLACE INTO listings ({cols}) VALUES ({placeholders})",
                        list(d.values()),
                    )
                await db.commit()
                await db.close()
            except Exception as e:
                logger.warning(f"Failed to save to DB: {e}")

        platform_errors = [
            PlatformError(platform=p, error="scraper_error", message=msg)
            for p, msg in job.platforms_failed
        ]

        return SearchResult(
            job_id=job.job_id,
            status=job.status,
            progress=job.progress_message,
            deals=deals,
            platforms_searched=job.platforms_done,
            platforms_failed=platform_errors,
            cached=False,
            search_duration_seconds=job.duration_seconds,
        )
