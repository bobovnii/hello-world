# MCP Server Plan: Hamburg Real Estate Deal Finder (v2 - post architect review)

## Research Summary

### MCP SDK (Python `mcp` 1.26.0)
- **FastMCP** is the high-level API: `@mcp.tool()`, `@mcp.resource()`, `@mcp.prompt()`
- **Structured output** via Pydantic models with `json_response=True`
- **Lifespan pattern** for DB/shared state init (asynccontextmanager yielding a dict)
- **Context** object gives tools access to lifespan state + `ctx.report_progress()`
- **Transports**: stdio (local), streamable-http (production), SSE (legacy)
- **Registration**: `claude mcp add <name> -- python server.py` (stdio) or `--transport http <url>` (HTTP)

### Architect Review - Critical Issues Fixed

| # | Severity | Issue | Fix Applied |
|---|----------|-------|-------------|
| 1 | CRITICAL | `search_deals` blocks 30-120s, causes MCP client timeouts | Split into `start_search` + `get_search_results` async job pattern |
| 2 | CRITICAL | SQLite not thread-safe with `stateless_http=True` | Use `aiosqlite` + connection-per-request pattern |
| 3 | CRITICAL | No caching - every search re-scrapes all platforms | TTL cache (30min default) with `force_refresh` param |
| 4 | IMPORTANT | `explain_deal` anti-pattern - LLM generates text better | Removed. Agent synthesizes from structured data |
| 5 | IMPORTANT | `compare_districts` overlaps `get_market_data` | Merged into one tool with multi-district support |
| 6 | IMPORTANT | No error model for scraper failures | Added `SearchResult` with `platforms_failed` field |
| 7 | IMPORTANT | `DealResult` missing description snippet | Added `description_snippet` (200 chars) |
| 8 | IMPORTANT | `search_deals` has 12 params - too many for LLM | Split search params from financing (6 search params) |
| 9 | NICE | No rate-limit coordination across clients | Server-wide rate limiter in lifespan context |
| 10 | NICE | No MCP-level abuse prevention | Per-tool rate limits + daily search cap |
| 11 | NICE | Hamburg-specific, not reusable | Acknowledge; future CityConfig abstraction |

## Architecture (Final)

```
mcp_server/
├── server.py                # FastMCP entry point, tool registration, CLI
├── tools/
│   ├── __init__.py
│   ├── search.py            # start_search, get_search_results (async job)
│   ├── analyze.py           # analyze_listing, estimate_investment
│   ├── market.py            # get_market_data (single + compare + overview)
│   └── portfolio.py         # get_saved_deals, manage_saved_deals
├── models/
│   ├── __init__.py
│   └── outputs.py           # All Pydantic response models
├── context.py               # Lifespan: aiosqlite DB, market data, rate limiter, job store
├── jobs.py                  # Async search job runner (background scraping)
└── db_async.py              # Async SQLite wrapper (aiosqlite)
```

## Final Tool Surface (7 tools)

### Tool 1: `start_search` (< 1s response)
```python
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
    ctx: Context,
) -> SearchJobStatus:
    """Start a Hamburg real estate deal search across all platforms.

    Kicks off scraping of Kleinanzeigen, Immowelt, Ohne-Makler, and
    ImmoScout24 in the background. Returns immediately with a job_id.
    Call get_search_results(job_id) to get results (typically ready
    in 30-90 seconds).

    Returns cached results instantly if a matching search ran within
    the last 30 minutes. Set force_refresh=True to re-scrape.

    property_type: "apartment", "house", or "multi_family"
    districts: e.g. ["Altona", "Eimsbüttel"] or omit for all Hamburg
    """
```

**Why async**: Scraping takes 30-120s. MCP clients timeout. This returns in <1s.
**Cache behavior**: Checks `search_runs` table. If matching criteria within TTL, returns `status: "completed"` with results immediately.

### Tool 2: `get_search_results` (< 1s response)
```python
@mcp.tool()
async def get_search_results(
    job_id: str,
    equity_pct: float = 20,
    interest_rate_pct: float = 3.5,
    loan_term_years: int = 25,
    risk_tolerance: str = "moderate",
) -> SearchResult:
    """Get results from a search started with start_search.

    Returns the current state of the search job:
    - "running": scraping in progress, shows platforms completed so far
    - "completed": all results scored and ranked
    - "failed": all platforms failed

    Financing params (equity, rate, term) are applied at result time,
    so you can re-score the same search with different financing
    without re-scraping. risk_tolerance adjusts scoring weights:
    "conservative" (cashflow focus), "moderate", "aggressive" (value-add).
    """
```

**Why separate from start_search**: Decouples search criteria from analysis params. Agent can re-score same results with different financing ("what if 30% equity?") without re-scraping.

### Tool 3: `analyze_listing` (< 2s)
```python
@mcp.tool()
async def analyze_listing(
    listing_id: str | None = None,
    listing_url: str | None = None,
    equity_pct: float = 20,
    interest_rate_pct: float = 3.5,
    loan_term_years: int = 25,
) -> DealAnalysis:
    """Get detailed financial analysis for a specific property.

    Provide a listing_id from search results, or a URL to scrape fresh.
    Returns complete investment breakdown: price vs market, rental yield,
    cap rate, monthly cashflow, cash-on-cash return, equity needed,
    mortgage payment, full description, and all undervalue signals.
    """
```

### Tool 4: `get_market_data` (instant)
```python
@mcp.tool()
def get_market_data(
    districts: list[str] | None = None,
) -> MarketOverview:
    """Get Hamburg real estate market data for investment context.

    - No districts: city-wide overview of all 7 districts
    - One district: detailed data for that district
    - Multiple districts: side-by-side comparison

    Returns: avg price/m², avg rent/m², gross yield %, market trend,
    and purchase cost breakdown (Grunderwerbsteuer, Notar, etc.).

    Districts: Altona, Eimsbüttel, Hamburg-Mitte, Hamburg-Nord,
    Wandsbek, Bergedorf, Harburg.
    """
```

**Merged**: Previously `get_market_data` + `compare_districts`. One tool handles all cases based on input.

### Tool 5: `estimate_investment` (instant)
```python
@mcp.tool()
def estimate_investment(
    purchase_price: float,
    size_sqm: float,
    district: str,
    equity_pct: float = 20,
    interest_rate_pct: float = 3.5,
    loan_term_years: int = 25,
    monthly_hausgeld: float = 0,
) -> InvestmentEstimate:
    """Calculate investment returns for a hypothetical property.

    No listing needed - just plug in numbers for "what if" scenarios.
    "What would a 200k, 60m² apartment in Harburg yield?"
    Returns: estimated rent, mortgage, cashflow, yield, equity needed.
    """
```

### Tool 6: `get_saved_deals` (instant)
```python
@mcp.tool()
async def get_saved_deals(
    min_score: float = 0,
    property_type: str | None = None,
    district: str | None = None,
    limit: int = 20,
) -> list[DealResult]:
    """Get previously found deals from the database without re-scraping.

    Use for quick lookups, revisiting earlier results, or filtering
    previously scraped deals by new criteria.
    """
```

### Tool 7: `manage_saved_deals` (instant)
```python
@mcp.tool()
async def manage_saved_deals(
    action: str,
    listing_ids: list[str],
) -> str:
    """Manage saved deals: mark as favorite, sold, or delete.

    action: "favorite", "mark_sold", or "delete"
    """
```

## Resources (3 static endpoints)

```python
@mcp.resource("market://hamburg/districts")
def districts_overview() -> str:
    """All Hamburg districts with avg prices, rents, yields, trends."""

@mcp.resource("market://hamburg/purchase-costs")
def purchase_costs() -> str:
    """Property purchase cost breakdown: 5.5% tax, 1.5% notary, 0.5% registry, 3.57% broker = 11.07% total."""

@mcp.resource("config://platforms")
def platform_status() -> str:
    """Available scraping platforms and their current status."""
```

## Prompts (1 workflow template)

```python
@mcp.prompt(title="Investment Search")
def investment_search(budget: str, requirements: str) -> str:
    """Guided investment search workflow."""
    return (
        f"User wants to invest in Hamburg real estate.\n"
        f"Budget: {budget}\nRequirements: {requirements}\n\n"
        f"Steps:\n"
        f"1. get_market_data() - understand which districts fit\n"
        f"2. start_search() - find matching deals\n"
        f"3. get_search_results() - retrieve and present top deals\n"
        f"4. analyze_listing() - deep dive on promising ones\n"
        f"5. Present comparison and recommendation"
    )
```

## Pydantic Output Models

```python
class SearchJobStatus(BaseModel):
    """Returned by start_search."""
    job_id: str
    status: str  # "running" | "completed" | "cached"
    message: str  # "Scraping 4 platforms..." or "Found 12 cached results"
    # If cached/completed, results are included directly:
    results: list[DealResult] | None = None

class SearchResult(BaseModel):
    """Returned by get_search_results."""
    job_id: str
    status: str  # "running" | "completed" | "failed"
    progress: str  # "2/4 platforms done, 8 listings found"
    deals: list[DealResult]
    platforms_searched: list[str]
    platforms_failed: list[PlatformError]
    cached: bool
    search_duration_seconds: float | None

class PlatformError(BaseModel):
    platform: str
    error: str  # "blocked_by_waf" | "timeout" | "rate_limited" | "no_results"
    message: str

class DealResult(BaseModel):
    """One deal in search results."""
    listing_id: str
    title: str
    url: str
    platform: str
    price: float = Field(description="Purchase price EUR")
    size_sqm: float
    rooms: float
    district: str
    address: str
    description_snippet: str | None = Field(None, description="First 200 chars of description")
    deal_score: float = Field(description="0-100 investment score")
    price_per_sqm: float
    price_vs_market_pct: float = Field(description="Negative = below market avg")
    gross_yield_pct: float
    monthly_cashflow: float = Field(description="After mortgage payment")
    undervalue_reasons: list[str]

class DealAnalysis(BaseModel):
    """Full analysis from analyze_listing."""
    listing: DealResult
    total_purchase_cost: float
    equity_required: float
    mortgage_monthly: float
    estimated_rent_monthly: float
    net_yield_pct: float
    cap_rate_pct: float
    cash_on_cash_return_pct: float
    district_avg_price_sqm: float
    year_built: int | None
    condition: str | None
    description: str | None  # Full description (up to 2000 chars)
    property_type: str
    features: list[str]  # ["balcony", "garden", "parking"]

class MarketOverview(BaseModel):
    """From get_market_data."""
    districts: list[DistrictData]
    purchase_costs_pct: dict[str, float]
    total_purchase_costs_pct: float

class DistrictData(BaseModel):
    name: str
    avg_price_sqm: float
    avg_rent_sqm: float
    avg_gross_yield_pct: float
    trend: str
    description: str

class InvestmentEstimate(BaseModel):
    """From estimate_investment."""
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
```

## Async Job System (jobs.py)

```python
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class SearchJob:
    job_id: str
    criteria: UserCriteria
    status: str = "running"  # running | completed | failed
    listings: list[Listing] = field(default_factory=list)
    platforms_done: list[str] = field(default_factory=list)
    platforms_failed: list[tuple[str, str]] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None

class JobStore:
    """In-memory job store with TTL-based cache lookup."""

    def __init__(self, cache_ttl_minutes: int = 30):
        self._jobs: dict[str, SearchJob] = {}
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._cache_ttl = cache_ttl_minutes

    def find_cached(self, criteria: UserCriteria) -> SearchJob | None:
        """Find a completed job with matching criteria within TTL."""
        ...

    async def start_job(self, criteria: UserCriteria, scrapers: list) -> SearchJob:
        """Start scraping in background thread, return job immediately."""
        job = SearchJob(job_id=str(uuid.uuid4())[:8], criteria=criteria)
        self._jobs[job.job_id] = job
        asyncio.get_event_loop().run_in_executor(
            self._executor, self._run_scrapers, job, scrapers
        )
        return job

    def _run_scrapers(self, job: SearchJob, scrapers: list):
        """Runs in background thread."""
        for scraper in scrapers:
            try:
                listings = scraper.search(job.criteria, max_pages=3)
                job.listings.extend(listings)
                job.platforms_done.append(scraper.PLATFORM_NAME)
            except Exception as e:
                job.platforms_failed.append((scraper.PLATFORM_NAME, str(e)))
            finally:
                scraper.close()
        job.status = "completed"
        job.completed_at = datetime.now()
```

## Lifespan (context.py)

```python
from contextlib import asynccontextmanager
import aiosqlite
import threading

@asynccontextmanager
async def app_lifespan(server):
    # Thread-safe rate limiter shared across all tool calls
    rate_limiter = ServerRateLimiter(max_searches_per_hour=10)

    market_data = HamburgMarketData()
    scorer = DealScorer(market_data)
    job_store = JobStore(cache_ttl_minutes=30)
    db = await aiosqlite.connect("data/deals.db")

    yield {
        "db": db,
        "market_data": market_data,
        "scorer": scorer,
        "job_store": job_store,
        "rate_limiter": rate_limiter,
    }

    await db.close()
    job_store._executor.shutdown(wait=False)
```

## Server Entry Point (server.py)

```python
from mcp.server.fastmcp import FastMCP
from mcp_server.context import app_lifespan

mcp = FastMCP(
    "Hamburg Real Estate Deals",
    lifespan=app_lifespan,
    json_response=True,
)

# Import tools (they register via @mcp.tool() decorators)
from mcp_server.tools import search, analyze, market, portfolio

if __name__ == "__main__":
    import sys
    transport = "stdio"
    if "--http" in sys.argv:
        transport = "streamable-http"
    mcp.run(transport=transport)
```

## Deployment Options

### Local (Claude Code / Claude Desktop)
```bash
pip install "mcp[cli]" aiosqlite

# stdio (simplest)
claude mcp add hamburg-deals -- python -m mcp_server.server

# HTTP (for testing with MCP Inspector)
python -m mcp_server.server --http
npx @modelcontextprotocol/inspector  # connect to http://localhost:8000/mcp
```

### Claude Desktop config
```json
{
  "mcpServers": {
    "hamburg-deals": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/hello-world"
    }
  }
}
```

### Remote (multi-agent)
```bash
# Docker
docker run -p 8000:8000 hamburg-deals --http

# Any MCP client connects to http://host:8000/mcp
```

## Implementation Order

1. `pip install "mcp[cli]" aiosqlite pydantic` - dependencies
2. `mcp_server/models/outputs.py` - all Pydantic models above
3. `mcp_server/jobs.py` - async job store with cache
4. `mcp_server/context.py` - lifespan wiring
5. `mcp_server/tools/market.py` - `get_market_data`, `estimate_investment` (instant, no scraping)
6. `mcp_server/tools/search.py` - `start_search`, `get_search_results` (the big one)
7. `mcp_server/tools/analyze.py` - `analyze_listing`
8. `mcp_server/tools/portfolio.py` - `get_saved_deals`, `manage_saved_deals`
9. `mcp_server/server.py` - wire tools + resources + prompts
10. Test with `mcp dev mcp_server/server.py` (MCP Inspector)
11. README: registration instructions for Claude Code, Claude Desktop, HTTP

## Future: Reusability for Other Cities

The current plan is Hamburg-specific. To reuse for Berlin/Munich/etc:
- Extract `CityConfig(districts, market_data_path, scraper_urls)` abstraction
- Deploy one MCP server instance per city
- Tool interfaces stay identical, only the data changes
- This is a v2 concern, not needed for initial implementation

## Verification Criteria
1. All 7 tools callable via MCP Inspector (`mcp dev`)
2. `start_search` returns in < 1s, `get_search_results` shows progress
3. Cache hit returns instantly (< 100ms)
4. Multiple concurrent agent sessions don't corrupt DB
5. Scraper failures reported clearly in `platforms_failed`
6. `claude mcp add` works for both stdio and HTTP transport
