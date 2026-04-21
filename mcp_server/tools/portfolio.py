"""Portfolio management tools: saved deals, favorites."""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import Context

from mcp_server.models.outputs import DealResult
from mcp_server.context import get_db

logger = logging.getLogger(__name__)


def register(mcp):
    @mcp.tool()
    async def get_saved_deals(
        min_score: float = 0,
        property_type: str | None = None,
        district: str | None = None,
        limit: int = 20,
        ctx: Context = None,
    ) -> list[DealResult]:
        """Get previously found deals from the database without re-scraping.

        Use for quick lookups, revisiting earlier results, or filtering
        saved deals by score, type, or district. Returns deals ranked
        by score descending.
        """
        db = await get_db(ctx)
        try:
            query = """
                SELECT l.*, a.price_per_sqm as a_price_per_sqm,
                       a.district_avg_price_sqm, a.price_vs_market_pct,
                       a.gross_rental_yield_pct, a.monthly_cashflow,
                       a.deal_score, a.undervalue_reasons
                FROM listings l
                JOIN analysis_results a ON l.id = a.listing_id
                WHERE a.deal_score >= ?
            """
            params: list = [min_score]

            if property_type:
                query += " AND l.property_type = ?"
                params.append(property_type)
            if district:
                query += " AND l.district = ?"
                params.append(district)

            query += " ORDER BY a.deal_score DESC LIMIT ?"
            params.append(limit)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            deals = []
            for row in rows:
                d = dict(row)
                reasons = d.get("undervalue_reasons", "[]")
                if isinstance(reasons, str):
                    reasons = json.loads(reasons)

                desc = d.get("description", "") or ""
                deals.append(DealResult(
                    listing_id=d["id"],
                    title=d["title"],
                    url=d["url"],
                    platform=d["platform"],
                    price=d["price"],
                    size_sqm=d["size_sqm"],
                    rooms=d["rooms"],
                    district=d.get("district", ""),
                    address=d.get("address", ""),
                    property_type=d.get("property_type", "apartment"),
                    description_snippet=desc[:200] if desc else None,
                    deal_score=d.get("deal_score", 0),
                    price_per_sqm=d.get("a_price_per_sqm", 0),
                    price_vs_market_pct=d.get("price_vs_market_pct", 0),
                    gross_yield_pct=d.get("gross_rental_yield_pct", 0),
                    monthly_cashflow=d.get("monthly_cashflow", 0),
                    undervalue_reasons=reasons,
                ))

            return deals
        finally:
            await db.close()

    @mcp.tool()
    async def manage_saved_deals(
        action: str,
        listing_ids: list[str],
        ctx: Context = None,
    ) -> str:
        """Manage saved deals: delete stale listings or mark as sold.

        action: "delete" removes listings, "mark_sold" flags them
        listing_ids: list of listing IDs from search results
        """
        db = await get_db(ctx)
        try:
            if action == "delete":
                placeholders = ", ".join(["?"] * len(listing_ids))
                await db.execute(
                    f"DELETE FROM analysis_results WHERE listing_id IN ({placeholders})",
                    listing_ids,
                )
                await db.execute(
                    f"DELETE FROM listings WHERE id IN ({placeholders})",
                    listing_ids,
                )
                await db.commit()
                return f"Deleted {len(listing_ids)} listings."
            elif action == "mark_sold":
                placeholders = ", ".join(["?"] * len(listing_ids))
                await db.execute(
                    f"UPDATE listings SET condition = 'sold' WHERE id IN ({placeholders})",
                    listing_ids,
                )
                await db.commit()
                return f"Marked {len(listing_ids)} listings as sold."
            else:
                return f"Unknown action: {action}. Use 'delete' or 'mark_sold'."
        finally:
            await db.close()
