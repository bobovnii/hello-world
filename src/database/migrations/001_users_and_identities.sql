-- Migration 001: users + telegram_identities
--
-- Introduces a stable, frontend-agnostic user identity (UUID) decoupled from
-- Telegram's chat_id. telegram_identities maps a chat_id to a user_id so
-- future frontends (email/OAuth) can attach to the same user without schema
-- churn. See FLAVOR_B_DESIGN.md §5.1.
--
-- NOTE: user_criteria.chat_id is intentionally NOT renamed in this migration.
-- A later phase (once handlers read the identity table) will cut over
-- user_criteria to user_id. Until then, chat_id remains the link key in
-- user_criteria to keep existing code paths working.

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,       -- UUID v4, opaque
    created_at TEXT NOT NULL,
    blocked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS telegram_identities (
    chat_id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    telegram_username TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_telegram_identities_user
    ON telegram_identities(user_id);
