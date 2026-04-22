"""Tests for database operations."""

import os
import sqlite3
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.database.models import Listing, UserCriteria, AnalysisResult
from src.database.db import Database, _MIGRATIONS_DIR, _split_sql_statements


@pytest.fixture
def db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = Database(db_path=path)
    yield database
    database.close()
    os.unlink(path)


@pytest.fixture
def sample_listing():
    return Listing(
        id="test_1",
        platform="immoscout",
        url="https://example.com/1",
        title="Test Wohnung",
        price=200000,
        size_sqm=60,
        rooms=3,
        district="Harburg",
    )


class TestDatabase:
    def test_save_and_get_listing(self, db, sample_listing):
        is_new = db.save_listing(sample_listing)
        assert is_new

        listings = db.get_listings()
        assert len(listings) == 1
        assert listings[0].id == "test_1"
        assert listings[0].price == 200000

    def test_update_existing(self, db, sample_listing):
        db.save_listing(sample_listing)
        sample_listing.price = 190000
        is_new = db.save_listing(sample_listing)
        assert not is_new

        listings = db.get_listings()
        assert len(listings) == 1
        assert listings[0].price == 190000

    def test_bulk_save(self, db):
        listings = [
            Listing(id=f"test_{i}", platform="test", url=f"url_{i}",
                    title=f"Listing {i}", price=100000 + i * 50000,
                    size_sqm=50 + i * 10, rooms=2 + i)
            for i in range(5)
        ]
        new, updated = db.save_listings(listings)
        assert new == 5
        assert updated == 0

    def test_filter_by_criteria(self, db):
        listings = [
            Listing(id="cheap", platform="test", url="u1", title="Cheap",
                    price=100000, size_sqm=40, rooms=2, district="Harburg"),
            Listing(id="mid", platform="test", url="u2", title="Mid",
                    price=250000, size_sqm=70, rooms=3, district="Wandsbek"),
            Listing(id="expensive", platform="test", url="u3", title="Expensive",
                    price=500000, size_sqm=100, rooms=4, district="Eimsbüttel"),
        ]
        db.save_listings(listings)

        criteria = UserCriteria(budget_min=100000, budget_max=300000)
        results = db.get_listings(criteria)
        assert len(results) == 2
        assert all(l.price <= 300000 for l in results)

    def test_save_and_get_analysis(self, db, sample_listing):
        db.save_listing(sample_listing)

        analysis = AnalysisResult(
            listing_id="test_1",
            deal_score=75.5,
            gross_rental_yield_pct=5.2,
            monthly_cashflow=150,
            undervalue_reasons=["Below market", "Rising district"],
        )
        db.save_analysis(analysis)

        top = db.get_top_deals(limit=10)
        assert len(top) == 1
        listing, result = top[0]
        assert result.deal_score == 75.5
        assert len(result.undervalue_reasons) == 2

    def test_user_criteria_crud(self, db):
        criteria = UserCriteria(
            chat_id=12345,
            budget_min=100000,
            budget_max=400000,
            districts=["Harburg", "Wandsbek"],
        )
        db.save_user_criteria(criteria)

        loaded = db.get_user_criteria(12345)
        assert loaded is not None
        assert loaded.budget_max == 400000
        assert "Harburg" in loaded.districts

    def test_nonexistent_user(self, db):
        assert db.get_user_criteria(99999) is None


class TestModels:
    def test_listing_to_from_dict(self):
        listing = Listing(
            id="t1", platform="test", url="url", title="Title",
            price=200000, size_sqm=60, rooms=3,
            image_urls=["img1.jpg", "img2.jpg"],
            balcony=True,
        )
        d = listing.to_dict()
        restored = Listing.from_dict(d)
        assert restored.id == listing.id
        assert restored.image_urls == ["img1.jpg", "img2.jpg"]
        assert restored.balcony is True

    def test_listing_price_per_sqm(self):
        listing = Listing(
            id="t1", platform="test", url="", title="",
            price=200000, size_sqm=80, rooms=3,
        )
        assert listing.price_per_sqm == 2500.0

    def test_criteria_to_from_dict(self):
        criteria = UserCriteria(
            districts=["Altona", "Harburg"],
            property_types=["apartment", "house"],
            chat_id=123,
        )
        d = criteria.to_dict()
        restored = UserCriteria.from_dict(d)
        assert restored.districts == ["Altona", "Harburg"]
        assert restored.property_types == ["apartment", "house"]


# ---------------------------------------------------------------------------
# Migration / schema tests (FLAVOR_B_DESIGN §5.5)
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path():
    """Bare temp path (no Database yet) so the test can drive __init__."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    # Remove DB + any WAL sidecars.
    for suffix in ("", "-wal", "-shm"):
        p = path + suffix
        if os.path.exists(p):
            os.unlink(p)


class TestMigrations:
    """Migrations run on every startup, are idempotent, and are tracked."""

    def test_fresh_db_applies_all_migrations(self, db_path):
        db = Database(db_path=db_path)
        try:
            version = db.conn.execute("PRAGMA user_version").fetchone()[0]
            # At least migration 001 exists; PRAGMA user_version must reflect it.
            expected = max(v for v, _ in Database._discover_migrations())
            assert version == expected
            # Tables from 001 must exist.
            tables = {
                r[0]
                for r in db.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "users" in tables
            assert "telegram_identities" in tables
        finally:
            db.close()

    def test_migrations_are_idempotent(self, db_path):
        """Opening the DB twice must not re-run or corrupt migrations."""
        db = Database(db_path=db_path)
        v1 = db.conn.execute("PRAGMA user_version").fetchone()[0]
        # Seed a row — proves the second init doesn't drop/recreate.
        uid = str(uuid.uuid4())
        db.conn.execute(
            "INSERT INTO users (user_id, created_at) VALUES (?, ?)",
            (uid, datetime.now(timezone.utc).isoformat()),
        )
        db.conn.commit()
        db.close()

        db2 = Database(db_path=db_path)
        try:
            v2 = db2.conn.execute("PRAGMA user_version").fetchone()[0]
            assert v2 == v1, "user_version moved on re-open"
            rows = db2.conn.execute(
                "SELECT user_id FROM users"
            ).fetchall()
            assert [r[0] for r in rows] == [uid], "data lost on re-init"
        finally:
            db2.close()

    def test_legacy_db_gets_migrated(self, db_path):
        """A pre-existing DB with user_version=0 must get migration 001 applied."""
        # Simulate a DB from before migrations existed: a user_criteria table
        # only, user_version left at 0.
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE user_criteria (
                chat_id INTEGER PRIMARY KEY,
                budget_min REAL DEFAULT 0
            );
        """)
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        conn.close()

        db = Database(db_path=db_path)
        try:
            tables = {
                r[0]
                for r in db.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "users" in tables
            assert "telegram_identities" in tables
            # Existing user_criteria table must not be clobbered.
            assert "user_criteria" in tables
            version = db.conn.execute("PRAGMA user_version").fetchone()[0]
            assert version >= 1
        finally:
            db.close()

    def test_discover_migrations_orders_numerically(self, tmp_path):
        """Discovery must sort by numeric version, not lexicographically."""
        (tmp_path / "010_later.sql").write_text("-- noop\n")
        (tmp_path / "002_mid.sql").write_text("-- noop\n")
        (tmp_path / "001_first.sql").write_text("-- noop\n")
        # Non-matching files are ignored.
        (tmp_path / "README.md").write_text("hi")
        (tmp_path / "draft.sql").write_text("-- noop\n")

        found = Database._discover_migrations(tmp_path)
        assert [v for v, _ in found] == [1, 2, 10]

    def test_duplicate_migration_versions_raise(self, tmp_path):
        (tmp_path / "001_a.sql").write_text("-- noop\n")
        (tmp_path / "001_b.sql").write_text("-- noop\n")
        with pytest.raises(ValueError, match="Duplicate migration"):
            Database._discover_migrations(tmp_path)

    def test_broken_migration_raises_and_rolls_back(
        self, db_path, monkeypatch, tmp_path
    ):
        """A migration that fails mid-script must not bump user_version
        AND must NOT leave partial statements persisted.

        Proves CRITICAL #2 from the Phase 1 critique: the previous
        implementation used ``sqlite3.executescript()`` which implicitly
        commits pending work and leaves the first statement's side effect
        in place when the second statement fails. The new runner wraps
        each migration in an explicit BEGIN/COMMIT/ROLLBACK so we get real
        transactional guarantees.
        """
        fake_dir = tmp_path / "mig"
        fake_dir.mkdir()
        (fake_dir / "001_bad.sql").write_text(
            "CREATE TABLE ok (id INTEGER);\n"
            "SELECT broken_syntax ( ;\n"  # guaranteed parse error
        )
        monkeypatch.setattr("src.database.db._MIGRATIONS_DIR", fake_dir)

        with pytest.raises(RuntimeError, match="Migration 1"):
            Database(db_path=db_path)

        # Re-open raw to inspect state left behind by the failed migration.
        conn = sqlite3.connect(db_path)
        try:
            # user_version is still 0 (migration's COMMIT never ran).
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
            # AND the first statement's CREATE TABLE was rolled back.
            # This is the assertion that distinguishes a real transaction
            # from the old executescript()-based "transaction".
            result = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='ok'"
            ).fetchone()
            assert result is None, (
                "failed migration must not leave tables behind; "
                "found 'ok' table on disk after ROLLBACK"
            )
        finally:
            conn.close()

    def test_connection_pragmas_applied(self, db_path):
        db = Database(db_path=db_path)
        try:
            c = db.conn
            assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
            # synchronous NORMAL == 1
            assert c.execute("PRAGMA synchronous").fetchone()[0] == 1
        finally:
            db.close()

    def test_migration_001_file_present(self):
        """The design requires 001_users_and_identities.sql to exist on disk."""
        path = _MIGRATIONS_DIR / "001_users_and_identities.sql"
        assert path.exists(), f"{path} missing"
        sql = path.read_text()
        assert "CREATE TABLE" in sql
        assert "users" in sql
        assert "telegram_identities" in sql

    def test_migration_000_baseline_present_and_contains_baseline_tables(self):
        """FLAVOR_B_DESIGN §5.5: baseline lives in 000_baseline.sql.

        Proves CRITICAL #1 from the Phase 1 critique: the old
        ``_create_tables()`` helper has been retired and replaced by a
        numbered migration file. ALL schema — including baseline — is now
        reached via ``_migrate()``.
        """
        path = _MIGRATIONS_DIR / "000_baseline.sql"
        assert path.exists(), f"{path} missing"
        sql = path.read_text()
        # All three baseline tables from the pre-Flavor-B era must be here.
        assert "CREATE TABLE IF NOT EXISTS listings" in sql
        assert "CREATE TABLE IF NOT EXISTS analysis_results" in sql
        assert "CREATE TABLE IF NOT EXISTS user_criteria" in sql
        # The three indexes that used to be in _create_tables().
        assert "idx_listings_district" in sql
        assert "idx_listings_price" in sql
        assert "idx_analysis_score" in sql

    def test_create_tables_helper_is_retired(self):
        """Once baseline moved into 000_baseline.sql, _create_tables() goes.

        Directly checks that the attribute no longer exists on Database so
        nothing is quietly calling a stale helper.
        """
        assert not hasattr(Database, "_create_tables"), (
            "Database._create_tables() must be retired; baseline schema now "
            "lives in migrations/000_baseline.sql (FLAVOR_B_DESIGN §5.5)"
        )

    def test_fresh_db_baseline_tables_created_by_migration(self, db_path):
        """On a fresh DB, 000_baseline.sql must create listings/analysis/criteria.

        This is the end-to-end proof that retiring _create_tables() did
        not lose the baseline schema: migrations alone produce a usable DB.
        """
        db = Database(db_path=db_path)
        try:
            tables = {
                r[0]
                for r in db.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert {"listings", "analysis_results", "user_criteria"} <= tables
            # Indexes are registered separately.
            indexes = {
                r[0]
                for r in db.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            assert "idx_listings_district" in indexes
            assert "idx_listings_price" in indexes
            assert "idx_analysis_score" in indexes
        finally:
            db.close()

    def test_legacy_pre_flavor_b_db_migrates_to_v1(self, db_path):
        """A DB previously initialized by the old _create_tables() at
        ``user_version=0`` must migrate cleanly to ``user_version=1`` after
        running _migrate() with 000 and 001 present.

        Proves CRITICAL #1's acceptance test from the Phase 1 critique:
        legacy DBs retain their existing tables (CREATE TABLE IF NOT EXISTS
        is a no-op) while the new 001 migration adds users + identities.
        """
        # Simulate a DB from the pre-Flavor-B era by applying a shape
        # close to the old _create_tables() body directly. Leaves
        # user_version=0 (the default on every fresh sqlite file).
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE listings (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                price REAL NOT NULL,
                size_sqm REAL NOT NULL,
                rooms REAL NOT NULL,
                district TEXT DEFAULT '',
                scraped_at TEXT NOT NULL
            );
            CREATE TABLE analysis_results (
                listing_id TEXT PRIMARY KEY,
                deal_score REAL,
                FOREIGN KEY (listing_id) REFERENCES listings(id)
            );
            CREATE TABLE user_criteria (
                chat_id INTEGER PRIMARY KEY,
                budget_min REAL DEFAULT 0
            );
            CREATE INDEX idx_listings_district ON listings(district);
        """)
        # Seed a row so we can prove data survives.
        conn.execute(
            "INSERT INTO user_criteria (chat_id, budget_min) VALUES (?, ?)",
            (777, 150000),
        )
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        conn.close()

        db = Database(db_path=db_path)
        try:
            # user_version advanced to the highest migration number (1 for now).
            v = db.conn.execute("PRAGMA user_version").fetchone()[0]
            expected = max(v for v, _ in Database._discover_migrations())
            assert v == expected == 1

            tables = {
                r[0]
                for r in db.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            # Baseline tables preserved (CREATE TABLE IF NOT EXISTS no-op).
            assert {"listings", "analysis_results", "user_criteria"} <= tables
            # New 001 tables added.
            assert "users" in tables
            assert "telegram_identities" in tables
            # Pre-existing data not clobbered.
            row = db.conn.execute(
                "SELECT budget_min FROM user_criteria WHERE chat_id = ?", (777,)
            ).fetchone()
            assert row is not None
            assert row[0] == 150000
        finally:
            db.close()


class TestSqlStatementSplitter:
    """_split_sql_statements underpins the transactional migration runner."""

    def test_splits_simple_statements(self):
        sql = "CREATE TABLE a (id INT); CREATE TABLE b (id INT);"
        stmts = _split_sql_statements(sql)
        assert stmts == ["CREATE TABLE a (id INT)", "CREATE TABLE b (id INT)"]

    def test_ignores_trailing_semicolon_only_whitespace(self):
        stmts = _split_sql_statements("SELECT 1;   ;  \n;")
        assert stmts == ["SELECT 1"]

    def test_strips_line_comments(self):
        sql = "-- leading comment\nCREATE TABLE a (id INT);\n-- trailing\n"
        stmts = _split_sql_statements(sql)
        assert stmts == ["CREATE TABLE a (id INT)"]

    def test_preserves_semicolons_inside_single_quoted_strings(self):
        sql = "INSERT INTO t VALUES ('a;b;c'); INSERT INTO t VALUES ('x');"
        stmts = _split_sql_statements(sql)
        assert stmts == [
            "INSERT INTO t VALUES ('a;b;c')",
            "INSERT INTO t VALUES ('x')",
        ]

    def test_handles_doubled_single_quote_escape(self):
        sql = "INSERT INTO t VALUES ('it''s ok;');"
        stmts = _split_sql_statements(sql)
        assert stmts == ["INSERT INTO t VALUES ('it''s ok;')"]

    def test_preserves_semicolons_inside_double_quoted_identifiers(self):
        sql = 'CREATE TABLE "weird;name" (id INT); SELECT 1;'
        stmts = _split_sql_statements(sql)
        assert stmts == ['CREATE TABLE "weird;name" (id INT)', "SELECT 1"]

    def test_empty_input_yields_empty_list(self):
        assert _split_sql_statements("") == []
        assert _split_sql_statements("-- only comment\n") == []
        assert _split_sql_statements("   \n\n  ") == []


# ---------------------------------------------------------------------------
# Identity table FK + linkage tests (FLAVOR_B_DESIGN §5.1)
# ---------------------------------------------------------------------------


class TestIdentityTables:
    """users + telegram_identities enforce the expected relations."""

    def _make_user(self, db: Database) -> str:
        user_id = str(uuid.uuid4())
        db.conn.execute(
            "INSERT INTO users (user_id, created_at) VALUES (?, ?)",
            (user_id, datetime.now(timezone.utc).isoformat()),
        )
        db.conn.commit()
        return user_id

    def test_user_id_is_primary_key_unique(self, db):
        uid = self._make_user(db)
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(
                "INSERT INTO users (user_id, created_at) VALUES (?, ?)",
                (uid, datetime.now(timezone.utc).isoformat()),
            )

    def test_telegram_identity_requires_existing_user(self, db):
        """FK enforcement must reject a chat_id pointing at no user."""
        ghost_user = str(uuid.uuid4())
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(
                "INSERT INTO telegram_identities (chat_id, user_id) VALUES (?, ?)",
                (12345, ghost_user),
            )
            db.conn.commit()

    def test_telegram_identity_happy_path(self, db):
        uid = self._make_user(db)
        db.conn.execute(
            "INSERT INTO telegram_identities (chat_id, user_id, telegram_username) "
            "VALUES (?, ?, ?)",
            (99, uid, "alice"),
        )
        db.conn.commit()
        row = db.conn.execute(
            "SELECT user_id, telegram_username FROM telegram_identities WHERE chat_id = ?",
            (99,),
        ).fetchone()
        assert row["user_id"] == uid
        assert row["telegram_username"] == "alice"

    def test_chat_id_is_primary_key_unique(self, db):
        uid = self._make_user(db)
        db.conn.execute(
            "INSERT INTO telegram_identities (chat_id, user_id) VALUES (?, ?)",
            (42, uid),
        )
        db.conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(
                "INSERT INTO telegram_identities (chat_id, user_id) VALUES (?, ?)",
                (42, uid),
            )

    def test_delete_user_cascades_to_identities(self, db):
        uid = self._make_user(db)
        db.conn.execute(
            "INSERT INTO telegram_identities (chat_id, user_id) VALUES (?, ?)",
            (7, uid),
        )
        db.conn.commit()
        db.conn.execute("DELETE FROM users WHERE user_id = ?", (uid,))
        db.conn.commit()
        remaining = db.conn.execute(
            "SELECT COUNT(*) FROM telegram_identities WHERE user_id = ?",
            (uid,),
        ).fetchone()[0]
        assert remaining == 0


# ---------------------------------------------------------------------------
# Thread-safety tests (FLAVOR_B_DESIGN §5.6)
# ---------------------------------------------------------------------------


class TestThreadSafety:
    """Database must survive concurrent access from multiple threads."""

    def test_per_thread_connection_objects(self, db):
        """Each thread must see its own sqlite3.Connection instance."""
        seen: dict[int, int] = {}
        barrier = threading.Barrier(4)

        def worker() -> int:
            barrier.wait()
            return id(db.conn)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker) for _ in range(4)]
            ids = [f.result() for f in as_completed(futures)]
        # Plus the main thread's connection.
        ids.append(id(db.conn))
        # All four worker connections should be distinct objects AND distinct
        # from the main thread's. (threading.local returns per-thread attrs.)
        assert len(set(ids)) == 5

    def test_concurrent_save_listing(self, db_path):
        """Hammer save_listing from 8 threads; assert no errors + final count."""
        db = Database(db_path=db_path)
        try:
            n_per_thread = 25
            threads = 8

            def worker(tid: int) -> None:
                for i in range(n_per_thread):
                    listing = Listing(
                        id=f"t{tid}_i{i}",
                        platform="test",
                        url=f"u/{tid}/{i}",
                        title="x",
                        price=100_000 + i,
                        size_sqm=50,
                        rooms=2,
                    )
                    db.save_listing(listing)

            with ThreadPoolExecutor(max_workers=threads) as pool:
                futures = [pool.submit(worker, t) for t in range(threads)]
                # .result() re-raises any exception from the thread.
                for f in as_completed(futures):
                    f.result()

            count = db.conn.execute(
                "SELECT COUNT(*) FROM listings"
            ).fetchone()[0]
            assert count == threads * n_per_thread
        finally:
            db.close()

    def test_concurrent_identity_inserts(self, db_path):
        """Parallel user + identity inserts must all land without errors."""
        db = Database(db_path=db_path)
        try:
            threads = 6
            per_thread = 10
            lock = threading.Lock()
            created_ids: list[str] = []

            def worker(tid: int) -> None:
                conn = db.conn  # per-thread connection
                for i in range(per_thread):
                    uid = str(uuid.uuid4())
                    chat_id = tid * 10_000 + i
                    with lock:
                        # Two-statement sequence — we use the write_lock via
                        # higher-level API normally; here we exercise raw SQL
                        # in a lock to mirror what a Service method would do.
                        conn.execute(
                            "INSERT INTO users (user_id, created_at) VALUES (?, ?)",
                            (uid, datetime.now(timezone.utc).isoformat()),
                        )
                        conn.execute(
                            "INSERT INTO telegram_identities (chat_id, user_id) "
                            "VALUES (?, ?)",
                            (chat_id, uid),
                        )
                        conn.commit()
                        created_ids.append(uid)

            with ThreadPoolExecutor(max_workers=threads) as pool:
                for f in as_completed(
                    [pool.submit(worker, t) for t in range(threads)]
                ):
                    f.result()

            n_users = db.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            n_ids = db.conn.execute(
                "SELECT COUNT(*) FROM telegram_identities"
            ).fetchone()[0]
            assert n_users == threads * per_thread
            assert n_ids == threads * per_thread
            assert len(set(created_ids)) == threads * per_thread
        finally:
            db.close()

    def test_wal_mode_persists(self, db_path):
        """Once WAL is set, every new connection (thread) should see WAL."""
        db = Database(db_path=db_path)
        try:
            results: list[str] = []

            def check() -> None:
                results.append(
                    db.conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
                )

            threads = [threading.Thread(target=check) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert all(r == "wal" for r in results), results
        finally:
            db.close()
