"""Hamburg Real Estate Deal Finder - MCP Server.

Provides AI agents with tools to search, analyze, and compare
Hamburg real estate investment opportunities.

Usage:
    # stdio (Claude Desktop / Claude Code)
    python -m mcp_server.server

    # HTTP (remote agents / MCP Inspector)
    python -m mcp_server.server --http

    # Debug with MCP Inspector
    mcp dev mcp_server/server.py
"""

from __future__ import annotations

import json
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP
from mcp_server.context import app_lifespan

mcp = FastMCP(
    "Hamburg Real Estate Deals",
    lifespan=app_lifespan,
    json_response=True,
)

# --- Register tools from modules ---
from mcp_server.tools import market, search, analyze, portfolio

market.register(mcp)
search.register(mcp)
analyze.register(mcp)
portfolio.register(mcp)


# --- Resources (static reference data) ---

@mcp.resource("market://hamburg/districts")
def districts_overview() -> str:
    """All Hamburg districts with average prices, rents, yields, and trends."""
    from src.analyzer.market_data import HamburgMarketData
    market_data = HamburgMarketData(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "hamburg_market_data.json")
    )
    districts = {}
    for name in market_data.districts:
        data = market_data.get_district_data(name)
        if data:
            districts[name] = {
                "avg_price_sqm": data["avg_price_sqm_buy"],
                "avg_rent_sqm": data["avg_rent_sqm_month"],
                "avg_yield_pct": data["avg_gross_yield_pct"],
                "trend": data.get("trend", "stable"),
            }
    return json.dumps(districts, indent=2)


@mcp.resource("market://hamburg/purchase-costs")
def purchase_costs() -> str:
    """Property purchase cost breakdown for Hamburg."""
    return json.dumps({
        "grunderwerbsteuer": "5.5% (property transfer tax)",
        "notar": "1.5% (notary fees)",
        "grundbuch": "0.5% (land registry)",
        "makler": "3.57% (broker commission, if applicable)",
        "total": "11.07%",
        "note": "Ohne-Makler listings save the 3.57% broker commission",
    }, indent=2)


@mcp.resource("config://platforms")
def platform_status() -> str:
    """Available scraping platforms and notes."""
    return json.dumps({
        "platforms": [
            {"name": "kleinanzeigen", "status": "active", "note": "Best for private sellers and apartments"},
            {"name": "immowelt", "status": "active", "note": "Good for houses and new construction"},
            {"name": "ohne-makler", "status": "active", "note": "Commission-free listings only"},
            {"name": "immoscout", "status": "requires_residential_ip", "note": "Blocked by AWS WAF from datacenter IPs. Works from home network."},
        ]
    }, indent=2)


# --- Prompt templates ---

@mcp.prompt(title="Investment Search")
def investment_search(budget: str, requirements: str) -> str:
    """Guided workflow for finding investment properties in Hamburg."""
    return (
        f"The user is looking for a real estate investment in Hamburg.\n"
        f"Budget: {budget}\n"
        f"Requirements: {requirements}\n\n"
        f"Follow this workflow:\n"
        f"1. Call get_market_data() to understand which districts fit the budget\n"
        f"2. Call start_search() with appropriate criteria\n"
        f"3. Wait a moment, then call get_search_results(job_id) to get ranked deals\n"
        f"4. For the top 2-3 deals, call analyze_listing(listing_id) for deep dives\n"
        f"5. Present a comparison and your investment recommendation\n"
        f"6. Ask if they want to adjust criteria or explore further"
    )


def main():
    transport = "stdio"
    if "--http" in sys.argv:
        transport = "streamable-http"
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
