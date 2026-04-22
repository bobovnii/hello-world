"""SQLite database operations for listings, criteria, and analysis results.

Thread-safety (FLAVOR_B_DESIGN §5.6):
- One Connection per thread, stored in ``threading.local()``.
- ``check_same_thread=False`` on the base class so stray cross-thread reads
  do not raise (they still use their own thread's Connection via ``_conn``).
- An explicit ``threading.Lock`` serialises multi-statement writes so
  interleaved threads never corrupt the DB.

Connection PRAGMAs applied on every new connection:
- ``journal_mode=WAL``      - many readers + one writer concurrently.
- ``busy_timeout=5000``     - wait up to 5s on lock contention.
- ``foreign_keys=ON``       - enforce FK constraints (off by default in SQLite).
- ``synchronous=NORMAL``    - WAL-safe durability tradeoff.

Migrations (FLAVOR_B_DESIGN §5.5):
- Numbered ``NNN_*.sql`` files under ``src/database/migrations/``.
  ``000_baseline.sql`` holds the pre-Flavor-B schema (listings,
  analysis_results, user_criteria). There is no separate ``_create_tables()``:
  ALL schema lives in migrations.
- ``PRAGMA user_version`` tracks the highest applied migration number.
  A fresh DB starts at version 0 and 000 is (re-)applied; 000 uses
  ``CREATE TABLE IF NOT EXISTS`` so re-application is a no-op.
- ``_migrate()`` runs on every ``Database()`` init.
- Transactional correctness: ``sqlite3.executescript()`` implicitly COMMITs
  pending work and does NOT roll back on failure, so we parse the .sql file
  into individual statements and execute them inside an explicit
  ``BEGIN``/``COMMIT``/``ROLLBACK`` frame via a dedicated migration connection
  (``isolation_level=None`` / autocommit mode so our BEGIN actually begins).
  Any statement failure rolls back ALL preceding statements in the same file
  and leaves ``PRAGMA user_version`` untouched; the exception propagates and
  halts startup (no silent half-migrations).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from pathlib import Path

from .models import Listing, UserCriteria, AnalysisResult

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_NAME_RE = re.compile(r"^(\d{3,})_.+\.sql$")


def _split_sql_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements.

    Handles:
    - ``--`` line comments (stripped until end of line)
    - Single-quoted string literals with ``''`` escape
    - Double-quoted identifiers with ``""`` escape
    - Semicolons as statement terminators outside of strings

    Does NOT handle nested ``BEGIN ... END`` blocks (triggers/procedures).
    Our migrations are flat DDL + simple DML, so this is sufficient. If we
    ever need triggers, extend this function and add a test.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    in_single = False
    in_double = False
    while i < n:
        c = sql[i]
        # Line comment: only outside of strings.
        if not in_single and not in_double and c == "-" and i + 1 < n and sql[i + 1] == "-":
            # Skip to end of line (or EOF).
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if c == "'" and not in_double:
            buf.append(c)
            # Handle '' escape: a doubled quote inside a single-quoted string
            # does NOT end the string.
            if in_single and i + 1 < n and sql[i + 1] == "'":
                buf.append(sql[i + 1])
                i += 2
                continue
            in_single = not in_single
            i += 1
            continue
        if c == '"' and not in_single:
            buf.append(c)
            if in_double and i + 1 < n and sql[i + 1] == '"':
                buf.append(sql[i + 1])
                i += 2
                continue
            in_double = not in_double
            i += 1
            continue
        if c == ";" and not in_single and not in_double:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


class Database:
    """SQLite-backed persistence layer.

    Public methods are thread-safe. A single ``Database`` instance may be
    shared across threads (e.g. by the bot process and the JobQueue).
    """

    def __init__(self, db_path: str = "data/deals.db") -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # Per-thread connection holder. ``threading.local`` gives each
        # thread its own ``.conn`` attribute without us managing a dict.
        self._local = threading.local()
        # Serialises multi-statement writes across threads. SQLite's own
        # file lock + busy_timeout handles single-statement contention;
        # this lock guards our read-then-write sequences (e.g.
        # ``save_listing``'s SELECT-then-INSERT-or-UPDATE).
        self._write_lock = threading.Lock()
        # Migrations own all schema creation. There is no separate
        # _create_tables() anymore — baseline lives in 000_baseline.sql.
        self._migrate()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        """Return this thread's Connection, creating it on first use."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # Apply PRAGMAs on EVERY new connection (SQLite settings are
            # per-connection, not per-file, except WAL which sticks to the
            # file once set).
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    # Back-compat shim: some internal helpers historically accessed
    # ``self.conn`` directly. Keep a property that returns the current
    # thread's connection so any stragglers continue to work.
    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn()

    # ------------------------------------------------------------------
    # Migrations
    # ------------------------------------------------------------------
    @staticmethod
    def _discover_migrations(
        migrations_dir: Path | None = None,
    ) -> list[tuple[int, Path]]:
        """Return ``(version, path)`` pairs sorted ascending by version.

        Only files matching ``NNN_*.sql`` are included. Raises ``ValueError``
        on duplicate version numbers - the design says failures halt startup.

        When ``migrations_dir`` is None, the module-level ``_MIGRATIONS_DIR``
        is read at call time (so tests can monkeypatch it).
        """
        if migrations_dir is None:
            migrations_dir = _MIGRATIONS_DIR
        if not migrations_dir.exists():
            return []
        found: dict[int, Path] = {}
        for path in sorted(migrations_dir.iterdir()):
            if not path.is_file():
                continue
            m = _MIGRATION_NAME_RE.match(path.name)
            if not m:
                continue
            version = int(m.group(1))
            if version in found:
                raise ValueError(
                    f"Duplicate migration version {version}: "
                    f"{found[version].name} vs {path.name}"
                )
            found[version] = path
        return sorted(found.items())

    def _migrate(self) -> None:
        """Apply all pending migrations in order.

        Uses a dedicated migration connection in autocommit mode
        (``isolation_level=None``) so our explicit ``BEGIN``/``COMMIT``/
        ``ROLLBACK`` frames around each file are the only transactions.
        This gives real transactional rollback — unlike
        ``sqlite3.executescript()`` which implicitly COMMITs pending work
        and does NOT roll back on failure.

        Version 0 (``000_baseline.sql``) is idempotent (``CREATE TABLE IF
        NOT EXISTS``) and is applied only when ``user_version`` is still 0.
        For N > 0, migration N is applied iff ``user_version < N``. On
        success, ``PRAGMA user_version`` is set to N inside the SAME
        transaction as the migration's statements.
        """
        migrations = self._discover_migrations()
        if not migrations:
            return

        # Dedicated migration connection. Autocommit so we fully own
        # transaction boundaries. check_same_thread=False is safe because
        # only this method touches this connection.
        mig_conn = sqlite3.connect(
            self.db_path, isolation_level=None, check_same_thread=False
        )
        try:
            # Foreign keys must be off during structural changes on legacy
            # tables per SQLite docs, but we're using IF NOT EXISTS on the
            # baseline and additive CREATE TABLE on later migrations, so we
            # can leave FKs on. busy_timeout helps if another process holds
            # a lock during startup.
            mig_conn.execute("PRAGMA busy_timeout=5000")
            mig_conn.execute("PRAGMA foreign_keys=ON")
            # Promote the DB to WAL here (before any worker threads try to
            # do it concurrently via _conn()). Journal mode sticks to the
            # file, so per-thread PRAGMA journal_mode=WAL becomes a cheap
            # no-op afterwards. Without this, a burst of first-time thread
            # connections can collide on the mode-change lock.
            mig_conn.execute("PRAGMA journal_mode=WAL")

            current = mig_conn.execute("PRAGMA user_version").fetchone()[0]

            for version, path in migrations:
                # Skip rule: for version N>0, already-applied means
                # current >= N. For the idempotent baseline (version 0),
                # "already applied" means current > 0 (any later migration
                # has run, so 000's CREATE TABLE IF NOT EXISTS was not
                # needed or was already handled on a prior startup).
                if version == 0:
                    if current > 0:
                        continue
                else:
                    if version <= current:
                        continue

                sql = path.read_text(encoding="utf-8")
                statements = _split_sql_statements(sql)
                if not statements:
                    # Empty / comment-only migration: still bump the
                    # version if > 0, but nothing to execute.
                    logger.warning(
                        "Migration %03d (%s) has no executable statements",
                        version,
                        path.name,
                    )

                logger.info(
                    "Applying migration %03d (%s): %d statement(s)",
                    version,
                    path.name,
                    len(statements),
                )
                mig_conn.execute("BEGIN")
                try:
                    for stmt in statements:
                        mig_conn.execute(stmt)
                    # Bump user_version inside the same transaction so a
                    # rollback here leaves the tracker untouched. PRAGMA
                    # does not accept bound params; version is a safe int
                    # discovered from a filename regex.
                    if version > 0:
                        mig_conn.execute(f"PRAGMA user_version = {version}")
                    mig_conn.execute("COMMIT")
                except sqlite3.Error as e:
                    try:
                        mig_conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        # ROLLBACK itself should not normally fail; log and
                        # re-raise the original migration error.
                        logger.exception(
                            "ROLLBACK failed for migration %03d", version
                        )
                    logger.exception(
                        "Migration %03d failed (%s); halting startup",
                        version,
                        path.name,
                    )
                    raise RuntimeError(
                        f"Migration {version} ({path.name}) failed: {e}"
                    ) from e

                # Refresh current so subsequent iterations compare correctly.
                if version > 0:
                    current = version
        finally:
            mig_conn.close()

    # ------------------------------------------------------------------
    # Listings
    # ------------------------------------------------------------------
    def save_listing(self, listing: Listing) -> bool:
        """Save or update a listing. Returns True if new, False if updated."""
        d = listing.to_dict()
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        updates = ", ".join(f"{k} = ?" for k in d.keys() if k != "id")

        conn = self._conn()
        # Multi-statement write: SELECT-then-(INSERT|UPDATE). Lock so a
        # concurrent saver cannot slip in between the two statements and
        # cause a duplicate-PK error or lost update.
        with self._write_lock:
            existing = conn.execute(
                "SELECT id FROM listings WHERE id = ?", (listing.id,)
            ).fetchone()
            if existing:
                vals = [v for k, v in d.items() if k != "id"] + [listing.id]
                conn.execute(
                    f"UPDATE listings SET {updates} WHERE id = ?", vals
                )
            else:
                conn.execute(
                    f"INSERT INTO listings ({cols}) VALUES ({placeholders})",
                    list(d.values()),
                )
            conn.commit()
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

        rows = self._conn().execute(query, params).fetchall()
        return [Listing.from_dict(dict(row)) for row in rows]

    # ------------------------------------------------------------------
    # Analysis results
    # ------------------------------------------------------------------
    def save_analysis(self, result: AnalysisResult) -> None:
        d = result.to_dict()
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        conn = self._conn()
        with self._write_lock:
            conn.execute(
                f"INSERT OR REPLACE INTO analysis_results ({cols}) VALUES ({placeholders})",
                list(d.values()),
            )
            conn.commit()

    def get_top_deals(self, limit: int = 10) -> list[tuple[Listing, AnalysisResult]]:
        """Get top deals joined with analysis, sorted by score."""
        rows = self._conn().execute(
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

    # ------------------------------------------------------------------
    # User criteria (legacy chat_id-keyed; will move to user_id in a later phase)
    # ------------------------------------------------------------------
    def save_user_criteria(self, criteria: UserCriteria) -> None:
        d = criteria.to_dict()
        cols = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        conn = self._conn()
        with self._write_lock:
            conn.execute(
                f"INSERT OR REPLACE INTO user_criteria ({cols}) VALUES ({placeholders})",
                list(d.values()),
            )
            conn.commit()

    def get_user_criteria(self, chat_id: int) -> UserCriteria | None:
        row = self._conn().execute(
            "SELECT * FROM user_criteria WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if row:
            return UserCriteria.from_dict(dict(row))
        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def close(self) -> None:
        """Close this thread's connection if any.

        Caveat: ``threading.local`` exposes only the caller's slot. This
        method therefore closes ONLY the connection for the thread that
        calls it. Connections belonging to other threads remain open until
        their threads terminate (at which point Python finalises them).
        For deterministic shutdown in a multi-threaded context, call
        ``close()`` from every thread that touched the DB, or rely on
        process exit to free the remaining handles.

        A future iteration may track every per-thread connection in a
        ``WeakSet`` so that a single ``close()`` can tear them all down;
        deferred until we have a concrete shutdown path that needs it.
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
