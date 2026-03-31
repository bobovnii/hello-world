"""MCP server lifespan: shared resources initialization."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from src.analyzer.market_data import HamburgMarketData
from src.analyzer.scorer import DealScorer
from mcp_server.jobs import JobStore


# Resolve paths relative to project root
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "deals.db"
MARKET_DATA_PATH = DATA_DIR / "hamburg_market_data.json"


@asynccontextmanager
async def app_lifespan(server) -> AsyncIterator[dict]:
    """Initialize shared resources at server startup.

    Yields a dict that tools access via ctx.request_context.lifespan_context.
    Uses a connection factory (not a single connection) for thread safety.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    market_data = HamburgMarketData(str(MARKET_DATA_PATH))
    scorer = DealScorer(market_data)
    job_store = JobStore(cache_ttl_minutes=30)

    # Create tables on startup using a temporary connection
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS listings (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                price REAL NOT NULL,
                size_sqm REAL NOT NULL,
                rooms REAL NOT NULL,
                address TEXT DEFAULT '',
                district TEXT DEFAULT '',
                zip_code TEXT DEFAULT '',
                year_built INTEGER,
                property_type TEXT DEFAULT 'apartment',
                condition TEXT,
                floor INTEGER,
                hausgeld REAL,
                nebenkosten REAL,
                energy_rating TEXT,
                balcony INTEGER DEFAULT 0,
                garden INTEGER DEFAULT 0,
                parking INTEGER DEFAULT 0,
                listing_date TEXT,
                description TEXT,
                scraped_at TEXT NOT NULL,
                image_urls TEXT DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS analysis_results (
                listing_id TEXT PRIMARY KEY,
                price_per_sqm REAL, district_avg_price_sqm REAL,
                price_vs_market_pct REAL, estimated_rent_monthly REAL,
                gross_rental_yield_pct REAL, net_rental_yield_pct REAL,
                cap_rate_pct REAL, monthly_cashflow REAL,
                cash_on_cash_return_pct REAL, total_purchase_cost REAL,
                mortgage_monthly REAL, equity_required REAL,
                deal_score REAL, undervalue_reasons TEXT DEFAULT '[]',
                analyzed_at TEXT,
                FOREIGN KEY (listing_id) REFERENCES listings(id)
            );
            CREATE INDEX IF NOT EXISTS idx_listings_district ON listings(district);
            CREATE INDEX IF NOT EXISTS idx_analysis_score ON analysis_results(deal_score DESC);
        """)

    yield {
        "market_data": market_data,
        "scorer": scorer,
        "job_store": job_store,
        "db_path": str(DB_PATH),
    }

    job_store.shutdown()


async def get_db(ctx) -> aiosqlite.Connection:
    """Get a new async DB connection from lifespan context.

    Each tool call gets its own connection - safe for concurrent access.
    """
    lc = ctx.request_context.lifespan_context
    db = await aiosqlite.connect(lc["db_path"])
    db.row_factory = aiosqlite.Row
    return db
