"""Pydantic output models for all MCP tools."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlatformError(BaseModel):
    """A scraper platform that failed during search."""
    platform: str
    error: str = Field(description="Error type: blocked_by_waf, timeout, rate_limited, no_results, parse_error")
    message: str


class DealResult(BaseModel):
    """One deal in search results, scored and ranked."""
    listing_id: str
    title: str
    url: str
    platform: str
    price: float = Field(description="Purchase price in EUR")
    size_sqm: float
    rooms: float
    district: str
    address: str
    property_type: str = "apartment"
    description_snippet: str | None = Field(None, description="First 200 chars of listing description")
    deal_score: float = Field(description="Investment score 0-100, higher is better deal")
    price_per_sqm: float
    price_vs_market_pct: float = Field(description="Negative means below district average")
    gross_yield_pct: float
    monthly_cashflow: float = Field(description="Monthly cashflow after mortgage, positive = profit")
    undervalue_reasons: list[str] = Field(default_factory=list)


class SearchJobStatus(BaseModel):
    """Returned by start_search."""
    job_id: str
    status: str = Field(description="running, completed, or cached")
    message: str
    results: list[DealResult] | None = None


class SearchResult(BaseModel):
    """Returned by get_search_results."""
    job_id: str
    status: str = Field(description="running, completed, or failed")
    progress: str = Field(description="e.g. '2/4 platforms done, 8 listings found'")
    deals: list[DealResult] = Field(default_factory=list)
    platforms_searched: list[str] = Field(default_factory=list)
    platforms_failed: list[PlatformError] = Field(default_factory=list)
    cached: bool = False
    search_duration_seconds: float | None = None


class DealAnalysis(BaseModel):
    """Full financial analysis for one listing."""
    listing: DealResult
    total_purchase_cost: float
    equity_required: float
    mortgage_monthly: float
    estimated_rent_monthly: float
    net_yield_pct: float
    cap_rate_pct: float
    cash_on_cash_return_pct: float
    district_avg_price_sqm: float
    year_built: int | None = None
    condition: str | None = None
    description: str | None = Field(None, description="Full description up to 2000 chars")
    property_type: str = "apartment"
    features: list[str] = Field(default_factory=list)


class DistrictData(BaseModel):
    """Market data for one Hamburg district."""
    name: str
    avg_price_sqm: float
    avg_rent_sqm: float
    avg_gross_yield_pct: float
    trend: str = Field(description="rising, stable, or declining")
    description: str


class MarketOverview(BaseModel):
    """Market data overview, single district, or comparison."""
    districts: list[DistrictData]
    purchase_costs_pct: dict[str, float]
    total_purchase_costs_pct: float


class InvestmentEstimate(BaseModel):
    """What-if investment calculation result."""
    purchase_price: float
    total_cost: float
    equity_required: float
    mortgage_monthly: float
    estimated_rent_monthly: float
    monthly_cashflow: float
    gross_yield_pct: float
    net_yield_pct: float
    cash_on_cash_return_pct: float
    district: str
    district_avg_price_sqm: float
    price_vs_market_pct: float
