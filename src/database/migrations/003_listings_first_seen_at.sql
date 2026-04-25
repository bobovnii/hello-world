-- Migration 003: listings.first_seen_at
--
-- Adds a "first time we ever scraped this listing" timestamp to listings so
-- the digest can show "time on market" without needing a separate listing
-- history table. Distinct from `scraped_at`, which is overwritten on every
-- re-scrape (and therefore can't answer "when did we first see it").
--
-- Backfill: for rows that pre-date this migration, copy `scraped_at` into
-- `first_seen_at` so they don't all read as "0 days on market" right after
-- the migration runs. New inserts (from save_listing) populate the column
-- explicitly; updates leave it untouched. See FLAVOR_B_DESIGN §5.5.
--
-- Honesty caveat (digest-side): this is "time since we first saw it", NOT
-- "time since the listing was originally published". The scraper's own
-- `listing_date` is unreliable / often a relative phrase, so we avoid
-- conflating the two.

ALTER TABLE listings ADD COLUMN first_seen_at TEXT NOT NULL DEFAULT '';

-- COALESCE guards against legacy rows where scraped_at is NULL — without it,
-- the NOT NULL DEFAULT '' on first_seen_at would raise IntegrityError, the
-- whole migration would roll back, and startup would halt. The column DEFAULT
-- is '' and Listing.days_on_market clamps '' to 0 days, so this fallback is
-- safe (just means "we don't know when we first saw it").
UPDATE listings SET first_seen_at = COALESCE(scraped_at, '') WHERE first_seen_at = '';
