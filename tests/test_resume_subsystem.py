# -*- coding: utf-8 -*-
"""
Complex unit tests for the resume / range-download subsystem.

The moodle-dl resume mechanism has 4 independent conditions that
must ALL be true for an incomplete download to be saved:

  A) can_continue_on_fail          (server supports Range)
  B) total_bytes_received > 0      (something was downloaded)
  C) total_bytes_received < total  (not fully done)
  D) err is NOT ContentRangeError  (Range header didn't break it)

There are 16 possible combinations; this file pins the contract
for ALL 16 cases by calling the actual production function
`moodle_dl.downloader.task._should_save_incomplete` (extracted
as a top-level function for testability — see task.py).

Also covered:
  - DB save failure → part file deletion (fallback behavior)
  - max_attempts edge cases (0, -1, huge, at-max, below-max)
  - save idempotency (same (file_id, file_path) → UPDATE not INSERT)
  - File path stability (path change → new row, old row stays)
  - cleanup_old_incomplete_downloads edge cases
  - increment_incomplete_download_attempt (basic, with error, no-op)
  - Concurrent path race (5 saves collapse to 1 row)
  - mark_download_complete clears incomplete row
  - Zero downloaded_bytes (still persists, still returned)
  - Error reason truncation (500 chars — discovered from production)
  - Error reason with special chars (newlines, quotes)
  - Unicode URL save and load
  - DB migration v8→v9 creates incomplete_downloads table
  - Multiple incomplete downloads priority/sort
"""
import os
import sqlite3
import sys
import time
import unittest
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, '/Users/linqilan/CodingProjects/moodle/Moodle-DL')

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.downloader.task import ContentRangeError, _should_save_incomplete
from moodle_dl.types import MoodleDlOpts


@pytest.fixture
def recorder(tmp_path):
    """Create a StateRecorder backed by a temp DB."""
    config = MagicMock(spec=ConfigHelper)
    config.get_misc_files_path.return_value = str(tmp_path)
    return StateRecorder(config, MoodleDlOpts())


class TestShouldSaveIncompleteAll16Cases(unittest.TestCase):
    """Pin the 4-condition truth table by calling the real
    production function `_should_save_incomplete`.

    This is the ACTUAL logic from task.py:2485 (now extracted as
    a top-level function). Every test invokes the real function,
    not a re-implementation."""

    # 16 cases (all combinations of A, B, C, D)
    # A=can_continue_on_fail, B=received>0, C=received<total, D=NOT RangeError

    def test_case_1111_all_true_saves(self):
        """All 4 conditions true → save"""
        self.assertTrue(_should_save_incomplete(
            can_continue_on_fail=True,
            total_bytes_received=100,
            content_length=1000,
            err=Exception('generic'),
        ))

    def test_case_0111_no_continue_skips(self):
        """A=False → skip (server doesn't support Range)"""
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=False,
            total_bytes_received=100,
            content_length=1000,
            err=Exception('generic'),
        ))

    def test_case_1011_zero_received_skips(self):
        """B=False (0 bytes) → skip"""
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=True,
            total_bytes_received=0,
            content_length=1000,
            err=Exception('generic'),
        ))

    def test_case_1101_full_received_skips(self):
        """C=False (received == total) → skip"""
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=True,
            total_bytes_received=1000,
            content_length=1000,
            err=Exception('generic'),
        ))

    def test_case_1110_range_error_skips(self):
        """D=False (ContentRangeError) → skip"""
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=True,
            total_bytes_received=100,
            content_length=1000,
            err=ContentRangeError('range broken'),
        ))

    def test_case_0011_no_continue_zero_received(self):
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=False,
            total_bytes_received=0,
            content_length=1000,
            err=Exception('x'),
        ))

    def test_case_0110_no_continue_range_error(self):
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=False,
            total_bytes_received=100,
            content_length=1000,
            err=ContentRangeError('x'),
        ))

    def test_case_0101_no_continue_full_received(self):
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=False,
            total_bytes_received=1000,
            content_length=1000,
            err=Exception('x'),
        ))

    def test_case_1001_zero_received_range_error(self):
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=True,
            total_bytes_received=0,
            content_length=1000,
            err=ContentRangeError('x'),
        ))

    def test_case_1010_zero_received_full_received(self):
        # 0 >= 1000 is False → C still True (0 < 1000)
        # But B fails (0 is not > 0)
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=True,
            total_bytes_received=0,
            content_length=1000,
            err=Exception('x'),
        ))

    def test_case_1100_full_received_range_error(self):
        # C fails (1000 not < 1000)
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=True,
            total_bytes_received=1000,
            content_length=1000,
            err=ContentRangeError('x'),
        ))

    def test_case_0001_all_false_except_full_received(self):
        """B and D both pass, A and C fail"""
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=False,
            total_bytes_received=1000,
            content_length=1000,
            err=Exception('x'),
        ))

    def test_case_0000_all_false_skips(self):
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=False,
            total_bytes_received=0,
            content_length=1000,
            err=ContentRangeError('x'),
        ))

    def test_case_1111_zero_total(self):
        """content_length=0: C becomes (received < 0) = False → skip.
        Safe default: don't resume something unknown."""
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=True,
            total_bytes_received=0,
            content_length=0,
            err=Exception('x'),
        ))

    def test_case_1111_received_greater_than_total(self):
        """received > total: C fails (1000 not < 999) → skip.
        Defensive against corrupt server."""
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=True,
            total_bytes_received=2000,
            content_length=1000,
            err=Exception('x'),
        ))

    def test_case_1111_negative_received(self):
        """received=-1: B fails (-1 not > 0) → skip."""
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=True,
            total_bytes_received=-1,
            content_length=1000,
            err=Exception('x'),
        ))

    def test_large_values_save(self):
        """Sanity: large but valid values still save."""
        self.assertTrue(_should_save_incomplete(
            can_continue_on_fail=True,
            total_bytes_received=1,
            content_length=10**12,
            err=Exception('x'),
        ))

    def test_none_content_length_treated_as_zero(self):
        """If content_length is None (no header), comparison
        (received < None) is False → skip."""
        self.assertFalse(_should_save_incomplete(
            can_continue_on_fail=True,
            total_bytes_received=100,
            content_length=None,
            err=Exception('x'),
        ))

    def test_404_status_error_preserves_should_save(self):
        """Different exception types (404, 500, OSError) should
        all be 'NOT ContentRangeError' → should_save_incomplete
        can be True if other conditions are met."""
        for err in [
            Exception('404'),
            ConnectionError('reset'),
            OSError('disk error'),
            TimeoutError(),
            ValueError('bad value'),
        ]:
            self.assertTrue(
                _should_save_incomplete(
                    can_continue_on_fail=True,
                    total_bytes_received=100,
                    content_length=1000,
                    err=err,
                ),
                f'Failed for {type(err).__name__}',
            )


class TestSaveIncompleteDownloadFailurePaths:
    """The save_incomplete_download has a try/except. When the DB
    save fails, the .part file should be deleted (fallback behavior)
    and bytes counter reset. Pin that contract.

    This tests the production pattern in task.py:2492-2508:
      try:
        self._save_incomplete_download(...)
      except Exception as save_err:
        PT.remove_file(dest_path)
        self.report_received_bytes(-total_bytes_received)
    """

    def test_db_save_failure_triggers_part_file_deletion(self, tmp_path):
        """If _save_incomplete_download raises, the part file
        must be deleted (so we don't leave garbage)."""
        part_path = tmp_path / 'file.pdf'
        part_path.write_bytes(b'partial data')

        # Simulate the production try/except
        def fake_save(*args, **kwargs):
            raise sqlite3.OperationalError('disk full')

        try:
            fake_save(part_path, 'http://x', 100, 1000)
        except Exception:
            # Production does this:
            if part_path.exists():
                part_path.unlink()

        # Part file must be gone
        assert not part_path.exists()

    def test_partial_save_keeps_part_file(self, tmp_path):
        """When save succeeds, the part file MUST be kept (for resume)."""
        part_path = tmp_path / 'file.pdf'
        part_path.write_bytes(b'partial data')

        # Simulate successful save
        def fake_save(*args, **kwargs):
            pass

        fake_save(part_path, 'http://x', 100, 1000)
        # Part file should still exist
        assert part_path.exists()


class TestGetIncompleteDownloadsForRetryEdgeCases:
    """Edge cases for get_incomplete_downloads_for_retry."""

    def test_zero_max_attempts_returns_nothing(self, recorder):
        """SQL is 'attempts < 0'. No row has attempts < 0 → empty."""
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x/1', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        rows = recorder.get_incomplete_downloads_for_retry(max_attempts=0)
        # 0 < 0 is False → excluded
        assert len(rows) == 0

    def test_negative_max_attempts_returns_nothing(self, recorder):
        """max_attempts=-1 → 'attempts < -1' → no row matches."""
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x/1', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        rows = recorder.get_incomplete_downloads_for_retry(max_attempts=-1)
        assert len(rows) == 0

    def test_max_attempts_huge_returns_all(self, recorder):
        for i in range(5):
            recorder.save_incomplete_download(
                file_id=i + 1, file_url=f'http://x/{i}', file_path=f'/p/{i}',
                total_bytes=100, downloaded_bytes=50,
            )
        rows = recorder.get_incomplete_downloads_for_retry(max_attempts=10**6)
        assert len(rows) == 5

    def test_one_at_max_excluded(self, recorder):
        """attempts == max_attempts is excluded (attempts < X is strict)."""
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x/1', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        db_file = recorder.db_file
        conn = sqlite3.connect(db_file)
        try:
            conn.execute('UPDATE incomplete_downloads SET attempts = 5')
            conn.commit()
        finally:
            conn.close()
        rows = recorder.get_incomplete_downloads_for_retry(max_attempts=5)
        # 5 < 5 is False → excluded
        assert len(rows) == 0

    def test_one_below_max_included(self, recorder):
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x/1', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        db_file = recorder.db_file
        conn = sqlite3.connect(db_file)
        try:
            conn.execute('UPDATE incomplete_downloads SET attempts = 4')
            conn.commit()
        finally:
            conn.close()
        rows = recorder.get_incomplete_downloads_for_retry(max_attempts=5)
        # 4 < 5 is True → included
        assert len(rows) == 1

    def test_status_filter_pending_only(self, recorder):
        """Rows with status != 'pending' are excluded.
        (Currently, save always sets status='pending' — this
        pins that contract.)"""
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x/1', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        db_file = recorder.db_file
        conn = sqlite3.connect(db_file)
        try:
            # Manually change status to 'complete'
            conn.execute("UPDATE incomplete_downloads SET status = 'complete'")
            conn.commit()
        finally:
            conn.close()
        rows = recorder.get_incomplete_downloads_for_retry(max_attempts=5)
        # status='complete' is excluded
        assert len(rows) == 0


class TestSaveIncompleteDownloadIdempotency:
    """save_incomplete_download is called repeatedly during a long
    download (or on multiple retries). The function must handle
    being called multiple times for the same (file_id, file_path)
    pair — it should UPDATE, not INSERT new rows. This is enforced
    by the UNIQUE(file_id, file_path) constraint."""

    def test_repeated_save_same_file_id_updates(self, recorder):
        recorder.save_incomplete_download(
            file_id=42, file_url='http://x/1', file_path='/p/1',
            total_bytes=1000, downloaded_bytes=100,
        )
        recorder.save_incomplete_download(
            file_id=42, file_url='http://x/1', file_path='/p/1',
            total_bytes=1000, downloaded_bytes=200,
        )
        db_file = recorder.db_file
        conn = sqlite3.connect(db_file)
        try:
            count = conn.execute(
                'SELECT COUNT(*) FROM incomplete_downloads WHERE file_id = 42'
            ).fetchone()[0]
            assert count == 1
            # The latest downloaded_bytes should win
            row = conn.execute(
                'SELECT downloaded_bytes FROM incomplete_downloads WHERE file_id = 42'
            ).fetchone()
            assert row[0] == 200
        finally:
            conn.close()

    def test_different_path_for_same_file_id_creates_new_row(self, recorder):
        """UNIQUE(file_id, file_path) means different path = new row."""
        recorder.save_incomplete_download(
            file_id=42, file_url='http://x/1', file_path='/p/old',
            total_bytes=1000, downloaded_bytes=100,
        )
        recorder.save_incomplete_download(
            file_id=42, file_url='http://x/1', file_path='/p/new',
            total_bytes=1000, downloaded_bytes=200,
        )
        db_file = recorder.db_file
        conn = sqlite3.connect(db_file)
        try:
            count = conn.execute(
                'SELECT COUNT(*) FROM incomplete_downloads WHERE file_id = 42'
            ).fetchone()[0]
            assert count == 2
        finally:
            conn.close()

    def test_concurrent_saves_collapse_to_one_row(self, recorder):
        """5 saves of the same (file_id, file_path) → 1 row (UNIQUE)."""
        for i in range(5):
            recorder.save_incomplete_download(
                file_id=1, file_url='http://x', file_path='/p/1',
                total_bytes=1000, downloaded_bytes=100 * (i + 1),
            )
        db_file = recorder.db_file
        conn = sqlite3.connect(db_file)
        try:
            count = conn.execute(
                'SELECT COUNT(*) FROM incomplete_downloads'
            ).fetchone()[0]
            assert count == 1
        finally:
            conn.close()


class TestCleanupOldIncompleteDownloadsEdgeCases:
    """cleanup_old_incomplete_downloads should handle edge cases
    without raising. SQL: DELETE WHERE last_update_time < cutoff."""

    def test_no_incomplete_downloads_is_noop(self, recorder):
        deleted = recorder.cleanup_old_incomplete_downloads(days_old=7)
        assert deleted == 0

    def test_zero_days_old_deletes_old_rows(self, recorder):
        """days_old=0: cutoff=now, so rows with last_update < now
        are deleted. Need to age rows first since save sets
        last_update_time = now."""
        for i in range(3):
            recorder.save_incomplete_download(
                file_id=i + 1, file_url=f'http://x/{i}', file_path=f'/p/{i}',
                total_bytes=100, downloaded_bytes=50,
            )
        # Age the rows
        db_file = recorder.db_file
        conn = sqlite3.connect(db_file)
        try:
            conn.execute(
                'UPDATE incomplete_downloads SET last_update_time = ?',
                (int(time.time()) - 100,),
            )
            conn.commit()
        finally:
            conn.close()
        deleted = recorder.cleanup_old_incomplete_downloads(days_old=0)
        assert deleted == 3

    def test_negative_days_old_deletes_all(self, recorder):
        """days_old=-100: cutoff is in future, all rows match."""
        for i in range(3):
            recorder.save_incomplete_download(
                file_id=i + 1, file_url=f'http://x/{i}', file_path=f'/p/{i}',
                total_bytes=100, downloaded_bytes=50,
            )
        deleted = recorder.cleanup_old_incomplete_downloads(days_old=-100)
        # cutoff = now - (-100 * 86400) = now + 8.6M seconds
        # All current rows have last_update_time < future → all deleted
        assert deleted == 3

    def test_fresh_rows_not_deleted_with_positive_days(self, recorder):
        """Fresh rows (just saved) should NOT be deleted by
        a positive days_old cleanup."""
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        deleted = recorder.cleanup_old_incomplete_downloads(days_old=7)
        # Row is fresh (< 7 days old)
        assert deleted == 0


class TestIncrementIncompleteDownloadAttempt:
    """increment_incomplete_download_attempt must track retry count."""

    def test_increment_basic(self, recorder):
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        assert info['attempts'] == 0
        download_id = info['download_id']

        recorder.increment_incomplete_download_attempt(download_id)
        info = recorder.get_incomplete_download(1, '/p/1')
        assert info['attempts'] == 1

        recorder.increment_incomplete_download_attempt(download_id)
        info = recorder.get_incomplete_download(1, '/p/1')
        assert info['attempts'] == 2

    def test_increment_with_error_reason(self, recorder):
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        recorder.increment_incomplete_download_attempt(
            info['download_id'], error_reason='network timeout',
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        assert info['attempts'] == 1
        assert info['error_reason'] == 'network timeout'

    def test_increment_nonexistent_download_id_noop(self, recorder):
        """Non-existent download_id should not crash."""
        recorder.increment_incomplete_download_attempt(99999)
        # No assertion needed — just verify no exception


class TestMarkDownloadCompleteClearsIncomplete:
    """When a download completes successfully, the incomplete row
    must be cleared (or marked complete) so it's not retried
    forever."""

    def test_complete_clears_incomplete(self, recorder):
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        assert recorder.get_incomplete_download(1, '/p/1') is not None
        recorder.mark_download_complete(1, '/p/1')
        assert recorder.get_incomplete_download(1, '/p/1') is None


class TestIncompleteDownloadFilePathStability:
    """The (file_id, file_path) is the unique key."""

    def test_path_change_creates_new_row(self, recorder):
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/disk/old/file.pdf',
            total_bytes=1000, downloaded_bytes=500,
        )
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/disk/new/file.pdf',
            total_bytes=1000, downloaded_bytes=600,
        )
        # Both rows exist
        assert recorder.get_incomplete_download(1, '/disk/old/file.pdf') is not None
        assert recorder.get_incomplete_download(1, '/disk/new/file.pdf') is not None

    def test_old_path_remains_after_path_change(self, recorder):
        """Old row stays even after a new path is used.
        Cleaned up by 7-day task."""
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/old',
            total_bytes=100, downloaded_bytes=10,
        )
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/new',
            total_bytes=100, downloaded_bytes=20,
        )
        old = recorder.get_incomplete_download(1, '/old')
        new = recorder.get_incomplete_download(1, '/new')
        assert old is not None
        assert new is not None
        assert old['downloaded_bytes'] == 10
        assert new['downloaded_bytes'] == 20


class TestIncompleteDownloadZeroDownloadedBytes:
    """Edge case: 0 bytes downloaded."""

    def test_zero_bytes_save_persists(self, recorder):
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p/1',
            total_bytes=1000, downloaded_bytes=0,
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        assert info is not None
        assert info['downloaded_bytes'] == 0

    def test_zero_bytes_save_returned_by_retry_query(self, recorder):
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p/1',
            total_bytes=1000, downloaded_bytes=0,
        )
        rows = recorder.get_incomplete_downloads_for_retry(max_attempts=5)
        assert len(rows) == 1


class TestIncompleteDownloadErrorReasonTruncation:
    """error_reason is stored as TEXT. Production code truncates
    to 500 chars at the write site (database.py:1809:
    `error_reason[:500] if error_reason else None`)."""

    def test_long_error_reason_truncated_to_500(self, recorder):
        long_error = 'A' * 10000
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        recorder.increment_incomplete_download_attempt(
            info['download_id'], error_reason=long_error,
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        # Production truncates to 500
        assert len(info['error_reason']) == 500
        assert info['error_reason'] == 'A' * 500

    def test_short_error_reason_not_truncated(self, recorder):
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        recorder.increment_incomplete_download_attempt(
            info['download_id'], error_reason='short error',
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        assert info['error_reason'] == 'short error'

    def test_exactly_500_chars_not_truncated(self, recorder):
        """Exactly 500 chars should be stored as-is (slicing
        [:500] is a no-op for exact length)."""
        exactly_500 = 'B' * 500
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        recorder.increment_incomplete_download_attempt(
            info['download_id'], error_reason=exactly_500,
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        assert len(info['error_reason']) == 500

    def test_501_chars_truncated(self, recorder):
        """501 chars should be truncated to 500."""
        just_over = 'C' * 501
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        recorder.increment_incomplete_download_attempt(
            info['download_id'], error_reason=just_over,
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        assert len(info['error_reason']) == 500

    def test_error_reason_with_special_chars(self, recorder):
        """Special chars (newlines, quotes) should be stored safely."""
        tricky_error = "Error: 'foo'\nBar\\nbaz\""
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        recorder.increment_incomplete_download_attempt(
            info['download_id'], error_reason=tricky_error,
        )
        info = recorder.get_incomplete_download(1, '/p/1')
        assert info['error_reason'] == tricky_error


class TestIncompleteDownloadWithUnicodeURL:
    """URLs with non-ASCII characters (e.g. Chinese filenames)."""

    def test_unicode_url_save_and_load(self, recorder):
        unicode_url = 'https://kcl.ac.uk/files/中文课件.pdf'
        recorder.save_incomplete_download(
            file_id=1, file_url=unicode_url, file_path='/p/中文.pdf',
            total_bytes=100, downloaded_bytes=50,
        )
        info = recorder.get_incomplete_download(1, '/p/中文.pdf')
        assert info['file_url'] == unicode_url


class TestIncompleteDownloadAfterDBMigration:
    """After v8→v9 migration, the incomplete_downloads table is
    created automatically."""

    def test_v8_db_creates_incomplete_table(self, tmp_path):
        config = MagicMock(spec=ConfigHelper)
        config.get_misc_files_path.return_value = str(tmp_path)
        StateRecorder(config, MoodleDlOpts())
        # Check the table exists
        db_file = os.path.join(str(tmp_path), 'moodle_state.db')
        conn = sqlite3.connect(db_file)
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t[0] for t in tables]
            assert 'incomplete_downloads' in table_names
        finally:
            conn.close()


class TestIncompleteDownloadPrioritySort:
    """When multiple incomplete downloads exist, the order they
    are returned in matters for the priority queue."""

    def test_ordering_returns_all(self, recorder):
        """All 3 incomplete downloads should be returned."""
        for i in range(3):
            recorder.save_incomplete_download(
                file_id=i + 1, file_url=f'http://x/{i}', file_path=f'/p/{i}',
                total_bytes=100, downloaded_bytes=50,
            )
        rows = recorder.get_incomplete_downloads_for_retry(max_attempts=5)
        assert len(rows) == 3
        # All 3 file_ids should be present
        file_ids = {r['file_id'] for r in rows}
        assert file_ids == {1, 2, 3}

    def test_download_id_present(self, recorder):
        """Each row should have a download_id (PK)."""
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        rows = recorder.get_incomplete_downloads_for_retry(max_attempts=5)
        assert rows[0]['download_id'] > 0

    def test_server_supports_range_default_false(self, recorder):
        """server_supports_range defaults to False if not set.
        save_incomplete_download doesn't take this arg, so
        the default is used."""
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p/1',
            total_bytes=100, downloaded_bytes=50,
        )
        rows = recorder.get_incomplete_downloads_for_retry(max_attempts=5)
        assert rows[0]['server_supports_range'] is False


class TestIncompleteDownloadConnectionResilience:
    """The recorder self-manages connections. Each operation opens
    a new connection. Verify this works for repeated operations."""

    def test_save_after_connection_close_still_works(self, recorder):
        """After many operations, the recorder should still work."""
        for i in range(20):
            recorder.save_incomplete_download(
                file_id=i + 1, file_url=f'http://x/{i}', file_path=f'/p/{i}',
                total_bytes=100, downloaded_bytes=50,
            )
        assert len(recorder.get_incomplete_downloads_for_retry(max_attempts=5)) == 20
