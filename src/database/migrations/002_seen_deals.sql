-- Migration 002: seen_deals for Telegram channel digest dedup
--
-- Tracks which listings have already been posted to a Telegram channel so the
-- daily digest doesn't repeat deals. Keyed by (channel, listing_id).
-- See docs/MVP_TELEGRAM_DIGEST.md.
--
-- NOTE: channel here is the Telegram channel identifier from config
-- (e.g. "@HamburgDealsDemo" or numeric chat_id as string). It is
-- intentionally NOT linked to users/telegram_identities — channel posts
-- are not per-user, they are per-config-target.

CREATE TABLE IF NOT EXISTS seen_deals (
    channel TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    search_slug TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (channel, listing_id),
    FOREIGN KEY (listing_id) REFERENCES listings(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_seen_deals_channel_time
    ON seen_deals(channel, seen_at DESC);
