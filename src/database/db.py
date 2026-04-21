"""SQLite database operations for listings, criteria, and analysis results."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Listing, UserCriteria, AnalysisResult


class Database:
    def __init__(self, db_path: str = "data/deals.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
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
                image_urls TEXT DEFAULT '[]',
                is_erbbaurecht INTEGER DEFAULT 0,
                is_rented INTEGER DEFAULT 0,
                current_rent_monthly REAL,
                is_wbs INTEGER DEFAULT 0,
                sonderumlage REAL,
                num_units_in_building INTEGER,
                is_dachgeschoss INTEGER DEFAULT 0,
                is_ausbau_needed INTEGER DEFAULT 0,
                total_floors INTEGER,
                plot_size_sqm REAL
            );

            CREATE TABLE IF NOT EXISTS analysis_results (
                listing_id TEXT PRIMARY KEY,
                price_per_sqm REAL,
                district_avg_price_sqm REAL,
                price_vs_market_pct REAL,
                estimated_rent_monthly REAL,
                gross_rental_yield_pct REAL,
                net_rental_yield_pct REAL,
                cap_rate_pct REAL,
                monthly_cashflow REAL,
                cash_on_cash_return_pct REAL,
                total_purchase_cost REAL,
                mortgage_monthly REAL,
                equity_required REAL,
                deal_score REAL,
                undervalue_reasons TEXT DEFAULT '[]',
                analyzed_at TEXT,
                FOREIGN KEY (listing_id) REFERENCES listings(id)
            );

            CREATE TABLE IF NOT EXISTS user_criteria (
                chat_id INTEGER PRIMARY KEY,
                budget_min REAL DEFAULT 0,
                budget_max REAL DEFAULT 1000000,
                min_size_sqm REAL DEFAULT 0,
                max_size_sqm REAL DEFAULT 999,
                min_rooms REAL DEFAULT 1,
                max_rooms REAL DEFAULT 10,
                property_types TEXT DEFAULT '["apartment"]',
                districts TEXT DEFAULT '[]',
                equity_pct REAL DEFAULT 20.0,
                interest_rate_pct REAL DEFAULT 3.5,
                loan_term_years INTEGER DEFAULT 25,
                min_gross_yield_pct REAL DEFAULT 4.0,
                min_cashflow_monthly REAL DEFAULT 0.0,
                risk_tolerance TEXT DEFAULT 'moderate'
            );

            CREATE INDEX IF NOT EXISTS idx_listings_district ON listings(district);
            CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(price);
            CREATE INDEX IF NOT EXISTS idx_analysis_score ON analysis_results(deal_score DESC);
        """)
        self.conn.commit()

    def save_listing(self, listing: Listing) -> bool:
        """Save or update a listing. Returns True if new, False if updated."""
        d = listing.to_dict()
        existing = self.conn.execute(
            "SELECT id FROM listings WHERE id = ?", (listing.id,)
        ).fetchone()

        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        updates = ", ".join(f"{k} = ?" for k in d.keys() if k != "id")

        if existing:
            vals = [v for k, v in d.items() if k != "id"] + [listing.id]
            self.conn.execute(
                f"UPDATE listings SET {updates} WHERE id = ?", vals
            )
        else:
            self.conn.execute(
                f"INSERT INTO listings ({cols}) VALUES ({placeholders})",
                list(d.values()),
            )
        self.conn.commit()
        return not existing

    def save_listings(self, listings: list[Listing]) -> tuple[int, int]:
        """Bulk save. Returns (new_count, updated_count)."""
        new, updated = 0, 0
        for listing in listings:
            if self.save_listing(listing):
                new += 1
            else:
                updated += 1
        return new, updated

    def get_listings(
        self,
        criteria: UserCriteria | None = None,
        limit: int = 100,
    ) -> list[Listing]:
        """Get listings optionally filtered by criteria."""
        query = "SELECT * FROM listings WHERE 1=1"
        params: list = []

        if criteria:
            query += " AND price >= ? AND price <= ?"
            params.extend([criteria.budget_min, criteria.budget_max])

            if criteria.min_size_sqm > 0:
                query += " AND size_sqm >= ?"
                params.append(criteria.min_size_sqm)

            if criteria.max_size_sqm < 999:
                query += " AND size_sqm <= ?"
                params.append(criteria.max_size_sqm)

            if criteria.min_rooms > 1:
                query += " AND rooms >= ?"
                params.append(criteria.min_rooms)

            if criteria.districts:
                placeholders = ", ".join(["?"] * len(criteria.districts))
                query += f" AND district IN ({placeholders})"
                params.extend(criteria.districts)

        query += " ORDER BY price ASC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(query, params).fetchall()
        return [Listing.from_dict(dict(row)) for row in rows]

    def save_analysis(self, result: AnalysisResult):
        d = result.to_dict()
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        self.conn.execute(
            f"INSERT OR REPLACE INTO analysis_results ({cols}) VALUES ({placeholders})",
            list(d.values()),
        )
        self.conn.commit()

    def get_top_deals(self, limit: int = 10) -> list[tuple[Listing, AnalysisResult]]:
        """Get top deals joined with analysis, sorted by score."""
        rows = self.conn.execute(
            """
            SELECT l.*, a.price_per_sqm as a_price_per_sqm,
                   a.district_avg_price_sqm, a.price_vs_market_pct,
                   a.estimated_rent_monthly, a.gross_rental_yield_pct,
                   a.net_rental_yield_pct, a.cap_rate_pct, a.monthly_cashflow,
                   a.cash_on_cash_return_pct, a.total_purchase_cost,
                   a.mortgage_monthly, a.equity_required,
                   a.deal_score, a.undervalue_reasons, a.analyzed_at
            FROM listings l
            JOIN analysis_results a ON l.id = a.listing_id
            ORDER BY a.deal_score DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            listing_fields = {
                k: d[k] for k in Listing.__dataclass_fields__ if k in d
            }
            listing = Listing.from_dict(listing_fields)

            analysis = AnalysisResult(
                listing_id=d["id"],
                price_per_sqm=d.get("a_price_per_sqm", 0),
                district_avg_price_sqm=d.get("district_avg_price_sqm", 0),
                price_vs_market_pct=d.get("price_vs_market_pct", 0),
                estimated_rent_monthly=d.get("estimated_rent_monthly", 0),
                gross_rental_yield_pct=d.get("gross_rental_yield_pct", 0),
                net_rental_yield_pct=d.get("net_rental_yield_pct", 0),
                cap_rate_pct=d.get("cap_rate_pct", 0),
                monthly_cashflow=d.get("monthly_cashflow", 0),
                cash_on_cash_return_pct=d.get("cash_on_cash_return_pct", 0),
                total_purchase_cost=d.get("total_purchase_cost", 0),
                mortgage_monthly=d.get("mortgage_monthly", 0),
                equity_required=d.get("equity_required", 0),
                deal_score=d.get("deal_score", 0),
                undervalue_reasons=d.get("undervalue_reasons", "[]"),
                analyzed_at=d.get("analyzed_at", ""),
            )
            if isinstance(analysis.undervalue_reasons, str):
                import json
                analysis.undervalue_reasons = json.loads(analysis.undervalue_reasons)
            results.append((listing, analysis))

        return results

    def save_user_criteria(self, criteria: UserCriteria):
        d = criteria.to_dict()
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        self.conn.execute(
            f"INSERT OR REPLACE INTO user_criteria ({cols}) VALUES ({placeholders})",
            list(d.values()),
        )
        self.conn.commit()

    def get_user_criteria(self, chat_id: int) -> UserCriteria | None:
        row = self.conn.execute(
            "SELECT * FROM user_criteria WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if row:
            return UserCriteria.from_dict(dict(row))
        return None

    def close(self):
        self.conn.close()
