-- Migration 004: user_criteria.city (CITY-SUPPORT-1)
--
-- Adds the city slug column so that ``UserCriteria.to_dict()`` (which
-- now includes ``city``) can round-trip through ``save_user_criteria``
-- without sqlite raising "no such column: city". Default 'hamburg'
-- preserves existing rows' behaviour exactly — they were always
-- Hamburg-only before.
--
-- The bot-side user_criteria table is the only persistence path that
-- writes UserCriteria as a row; the daily digest builds UserCriteria
-- from YAML and never persists it. Adding the column here keeps the
-- bot flow alive after the dataclass grew a new field.

ALTER TABLE user_criteria ADD COLUMN city TEXT NOT NULL DEFAULT 'hamburg';
