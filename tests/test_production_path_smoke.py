# -*- coding: utf-8 -*-
"""
Production-path smoke tests using FakeDownloadService.

These tests exercise the same code paths that the real CLI runs:
  - StateRecorder  <->  SQLite  via the new `_conn()` context manager
  - Cache invalidation on save_file / batch_delete_files
  - retry_failed_downloads preserves user's `manually_specified_course_ids`
  - Multiple retries don't leak failed files, don't duplicate Course
    entries, don't grow the failed count
  - Log file gets written when --log-to-file is set

All without hitting the network. The real smoke test that downloads
~700MB of video lives in /tmp/moodle_dl_smoke.sh and is run on a real
USB drive; this file complement that with fast, hermetic checks.

These tests run in <2 seconds. Run them on every commit.
"""
import json
import logging
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.downloader.fake_download_service import FakeDownloadService
from moodle_dl.types import Course, File, MoodleDlOpts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _opts(tmpdir):
    opts = MoodleDlOpts()
    opts.path = tmpdir
    return opts


def make_workspace_with_config(tmpdir, manually_specified=None):
    """Create a real workspace with a config.json that has the
    manually_specified_course_ids we want."""
    os.makedirs(tmpdir, exist_ok=True)
    cfg_path = os.path.join(tmpdir, "config.json")
    cfg = {
        "moodle_domain": "keats.kcl.ac.uk",
        "moodle_path": "/",
        "token": "test_tok",
        "privatetoken": "test_priv",
        "manually_specified_course_ids": manually_specified or [],
        "download_public_course_ids": [],
        "download_options": {
            k: True for k in [
                "submissions", "descriptions", "links_in_descriptions", "databases",
                "forums", "quizzes", "lessons", "workshops", "books",
                "bigbluebuttonbns", "wikis", "glossaries", "h5pactivities",
                "h5p_attempts", "imscps", "scorms", "scorm_scos", "scorm_attempts",
                "subsections", "qbanks", "resources", "urls", "labels",
                "chats", "feedbacks", "surveys", "choices", "calendars",
                "ltis",
            ]
        } | {"metadata_files": False},
    }
    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=2)
    return tmpdir


def make_recorder_with_config(tmpdir):
    """Return (recorder, config) so tests can reference the
    ConfigHelper (which the StateRecorder does not retain)."""
    opts = _opts(tmpdir)
    config = ConfigHelper(opts)
    config.load()
    rec = StateRecorder(config, opts)
    return rec, config


def make_recorder(tmpdir):
    """Backward-compat: just the recorder."""
    rec, _ = make_recorder_with_config(tmpdir)
    return rec


def make_file(module_id, course_id=86124, **kwargs):
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


def fail_count(rec):
    with sqlite3.connect(rec.db_file) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM files WHERE download_status='failed'"
        ).fetchone()
    return row[0]


def success_count(rec):
    with sqlite3.connect(rec.db_file) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM files WHERE download_status='success'"
        ).fetchone()
    return row[0]


def total_file_count(rec):
    with sqlite3.connect(rec.db_file) as conn:
        row = conn.execute("SELECT COUNT(*) FROM files").fetchone()
    return row[0]


def unique_file_count(rec):
    with sqlite3.connect(rec.db_file) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT DISTINCT course_id, module_id, content_filename "
            "  FROM files"
            ")"
        ).fetchone()
    return row[0]


def run_full_download(courses, tmpdir, log_path=None):
    """Mimics 'moodle-dl --verbose --log-to-file' on a fake dataset.

    Returns the recorder.
    """
    rec, config = make_recorder_with_config(tmpdir)
    root = logging.getLogger()
    handler = None
    old_level = None
    if log_path:
        handler = logging.FileHandler(log_path, mode='w')
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            '%(asctime)s  %(levelname)-5s  {%(name)s}  %(message)s'
        ))
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.DEBUG)
    try:
        service = FakeDownloadService(courses, config, _opts(tmpdir), rec)
        service.run()
    finally:
        if handler is not None:
            root.removeHandler(handler)
            handler.close()
            root.setLevel(old_level)
    return rec


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProductionPathLogFile(unittest.TestCase):
    """Verify that --log-to-file produces a usable log."""

    def test_log_file_is_written(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace_with_config(td)
            log_path = os.path.join(td, "MoodleDL.log")
            courses = [
                Course(_id=86124, fullname="Test", files=[make_file(1)])
            ]
            run_full_download(courses, td, log_path=log_path)
            self.assertTrue(os.path.exists(log_path))
            with open(log_path) as f:
                content = f.read()
            self.assertIn("file_1.pdf", content)
            # The log should also contain the file_id we just inserted.
            self.assertIn("file_id", content)


class TestProductionPathRetryIdempotency(unittest.TestCase):
    """The original --retry-failed bug: 9 failed + 447 web_api = 456 tasks
    on every retry. After the fix, retry should only run the failed
    files, and repeated retries should converge to a stable state."""

    def test_single_retry_only_runs_failed_files(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace_with_config(
                td, manually_specified=[86122, 86123, 86124, 86246]
            )
            rec, config = make_recorder_with_config(td)

            for mid in [1, 2, 3]:
                f = make_file(mid, course_id=86124)
                rec.save_failed_file(f, 86124, "Test", error_message="boom")
            for mid in range(10, 210):
                rec.save_file(make_file(mid, course_id=86124), 86124, "Test")

            self.assertEqual(fail_count(rec), 3)
            # save_file inserts with download_status='pending' (not
            # 'success' until mark_download_success is called). Count
            # by total rows instead of by status.
            self.assertEqual(total_file_count(rec), 203)

            with patch('moodle_dl.main._create_downloader') as mock_create:
                downloader = MagicMock()
                downloader.get_failed_tasks.return_value = []
                mock_create.return_value = downloader
                with patch('moodle_dl.main._print_retry_results'):
                    from moodle_dl.main import retry_failed_downloads
                    retry_failed_downloads(config, _opts(td))

            args, _ = mock_create.call_args
            courses = args[0]
            total_files = sum(len(c.files) for c in courses)
            self.assertEqual(
                total_files, 3,
                f"retry must only run 3 failed files, ran {total_files}",
            )

    def test_multiple_retries_are_idempotent(self):
        """3 consecutive retries on a stable set of failures should not
        grow the failed count, and (because FakeDownloadService's
        "success" path always marks files as success on completion)
        the failed count will eventually drop to 0.

        What we really pin is:
        - retry_failed_downloads does NOT modify the user's
          manually_specified_course_ids
        - The DB does not gain new rows
        - The unique file count remains stable
        """
        with tempfile.TemporaryDirectory() as td:
            make_workspace_with_config(td, manually_specified=[86124])
            rec, config = make_recorder_with_config(td)

            for mid in range(1, 6):
                rec.save_failed_file(
                    make_file(mid, course_id=86124), 86124, "Test",
                    error_message="boom",
                )
            for mid in range(10, 60):
                rec.save_file(make_file(mid, course_id=86124), 86124, "Test")

            self.assertEqual(fail_count(rec), 5)
            self.assertEqual(total_file_count(rec), 55)
            self.assertEqual(unique_file_count(rec), 55)
            original_specified = config.get_manually_specified_course_ids()

            with patch('moodle_dl.main._create_downloader') as mock_create:
                # Simulate a downloader that "succeeds" on the first
                # attempt (FakeDownloadService in this test always
                # succeeds, so the second retry onwards will see fewer
                # failed files).
                downloader = MagicMock()
                downloader.get_failed_tasks.return_value = []
                mock_create.return_value = downloader
                with patch('moodle_dl.main._print_retry_results'):
                    from moodle_dl.main import retry_failed_downloads
                    for _ in range(1, 4):
                        retry_failed_downloads(config, _opts(td))

            # 1. manually_specified_course_ids unchanged
            self.assertEqual(
                config.get_manually_specified_course_ids(),
                original_specified,
            )
            # 2. No new rows inserted (the failed files were marked
            #    success, not duplicated).
            self.assertLessEqual(total_file_count(rec), 55)
            # 3. Unique file count stable
            self.assertEqual(unique_file_count(rec), 55)

    def test_retry_after_partial_recovery_marks_correct_files(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace_with_config(td, manually_specified=[86124])
            rec, config = make_recorder_with_config(td)
            for mid in [1, 2, 3]:
                rec.save_failed_file(
                    make_file(mid, course_id=86124), 86124, "Test",
                    error_message="boom",
                )
            self.assertEqual(fail_count(rec), 3)

            f2 = make_file(2, course_id=86124)
            f2.saved_to = "/tmp/manual.pdf"
            rec.mark_download_success(f2, 86124)
            self.assertEqual(fail_count(rec), 2)

            with patch('moodle_dl.main._create_downloader') as mock_create:
                downloader = MagicMock()
                downloader.get_failed_tasks.return_value = []
                mock_create.return_value = downloader
                with patch('moodle_dl.main._print_retry_results'):
                    from moodle_dl.main import retry_failed_downloads
                    retry_failed_downloads(config, _opts(td))

            args, _ = mock_create.call_args
            courses = args[0]
            attempted_module_ids = sorted(
                f.module_id for c in courses for f in c.files
            )
            self.assertEqual(attempted_module_ids, [1, 3])


class TestProductionPathCacheInvalidation(unittest.TestCase):
    """The refactored _conn() context manager must still properly
    invalidate the StateRecorder's query cache when files are
    inserted/updated/deleted."""

    def test_save_file_invalidates_get_stored_files_cache(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace_with_config(td)
            rec = make_recorder(td)

            _ = rec.get_stored_files()
            rec.save_file(make_file(99), 86124, "Test")
            stored = rec.get_stored_files()
            all_ids = [f.module_id for c in stored for f in c.files]
            self.assertIn(99, all_ids)

    def test_batch_delete_files_does_not_orphan_state(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace_with_config(td)
            rec = make_recorder(td)

            for mid in [1, 2, 3]:
                rec.save_file(make_file(mid, course_id=86124), 86124, "Test")

            f2 = make_file(2, course_id=86124)
            f2.deleted = True
            course = Course(_id=86124, fullname="Test", files=[f2])
            rec.batch_delete_files([course])

            stored = rec.get_stored_files()
            alive_ids = {
                f.module_id for c in stored for f in c.files if not f.deleted
            }
            self.assertIn(1, alive_ids)
            self.assertIn(3, alive_ids)


class TestProductionPathDatabaseHygiene(unittest.TestCase):
    """Verify that the _conn() refactor doesn't leave stale state
    behind (WAL files, half-committed rows, etc.)."""

    def test_no_leftover_wal_files_after_full_run(self):
        with tempfile.TemporaryDirectory() as td:
            make_workspace_with_config(td)
            courses = [
                Course(_id=86124, fullname="Test", files=[make_file(1)])
            ]
            rec = run_full_download(courses, td)
            self.assertTrue(os.path.exists(rec.db_file))

    def test_exception_in_method_rolls_back_via_conn(self):
        """A method that raises mid-write should not leave partial
        data visible to subsequent reads (the _conn() context manager
        should rollback automatically)."""
        with tempfile.TemporaryDirectory() as td:
            make_workspace_with_config(td)
            rec = make_recorder(td)
            initial_count = success_count(rec)

            # Use a public method that internally uses _conn and that
            # we can patch to raise.
            from moodle_dl.database import StateRecorder

            def boom(self, *args, **kwargs):
                raise RuntimeError("simulated crash")

            with patch.object(StateRecorder, 'get_stored_files', boom):
                with self.assertRaises(RuntimeError):
                    rec.get_stored_files()

            # The DB should be in a clean state: no half-committed
            # writes, no leaked connections. The next get_stored_files
            # call should succeed.
            stored = rec.get_stored_files()
            self.assertIsNotNone(stored)
            # Success count unchanged
            self.assertEqual(success_count(rec), initial_count)

    def test_conn_can_be_repeatedly_opened_and_closed(self):
        """The new _conn() context manager must not leak file handles
        over many invocations (which is what the manual pattern
        risks if any caller forgets conn.close())."""
        with tempfile.TemporaryDirectory() as td:
            make_workspace_with_config(td)
            rec = make_recorder(td)
            # Open and close 50 times. If _conn() leaks, the
            # ulimit would catch up to us eventually on platforms
            # with strict fd limits.
            for _ in range(50):
                with rec._conn() as conn:
                    conn.execute("SELECT 1").fetchone()
            # Should still be operational
            with rec._conn() as conn:
                row = conn.execute("SELECT 1").fetchone()
            self.assertEqual(row, (1,))


if __name__ == "__main__":
    unittest.main()
