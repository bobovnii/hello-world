# Flavor B: Personalized Telegram Bot for Hamburg Real Estate Deals

**Status**: V2 (revised after architect + developer review)
**Review history**: V1 received 5 CRITICAL + 10 concrete issues. See §16 for changelog.

---

## 1. Goal

Deliver **customized daily deal reports** to users via a Telegram bot.
Each user sets their criteria once, then receives a personalized digest of
new deals matching their profile. The scraper, scoring logic, and red-flag
analysis stay hidden — users only see curated results.

**Non-goals** (for this MVP):
- Public web interface
- Payment/subscription flow
- Multi-city support (Hamburg only)
- Real-time alerts (daily is enough)
- i18n (single-locale, strings extracted for later)

## 2. Why this over public channel (Flavor A)

- Higher value-per-user: personalization > one-size-fits-all
- Sticky: users invest in setting criteria → higher retention
- Easier to monetize later: upgrade existing users to paid tier
- Better data: we learn what criteria users actually use

## 3. User-facing flow

```
User                          Bot
───                           ───
/start                   →    Welcome. Quick 1-min setup?
                         ←    Inline button: [Start]
                         ←    Show example digest (fake)

/criteria                →    Walk through budget, rooms, size,
                              districts, financing (existing flow)
                         ←    "Criteria saved. First digest tomorrow 08:00 CET,
                              or /search for instant results"
                         ←    Run immediate cold-start search, send up to 5
                              current matches marked as seen

/search                  →    Immediate search (all scrapers, serialized)
                         ←    Top 5 deals, 1 message per deal

/alerts on               →    Enable daily digest at 08:00 CET
/alerts off              →    Pause
/alerts weekly           →    Switch to weekly (Mon 08:00 CET)

/deal 1                  →    Full analysis for deal #1 from last digest
/export                  →    CSV of last 30 days of deals matching criteria
                              (from DB, not session memory)
/stats                   →    Your usage: X deals seen, Y alerts received
/help                    →    Command list + FAQ + disclaimer

Admin (gated by ADMIN_CHAT_IDS env):
/admin_stats             →    Active users, digest runs, scraper health
/admin_ban <chat_id>     →    Blocklist a user
/admin_health            →    Last digest success/fail, scraper counters
/admin_broadcast <msg>   →    Message all opted-in users (rare, e.g. outage)

Every morning 08:00 Europe/Berlin (for opted-in users):
                         ←    "Good morning! 3 new deals matching
                              your criteria:"
                         ←    Deal 1 (price, score, flags, link)
                         ←    Deal 2 ...
                         ←    "Reply /deal <n> for full analysis"
```

## 4. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    TELEGRAM CLIENT (phone)                   │
└──────────────────────┬──────────────────────────────────────┘
                       │ Bot API (polling or webhook)
┌──────────────────────▼──────────────────────────────────────┐
│                     BOT PROCESS (always-on)                  │
│  python-telegram-bot[job-queue] Application                  │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────┐  │
│  │ Command handlers│  │ ConversationFlow │  │ JobQueue   │  │
│  │ thin adapters   │  │ /criteria setup  │  │ run_daily  │  │
│  │ only — no logic │  │                  │  │ 08:00 CET  │  │
│  └────────┬────────┘  └────────┬─────────┘  └─────┬──────┘  │
│           └───────────┬────────┴───────────┬──────┘         │
│                       ▼                    ▼                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                  SERVICE LAYER                        │  │
│  │  DealService: search() · digest_run() · export()      │  │
│  │  UserService: upsert() · link_telegram() · opt_in()   │  │
│  │  AdminService: stats() · ban() · health()             │  │
│  └──────────────────────────┬────────────────────────────┘  │
│                             ▼                               │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  scrapers · analyzer · scorer · DB (SQLite WAL)       │  │
│  │  Connection-per-thread via threading.local            │  │
│  │  + global asyncio.Semaphore on scrapers               │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
         │                                       │
         ▼                                       ▼
   healthchecks.io                        ImmoScout/Kleinanzeigen/
   (dead-man switch)                      Immowelt/Ohne-Makler
```

**Key architectural rules**:
- Command handlers contain NO business logic — call a Service method, format response.
- Services own orchestration (scrape → save → score).
- DB layer owns persistence only. No Service calls into DB module's higher-level operations.
- All user-visible strings in `src/bot/strings.py` keyed by locale (only `en` for now).

## 5. Data model

### 5.1 Identity: users + telegram_identities (foundational for future multi-frontend)

```sql
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,      -- UUID, not chat_id
    created_at TEXT NOT NULL,
    blocked INTEGER DEFAULT 0
);

CREATE TABLE telegram_identities (
    chat_id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    telegram_username TEXT DEFAULT '',
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

`user_criteria.user_id` replaces `chat_id` as the link key. Cost now: ~30 LOC.
Value later: email/OAuth migration is a single new `user_credentials` table.

### 5.2 Extend user_criteria

```sql
-- Applied via migration #1
ALTER TABLE user_criteria ADD COLUMN alerts_enabled INTEGER DEFAULT 0;
--                                                                ^^^
--    DEFAULT 0, not 1. Existing users never persisted opt-in; don't spam.
ALTER TABLE user_criteria ADD COLUMN alerts_frequency TEXT DEFAULT 'daily';
ALTER TABLE user_criteria ADD COLUMN language TEXT DEFAULT 'en';
ALTER TABLE user_criteria ADD COLUMN created_at TEXT DEFAULT '';
ALTER TABLE user_criteria ADD COLUMN last_digest_at TEXT DEFAULT '';
```

### 5.3 seen_deals — prevent duplicates

```sql
CREATE TABLE seen_deals (
    user_id TEXT NOT NULL,         -- FK to users, not chat_id
    listing_id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (user_id, listing_id),
    FOREIGN KEY (listing_id) REFERENCES listings(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
CREATE INDEX idx_seen_user_time ON seen_deals(user_id, seen_at DESC);
CREATE INDEX idx_seen_listing ON seen_deals(listing_id);
```

Scale check: 100 users × 50 deals/day × 180 days = **900 000 rows** (~30 MB in SQLite). Fine unbounded for year 1.

### 5.4 Resumable digest runs

```sql
CREATE TABLE digest_runs (
    run_id TEXT PRIMARY KEY,          -- UUID
    started_at TEXT NOT NULL,
    completed_at TEXT,
    listings_scraped INTEGER DEFAULT 0,
    status TEXT DEFAULT 'in_progress' -- in_progress | completed | failed
);

CREATE TABLE digest_deliveries (
    run_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    status TEXT DEFAULT 'pending',    -- pending | sent | failed | skipped
    deals_sent INTEGER DEFAULT 0,
    error TEXT,
    PRIMARY KEY (run_id, user_id),
    FOREIGN KEY (run_id) REFERENCES digest_runs(run_id)
);
```

On bot restart: any `digest_runs` with `status='in_progress'` older than 10
minutes is marked `failed`; pending deliveries are re-attempted in a new run.
This prevents partial digests + silent user skips.

### 5.5 Schema migrations — PRAGMA user_version

Do NOT use `ALTER TABLE ADD COLUMN IF NOT EXISTS` — not reliably supported in
SQLite. Use introspection via `PRAGMA table_info` + numbered migration files:

```
src/database/migrations/
  001_users_and_identities.sql
  002_user_criteria_alerts_fields.sql
  003_seen_deals.sql
  004_digest_runs.sql
```

Applied on startup by `_migrate()`, ordered, tracked via `PRAGMA user_version`.
Failures raise and halt the bot — no silent half-migrations.

### 5.6 SQLite connection rules (applied on EVERY connection)

```sql
PRAGMA journal_mode=WAL;      -- multiple readers + 1 writer
PRAGMA busy_timeout=5000;     -- wait up to 5s instead of erroring
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;
```

- `sqlite3.connect(..., check_same_thread=False)` on the base class.
- Connection-per-thread via `threading.local()` — NOT a single shared Connection.
- Explicit `threading.Lock` for multi-statement writes.

## 6. Daily digest algorithm

Scheduled via `app.job_queue.run_daily()`:

```python
from zoneinfo import ZoneInfo
import datetime as dt

app.job_queue.run_daily(
    callback=deal_service.digest_daily,
    time=dt.time(hour=8, minute=0, tzinfo=ZoneInfo("Europe/Berlin")),
    name="daily_digest",
)
```

**NOT** `run_repeating(interval=86400, first=60)` — that drifts on every restart.

Algorithm (`DealService.digest_daily`):

```python
async def digest_daily(self, context):
    # 0. Resume any interrupted run
    self._recover_stale_runs(max_age_minutes=10)

    # 1. Collect opted-in users for today's frequency
    today = dt.date.today()
    users = self.db.get_opted_in_users(
        frequency="daily" if today.weekday() != 0 else ("daily", "weekly")
    )
    if not users:
        return

    # 2. Create run record
    run_id = str(uuid.uuid4())
    self.db.start_digest_run(run_id, [u.user_id for u in users])

    # 3. Bucket users by search shape (§6.1) and scrape per bucket
    buckets = self._bucket_users(users)
    listings_by_bucket = {}
    for bucket_key, bucket_users in buckets.items():
        broad = self._union_search_fields(bucket_users)  # see §6.2
        try:
            listings = await self._run_scrapers(broad, max_pages=3)
            listings_by_bucket[bucket_key] = listings
            self.db.save_listings(listings)
        except Exception as e:
            logger.exception("scrape_failed bucket=%s", bucket_key)
            # Continue with other buckets, don't kill the run

    # 4. Per-user dispatch (order-independent, each delivery is atomic)
    for user in users:
        bucket_key = self._user_to_bucket(user)
        listings = listings_by_bucket.get(bucket_key, [])
        try:
            await self._deliver_one(run_id, user, listings, context)
        except Exception as e:
            logger.exception("deliver_failed user=%s", user.user_id)
            self.db.mark_delivery_failed(run_id, user.user_id, str(e))

    self.db.complete_digest_run(run_id)

    # 5. Health ping
    healthchecks_ping("digest-daily")
```

`_deliver_one` filters listings in memory against user's criteria (richer than SQL),
dedups via bulk `get_seen_ids(user.user_id)` (one query, not per-listing), scores
with per-user financing, picks top 5, sends messages (with 0.05s sleep between),
marks `seen_deals` + `digest_deliveries` in one transaction.

### 6.1 Bucketing policy

Users partition into buckets so one "broad scrape" serves similar users:

- Partition first by `property_type` (apartment/house/multi_family) — scrapers
  build different URLs per type.
- Within a property_type, split on `budget_max` tertiles ONLY if
  `max(budget_max) / min(budget_min) > 5`. Otherwise single bucket.
- Do NOT split on districts (cheap to union).
- Cap: max 5 buckets total regardless of user count. If more needed, log
  warning to admin.

Defer full implementation: MVP uses one bucket per property_type (no budget
tertiles) until user distribution shows bimodality.

### 6.2 `_union_search_fields` — explicit semantics

Only these fields are unioned (used to build scraper URLs):
- `budget_min = min(user.budget_min for user in users)` (floor)
- `budget_max = max(user.budget_max for user in users)` (ceiling)
- `min_size_sqm = min(...)`
- `min_rooms = min(...)`
- `districts = ∪ user.districts; if any user has [] (meaning "all"), use []`
- `property_types = ∪` (but buckets should homogenize this already)

**NOT unioned** (per-user, applied at scoring time):
- `equity_pct, interest_rate_pct, loan_term_years` — financial params
- `risk_tolerance` — affects scoring weights, not search
- `min_gross_yield_pct, min_cashflow_monthly` — post-score filters

## 7. Message format

Each deal = one message (easier to react, forward, save). One short text
message, no photo (attachments require URL parsing we don't currently do):

```
🏠 Eimsbüttel · 2,5 Zi · 64 m²
💰 249 000 €  (3 891 €/m² · -37%)

🎯 Score 69/100 · Rendite 4,6%
💸 Cashflow -€384/Mon  (20% EK, 3,5% Zins, 25 Jahre)

Ruhige 2,5-Zimmer-Wohnung mit Westbalkon und Stellplatz
in Schnelsen.

🚩 Zu prüfen:
• Kein Hausgeld in Anzeige

🔗 https://www.immobilienscout24.de/expose/166110769
```

Between messages: `await asyncio.sleep(0.05)` to respect Telegram's 30/s rate limit.

## 8. Deployment

**Choice**: Hetzner CX22 (€4.51/mo, 2 vCPU / 4 GB, DE datacenter) + systemd + bare Python.

Rejected alternatives:
- Fly.io free tier: 256 MB RAM too tight for parallel scrapers with BS4 parsing; free tier deprecated anyway.
- Railway: US egress to DE scraping targets adds latency and IP-reputation risk with ImmoScout.
- Docker/container: no upside for a single-process bot on a single VPS.

systemd service:
```ini
[Unit]
Description=Hamburg Deals Telegram Bot
After=network.target

[Service]
Type=notify
WorkingDirectory=/opt/hamburg-deals
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/etc/hamburg-deals/env
ExecStart=/opt/hamburg-deals/venv/bin/python -m src.bot.telegram_bot
Restart=always
RestartSec=10s
WatchdogSec=300s
NotifyAccess=all

[Install]
WantedBy=multi-user.target
```

Secrets in `/etc/hamburg-deals/env` (root:root 600):
```
TELEGRAM_BOT_TOKEN=...
ADMIN_CHAT_IDS=12345,67890
HEALTHCHECKS_URL=https://hc-ping.com/<uuid>
```

Persistent data: `/var/lib/hamburg-deals/deals.db`.

## 9. Security & abuse prevention

- **No auth at bot level**: chat_id IS the identity. Anyone can DM.
- **Admin gate**: `ADMIN_CHAT_IDS` env list; admin commands check membership.
- **Rate limit per user**:
  - MVP: in-memory deque per chat_id (max 3 /search/hour).
  - Accepted loss-on-restart tradeoff for MVP. Upgrade to DB-backed counter when >50 users.
- **Global scraper semaphore**: `asyncio.Semaphore(1)` — serialize all scrape runs
  (user /search AND scheduled digest) so ImmoScout sees at most one concurrent
  session from our IP.
- **Blocklist**: `users.blocked=1` checked at every handler entry.
- **Audit log**: `data/bot_commands.jsonl` — chat_id, command, timestamp,
  args-truncated. Rotated via `logrotate`.
- **Cost guard**: if scraping attempts in last 24h > 300, pause digest and
  alert admin via admin chat.
- **ToS disclaimer**: shown in `/start` and `/help` — "Data is aggregated from
  public listings. This is a screening tool, not investment advice. Verify
  independently."

## 10. Observability

- **Structured logging**:
  - `DEBUG`: scraper URLs, SQL queries
  - `INFO`: startup, digest start/end summary, per-user delivery counts
  - `WARNING`: single-platform scraper failure, rate-limit hits
  - `ERROR`: unhandled handler exception (caught by global `error_handler`)
  - `CRITICAL`: DB migration failure, invalid token
- **Per-digest summary line** (grep/alert friendly):
  ```
  digest_complete run_id=abc users=12 scraped=47 notified=9 failed=0 duration=73.2s
  ```
- **healthchecks.io**: ping on successful digest. Alert after 25h silence.
- **Per-scraper health counter**: track 7-day baseline; if <50% of baseline,
  admin alert (don't poison digest with empty results).
- **Admin commands**: `/admin_stats`, `/admin_health` for on-demand inspection.
- **No web dashboard** until >100 users.

## 11. Testing strategy

- **Unit tests** via pytest (existing) + `pytest-asyncio` (new dep).
- **Scraper mocking**: inject `FakeScraper` via constructor — no network in CI.

```python
# tests/fake_scraper.py
class FakeScraper(BaseScraper):
    PLATFORM_NAME = "fake"
    def __init__(self, canned): self._listings = canned
    def search(self, criteria, max_pages=5): return self._listings
    def build_search_url(self, *a, **kw): return ""
    def parse_search_results(self, *a, **kw): return []
    def parse_listing_detail(self, *a, **kw): return None
```

- **Digest tests**: seed DB with users + listings, run digest, assert correct
  messages sent to correct chats with correct dedup behavior.
- **Migration tests**: load an "old schema" DB fixture, run `Database.__init__`,
  assert all new columns present + `from_dict` roundtrips.
- **Time-dependent tests**: `freezegun` (new dev dep) for schedule assertions.
- **Concurrency test**: `ThreadPoolExecutor(4)` hammering `save_listing` on a
  temp DB; assert no exceptions + final count correct.
- **Target coverage**: 70%+ on services + DB layer. Handlers can be lower.

## 12. Dependencies

Updates to `requirements.txt`:
```
python-telegram-bot[job-queue]>=21.0,<22   # was: python-telegram-bot>=21.0
```

Updates to dev deps (new `requirements-dev.txt`):
```
pytest-asyncio>=0.23
freezegun>=1.4
```

## 13. Rollout plan

```
Phase 1 (Day 1-4) — Foundation
  Dev: migrations + SQLite thread safety + users/identities tables
  Critique: verifies migration idempotency, concurrency tests pass

Phase 2 (Day 5-7) — Service layer
  Dev: DealService/UserService/AdminService extracted from bot
  Critique: verifies no business logic in handlers, tests cover services

Phase 3 (Day 8-12) — Digest pipeline
  Dev: digest_daily with bucketing, seen_deals, digest_runs resumability
  Critique: force restart mid-run, verify resume; stress test with 100 fake users

Phase 4 (Day 13-15) — Bot UX polish
  Dev: cold-start search on /criteria, /alerts persists to DB, /stats,
       error_handler, strings extraction
  Critique: full user flow walkthrough, edge cases (no results, all scrapers fail)

Phase 5 (Day 16-17) — Admin + observability
  Dev: /admin_* commands, healthchecks.io ping, structured digest log line
  Critique: simulate scraper outage, verify admin alert fires

Phase 6 (Day 18) — Deploy
  Hetzner CX22, systemd, tail logs, soft launch with 1-2 friends for 48h

Week 2-3 — Iterate on feedback from first 10 beta users
Week 4 — Decision: scale, pivot, or kill (§15)
```

Day estimates include Critique rounds; a critique round of "must fix X, Y, Z"
adds time within the phase.

## 14. Known risks & mitigations

| Risk | Severity | Mitigation |
|------|:---:|---|
| Bot crashes mid-digest, users get partial or no digest | HIGH | Resumable digest_runs (§5.4); systemd `Restart=always WatchdogSec=300s`; recover stale runs on startup |
| All scrapers fail (IP ban, site change), empty digest silently sent | HIGH | Per-scraper 7d baseline; if <50% scraped, send operator alert and skip digest |
| SQLite write contention under concurrent bot+digest | MED | WAL + connection-per-thread + busy_timeout=5s + `asyncio.Semaphore(1)` on scrapers |
| Migration breaks prod DB | MED | Numbered migrations + dry-run in CI against prod DB copy before deploy |
| User sets unrealistic criteria → always 0 results | MED | After 3 empty digests, bot suggests widening criteria |
| Telegram rate limit (30 msg/sec global) | LOW | Sleep 0.05s between messages; at <1000 users not an issue |
| User abuse / spam | LOW | Blocklist env var + per-user rate limit + admin ban command |
| Legal: scraping ToS violations | HIGH | ToS disclaimer prominent; plan migration to IS24 paid API + Immowelt free API within 60 days |

## 15. Success criteria (week-4 GO/NO-GO)

- ≥10 users with `alerts_enabled=1` who have received ≥1 digest
- ≥30% weekly active (received AND opened a digest in last 7 days)
- ≥3 users give unprompted "this is useful" feedback (tracked via `/feedback` or DM)
- 0 CRITICAL bugs unresolved >24h
- Scraping cost <€50/mo total

## 16. Review cycle history

### V1 → V2 changelog

**Architect review** (5 CRITICAL, 9 MAJOR):
- Fixed SQLite thread safety (WAL, per-thread connection, lock) — §5.6
- Replaced `run_repeating(first=60)` with `run_daily(time=...)` — §6
- Added resumable digest_runs (§5.4) for crash recovery
- Added per-scraper health counter + digest-skip-on-failure — §10
- Introduced service layer — §4
- Added `users(user_id)` + `telegram_identities` future-proofing — §5.1
- Committed to Hetzner CX22 + systemd (rejected fly.io/Railway/Docker) — §8
- Added admin commands + healthchecks.io + digest summary log — §10
- Expanded `union_of` to `_union_search_fields` with explicit semantics — §6.2
- Added bucketing policy — §6.1
- Added schema migration strategy (numbered files + PRAGMA user_version) — §5.5
- Replaced 6 open questions with 6 decisions

**Developer review** (10 concrete code issues):
- Fixed `UserCriteria.from_dict` to filter unknown keys — §5 fixture test
- Changed default `alerts_enabled` from 1 to 0 (don't auto-opt-in existing users) — §5.2
- Added `PRAGMA table_info` introspection for migrations — §5.5
- Replaced per-listing `is_seen` with bulk `get_seen_ids()` — §6
- Used in-memory filter for digest (not SQL) but bulk query for seen_ids — §6
- Fixed `_run_scrapers` blocking event loop (moved to `run_in_executor`) — §9
- Added FakeScraper injection for tests — §11
- Error handler `app.add_error_handler()` for uncaught exceptions — §10
- Pinned `python-telegram-bot[job-queue]` extra — §12
- Added migration tests, digest roundtrip tests — §11

---

## 17. Subagent workflow (how we build this)

Implementation proceeds via two subagents iterating per phase (§13):

### 17.1 Developer subagent

**Role**: Implements each phase from the design.

**Instructions template**:
```
You are the DEVELOPER subagent for the Hamburg Deals Telegram bot project.

GROUND RULES:
- Read docs/FLAVOR_B_DESIGN.md before writing any code.
- Implement ONLY the phase assigned to you (e.g., "Phase 1: Foundation").
- Preserve all existing tests passing (pytest tests/ -q).
- Follow the architecture strictly: handlers are thin, Services own logic,
  DB owns persistence.
- Write new tests for every new module. Target 70% coverage on services + DB.
- Use type hints, docstrings with constraints, no silent except blocks.

OUTPUT:
- A concise summary of files changed + new tests added.
- The output of `pytest tests/ -q` showing all green.
- A list of "design assumptions verified" and "design issues found" (things
  in the design that didn't work in practice, for Critique to consider).

DO NOT:
- Exceed scope (e.g., do not implement Phase 3 while in Phase 1).
- Skip tests to "save time".
- Add dependencies beyond what §12 specifies.
- Refactor files outside the phase scope unless required.
```

### 17.2 Critique subagent

**Role**: Reviews Developer's output, finds issues, blocks the phase until fixed.

**Instructions template**:
```
You are the CRITIQUE subagent. Your job is to find problems, not write code.

GROUND RULES:
- Read docs/FLAVOR_B_DESIGN.md + Developer's summary + the actual diff
  (git diff against main).
- Evaluate: (a) does the implementation match the design? (b) are there
  bugs, race conditions, security issues, or edge cases missed?
  (c) is test coverage adequate? (d) any code smells or over-engineering?

RETURN VERDICT (one of):
  APPROVED:              Phase is done. Move to next.
  APPROVED WITH NITS:    Minor issues to fix but not blockers. List them.
  CHANGES REQUESTED:     Must fix before merging. List each with severity
                         (CRITICAL/MAJOR/MINOR) and specific file:line.

BE HARSH. Look for:
- Missing tests for happy path AND error path.
- `except Exception` without logging.
- Hardcoded values that belong in config.
- Race conditions in concurrent code.
- Missing input validation on user-facing commands.
- Changes to shared modules that affect other subsystems.
- Design drift: the implementation does something different from what the
  design says (either fix the code or update the design).

DO NOT:
- Propose whole rewrites — aim for smallest fix.
- Approve just to unblock the developer.
- Chase stylistic nits over correctness.
```

### 17.3 Iteration protocol

1. Developer implements Phase N, returns summary.
2. Critique reviews, returns verdict.
3. If `CHANGES REQUESTED`: Developer addresses, back to step 2 (max 3 rounds).
4. If `APPROVED` or `APPROVED WITH NITS`: commit, move to Phase N+1.
5. After 3 Critique rounds without approval: escalate to user (me).

A phase ending means:
- All Critique-flagged issues resolved.
- All tests green.
- Design doc updated if any "design assumptions verified" in Developer's
  output contradicted the doc.

### 17.4 When to update this design doc

The design doc is **living**. Update in §16 changelog whenever:
- A decision here proves wrong in practice (Developer reports it).
- Critique flags something the design didn't consider.
- User requirements change.

Every change gets a bullet in the changelog with date + brief reason.
