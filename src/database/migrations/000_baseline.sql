-- Migration 000: baseline schema (listings, analysis_results, user_criteria)
--
-- This is the schema as it shipped before the Flavor B migration framework.
-- Pre-existing databases that were initialized by the old _create_tables()
-- helper already have these tables; the IF NOT EXISTS clauses make the
-- migration a no-op for them. Fresh databases get the tables created here.
--
-- See FLAVOR_B_DESIGN.md §5.5: ALL schema — including baseline — lives in
-- numbered migration files so there is exactly one code path to create tables.

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
