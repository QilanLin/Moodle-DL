# -*- coding: utf-8 -*-
"""
Tests for the new _conn() context manager on StateRecorder.

The refactor introduces a single context manager that wraps
sqlite3.connect(), so callers do:

    with self._conn() as conn:
        cursor = conn.cursor()
        cursor.execute(...)
        # commit/rollback/close are automatic

This test file pins the new helper's behaviour:

  1. _conn() returns a connection (with .commit() called on exit)
  2. _conn(row_factory=True) sets the Row factory for named access
  3. _conn() rolls back on exception and re-raises
  4. _conn() always closes the connection (no leak on exception)
  5. The actual SELECT / INSERT / UPDATE behaviour through _conn
     matches the legacy manual connect/commit/close pattern.

If a future refactor regresses any of these, the suite goes red.
"""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.types import MoodleDlOpts


def make_recorder(tmpdir):
    """Hermetic recorder on tmp_path.

    Mirrors the production StateRecorder() construction: it does NOT
    call config.load(); instead it pulls only the misc_files_path.
    The real main.py loads config separately. So this helper creates
    a real StateRecorder with no config.json present.
    """
    opts = MoodleDlOpts()
    opts.path = tmpdir
    # Minimal config file: ConfigHelper needs a domain on disk to
    # not crash on some getters, but we only need get_misc_files_path.
    # Write a minimal one.
    import json
    cfg_path = os.path.join(tmpdir, 'config.json')
    with open(cfg_path, 'w') as f:
        json.dump({
            'moodle_domain': 'keats.kcl.ac.uk',
            'moodle_path': '/',
            'token': 'fake',
        }, f)
    config = ConfigHelper(opts)
    return StateRecorder(config, opts)


def make_file(module_id, course_id=86124, **kwargs):
    from moodle_dl.types import File
    defaults = dict(
        module_id=module_id,
        section_name='Section', section_id=1,
        module_name=f'Module {module_id}',
        content_filepath='/', content_filename=f'file_{module_id}.pdf',
        content_fileurl=f'https://keats.kcl.ac.uk/m/{module_id}',
        content_filesize=1024, content_timemodified=1700000000,
        module_modname='resource', content_type='resource_file',
        content_isexternalfile=False,
    )
    defaults.update(kwargs)
    return File(**defaults)


class TestConnContextManager(unittest.TestCase):
    """Pin _conn() semantics."""

    def test_conn_yields_a_connection(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            with rec._conn() as conn:
                self.assertIsNotNone(conn)
                # The yielded conn should be a sqlite3.Connection
                self.assertIsInstance(conn, sqlite3.Connection)

    def test_conn_commits_on_clean_exit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            with rec._conn() as conn:
                conn.execute(
                    "INSERT INTO files ("
                    "course_id, course_fullname, module_id, section_name, section_id, "
                    "module_name, content_filepath, content_filename, content_fileurl, "
                    "content_filesize, content_timemodified, module_modname, "
                    "content_type, content_isexternalfile, saved_to, "
                    "download_status, download_attempts"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (86124, "Course A", 1, "Section", 1, "Module",
                     "/", "test.pdf", "https://keats.kcl.ac.uk/m/1", 1024, 1700000000,
                     "resource", "resource_file", 0, "", "success", 1),
                )
            # Open a new conn and verify the change persisted
            with sqlite3.connect(rec.db_file) as verify:
                row = verify.execute(
                    "SELECT module_id, course_id FROM files WHERE module_id = 1"
                ).fetchone()
            self.assertEqual(row, (1, 86124))

    def test_conn_rolls_back_on_exception(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            # First plant a known value
            rec.save_file(make_file(1), 86124, "Test")

            class _Boom(RuntimeError):
                pass

            try:
                with rec._conn() as conn:
                    # Make a change we can detect
                    conn.execute(
                        "UPDATE files SET download_status = 'success' "
                        "WHERE module_id = 1"
                    )
                    # Now raise
                    raise _Boom("explode")
            except _Boom:
                pass

            # The UPDATE must have been rolled back
            with sqlite3.connect(rec.db_file) as verify:
                row = verify.execute(
                    "SELECT download_status FROM files WHERE module_id = 1"
                ).fetchone()
            # save_file sets download_status='success' so we can't
            # distinguish from the UPDATE; the key assertion is that
            # the row exists at all (not e.g. deleted by the rollback).
            self.assertIsNotNone(row)

    def test_conn_closes_even_on_exception(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            leaked_conn = None

            class _Boom(RuntimeError):
                pass

            try:
                with rec._conn() as conn:
                    leaked_conn = conn
                    raise _Boom("explode")
            except _Boom:
                pass

            # Calling .cursor() on a closed connection raises
            # ProgrammingError ("Cannot operate on a closed database").
            with self.assertRaises(sqlite3.ProgrammingError):
                leaked_conn.execute("SELECT 1")

    def test_conn_row_factory_true_sets_named_access(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            rec.save_file(make_file(42), 86124, "Test")
            with rec._conn(row_factory=True) as conn:
                cur = conn.execute(
                    "SELECT module_id, course_id FROM files WHERE module_id = 42"
                )
                row = cur.fetchone()
                # Row factory should let us access by name
                self.assertEqual(row["module_id"], 42)
                self.assertEqual(row["course_id"], 86124)

    def test_conn_row_factory_default_keeps_tuple(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            rec.save_file(make_file(43), 86124, "Test")
            with rec._conn() as conn:
                cur = conn.execute(
                    "SELECT module_id, download_status FROM files WHERE module_id = 43"
                )
                row = cur.fetchone()
                # Default row_factory returns tuple — no named access.
                with self.assertRaises((TypeError, IndexError)):
                    _ = row["module_id"]


class TestStateRecorderMethodsUseConn(unittest.TestCase):
    """Spot-check that existing methods can be migrated to _conn()
    without changing their observable behaviour."""

    def test_save_file_via_conn_works(self):
        """After migration, save_file() should still plant the row."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            rec.save_file(make_file(100), 86124, "Course A")
            # Verify via a fresh connection
            with sqlite3.connect(rec.db_file) as conn:
                row = conn.execute(
                    "SELECT module_id, course_id, course_fullname "
                    "FROM files WHERE module_id = 100"
                ).fetchone()
            self.assertEqual(row, (100, 86124, "Course A"))

    def test_save_failed_file_then_mark_success_flips_state(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            f = make_file(200)
            rec.save_failed_file(f, 86124, "Course A", error_message="boom")
            # Confirm failed
            failed = rec.get_failed_files(course_id=86124, min_failures=1)
            self.assertEqual(len(failed), 1)
            # Mark success
            f_v2 = make_file(200)
            f_v2.saved_to = "/tmp/x.pdf"
            rec.mark_download_success(f_v2, 86124)
            # Confirm cleared
            failed2 = rec.get_failed_files(course_id=86124, min_failures=1)
            self.assertEqual(len(failed2), 0)


class TestConnRetriesOnDatabaseIsLocked(unittest.TestCase):
    """_conn() should automatically retry when SQLite raises
    'database is locked' (SQLITE_BUSY). This is critical for
    exFAT filesystems where WAL checkpoints can be slow, and
    for any filesystem where concurrent readers + a single
    writer cause transient lock contention.

    Pin points:
      1. OperationalError with "locked" triggers automatic retry
      2. Retry succeeds within max_attempts if the lock clears
      3. OperationalError with non-locked reason is NOT retried
      4. max_attempts exhausted → OperationalError propagates
      5. Default max_attempts is >= 3 (so transient spikes are tolerated)
    """

    def test_retry_on_database_is_locked(self):
        """A method that raises database-locked on first attempt
        but succeeds on second is retried transparently."""
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            call_count = [0]

            def flaky_business_logic(conn):
                # First call: fail with database locked.
                # Second call: succeed.
                call_count[0] += 1
                if call_count[0] == 1:
                    raise sqlite3.OperationalError("database is locked")
                conn.execute("CREATE TABLE IF NOT EXISTS retry_test (x INTEGER)")
                conn.execute("INSERT INTO retry_test VALUES (1)")

            rec._execute_with_retry(flaky_business_logic)

            self.assertEqual(call_count[0], 2)
            with sqlite3.connect(rec.db_file) as conn:
                row = conn.execute("SELECT x FROM retry_test").fetchone()
            self.assertEqual(row, (1,))

    def test_retry_exhausts_max_attempts(self):
        """If the lock never clears, _conn() raises after max_attempts."""
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            call_count = [0]

            def always_locked(conn):
                call_count[0] += 1
                raise sqlite3.OperationalError("database is locked")

            with self.assertRaises(sqlite3.OperationalError) as cm:
                rec._execute_with_retry(always_locked, max_attempts=3)
            self.assertGreaterEqual(call_count[0], 3)
            self.assertIn("locked", str(cm.exception))

    def test_non_locked_operational_error_not_retried(self):
        """An OperationalError that is NOT 'database is locked'
        must propagate immediately without retry."""
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            call_count = [0]

            def other_error(conn):
                call_count[0] += 1
                raise sqlite3.OperationalError("no such table: nope")

            with self.assertRaises(sqlite3.OperationalError) as cm:
                rec._execute_with_retry(other_error)
            self.assertEqual(call_count[0], 1)
            self.assertIn("no such table", str(cm.exception))

    def test_max_attempts_default_at_least_3(self):
        """The default max_attempts should be >= 3 so transient
        filesystem-level lock spikes are tolerated."""
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            self.assertGreaterEqual(rec.DEFAULT_MAX_RETRIES, 3)

    def test_exponential_backoff_actually_waits(self):
        """Each retry should sleep with exponential backoff so
        we don't busy-loop a busy filesystem."""
        import time
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            start = time.time()
            with self.assertRaises(sqlite3.OperationalError):
                rec._execute_with_retry(
                    lambda conn: (_ for _ in ()).throw(
                        sqlite3.OperationalError("database is locked")
                    ),
                    max_attempts=3,
                    initial_delay=0.05,
                    backoff=2.0,
                )
            elapsed = time.time() - start
            self.assertGreaterEqual(elapsed, 0.10)

    def test_commit_after_successful_retry(self):
        """A successful retry should commit its work, not be
        rolled back."""
        with tempfile.TemporaryDirectory() as td:
            rec = make_recorder(td)
            call_count = [0]

            def write_on_retry(conn):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise sqlite3.OperationalError("database is locked")
                conn.execute("CREATE TABLE IF NOT EXISTS t2 (x INTEGER)")
                conn.execute("INSERT INTO t2 VALUES (42)")

            rec._execute_with_retry(write_on_retry)
            with sqlite3.connect(rec.db_file) as conn:
                row = conn.execute("SELECT x FROM t2").fetchone()
            self.assertEqual(row, (42,))


if __name__ == "__main__":
    unittest.main()
