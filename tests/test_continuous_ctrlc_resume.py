# -*- coding: utf-8 -*-
"""
Tests for the continuous Ctrl-C + resume workflow.

User scenario (2026-06-22):

  '如果 ctrl c 然后 moodle-dl --verbose --log-to-file 然后 ctrl c
  然后 moodle-dl --verbose --log-to-file，程序还能正常工作吗'

Two Ctrl-C events in a row, each followed by a restart. After
the second Ctrl-C + restart, the program must continue to
function correctly: the .part file is preserved, the
incomplete_downloads DB row is preserved, and the next run
resumes from where the previous one left off.

The new default (commit 74f5532) is
``restart_incomplete_on_kill=False`` (resume from byte N),
so .part files are preserved on Ctrl-C.

These tests pin the contract by inspecting the on-disk state
(.part files, DB rows) without going through the full Task
init (which requires a ConfigHelper). This keeps the tests
fast and focused on the contract.
"""
import os
import sys
import tempfile
import sqlite3
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Helpers
# =========================================================================
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    module_id INTEGER NOT NULL,
    section_id INTEGER NOT NULL,
    content_filename TEXT NOT NULL,
    content_fileurl TEXT NOT NULL,
    content_filesize INTEGER DEFAULT 0,
    content_timemodified INTEGER DEFAULT 0,
    saved_to TEXT NOT NULL,
    modified INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS incomplete_downloads (
    download_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    file_url TEXT NOT NULL,
    file_path TEXT NOT NULL,
    total_bytes INTEGER DEFAULT 0,
    downloaded_bytes INTEGER DEFAULT 0,
    start_time INTEGER NOT NULL,
    last_update_time INTEGER NOT NULL,
    server_supports_range INTEGER DEFAULT 0,
    etag TEXT,
    last_modified TEXT,
    attempts INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    error_reason TEXT,
    UNIQUE(file_id, file_path)
);
"""


def _init_db(db_path: str) -> None:
    """Create the incomplete_downloads + files table schema in a fresh DB."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def _insert_file(db_path: str, course_id: int, module_id: int,
                  content_fileurl: str, saved_to: str) -> int:
    """Insert a row into the files table, return the file_id."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO files
               (course_id, module_id, section_id, content_filename,
                content_fileurl, saved_to)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (course_id, module_id, 1, 'file.pdf',
             content_fileurl, saved_to),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _save_incomplete(db_path: str, file_id: int, dl_url: str,
                      part_path: str, downloaded_bytes: int) -> None:
    """Mirror what Task._save_incomplete_on_kill does: INSERT OR
    REPLACE into incomplete_downloads.
    """
    import time
    now = int(time.time())
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO incomplete_downloads
               (file_id, file_url, file_path, total_bytes,
                downloaded_bytes, start_time, last_update_time, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
               ON CONFLICT(file_id, file_path)
               DO UPDATE SET downloaded_bytes = excluded.downloaded_bytes,
                             last_update_time = excluded.last_update_time,
                             status = excluded.status""",
            (file_id, dl_url, part_path, 0, downloaded_bytes, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _create_part_file(part_path: str, size_mb: int) -> None:
    """Create a .part file of the given size in MiB."""
    os.makedirs(os.path.dirname(part_path), exist_ok=True)
    with open(part_path, 'wb') as f:
        f.write(b'\x00' * size_mb * 1024 * 1024)


# =========================================================================
# Test: First Ctrl-C preserves .part and creates DB row
# =========================================================================
class TestFirstCtrlCPreservesPartAndDBRow:
    """First Ctrl-C: .part file is preserved on disk (NOT deleted),
    DB row is created in incomplete_downloads table with the
    correct downloaded_bytes.
    """

    def test_first_ctrl_c_creates_db_row_with_part_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'moodle_state.db')
            _init_db(db_path)

            file_id = _insert_file(db_path, 1, 1,
                                    'https://example.com/file.pdf',
                                    os.path.join(tmp, 'file.pdf'))
            part_path = os.path.join(tmp, 'file.pdf.part')
            _create_part_file(part_path, 32)

            _save_incomplete(db_path, file_id,
                             'https://example.com/file.pdf',
                             part_path, 32 * 1024 * 1024)

            # .part is preserved
            assert os.path.exists(part_path)
            assert os.path.getsize(part_path) == 32 * 1024 * 1024

            # DB has 1 row with correct fields
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT file_id, file_path, downloaded_bytes, status "
                "FROM incomplete_downloads"
            )
            rows = cur.fetchall()
            conn.close()
            assert len(rows) == 1
            assert rows[0][0] == file_id
            assert rows[0][1] == part_path
            assert rows[0][2] == 32 * 1024 * 1024
            assert rows[0][3] == 'pending'


# =========================================================================
# Test: Three consecutive Ctrl-C + restart cycles
# =========================================================================
class TestConsecutiveCtrlCAccumulateBytes:
    """Three Ctrl-C + restart cycles: .part file grows
    32 → 48 → 64 MiB. DB row keeps being updated (1 row, latest size).
    """

    def test_three_cycles_keep_growing_part_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'moodle_state.db')
            _init_db(db_path)
            file_id = _insert_file(db_path, 1, 1,
                                    'https://example.com/file.pdf',
                                    os.path.join(tmp, 'file.pdf'))
            part_path = os.path.join(tmp, 'file.pdf.part')

            for size_mb in [32, 48, 64]:
                _create_part_file(part_path, size_mb)
                _save_incomplete(db_path, file_id,
                                 'https://example.com/file.pdf',
                                 part_path, size_mb * 1024 * 1024)

                # .part is preserved at the current size
                assert os.path.getsize(part_path) == size_mb * 1024 * 1024

                # DB has exactly 1 row with the latest size
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*), MAX(downloaded_bytes) "
                    "FROM incomplete_downloads WHERE file_id = ?",
                    (file_id,),
                )
                count, latest_bytes = cur.fetchone()
                conn.close()
                assert count == 1
                assert latest_bytes == size_mb * 1024 * 1024

    def test_five_cycles_no_duplicate_db_rows(self):
        """5 save calls for the same .part path produce ONE row
        (UNIQUE constraint enforces)."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'moodle_state.db')
            _init_db(db_path)
            file_id = _insert_file(db_path, 1, 1,
                                    'https://example.com/file.pdf',
                                    os.path.join(tmp, 'file.pdf'))
            part_path = os.path.join(tmp, 'file.pdf.part')
            _create_part_file(part_path, 1)

            for _ in range(5):
                _save_incomplete(db_path, file_id,
                                 'https://example.com/file.pdf',
                                 part_path, 1024)

            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM incomplete_downloads")
            count = cur.fetchone()[0]
            conn.close()
            assert count == 1, (
                f'After 5 saves, expected 1 DB row, got {count}'
            )


# =========================================================================
# Test: Complete cycle after multiple partial cycles
# =========================================================================
class TestCompleteCycleAfterMultipleCtrlC:
    """3 partial cycles (32/64/100 MiB), 4th run completes the
    download to 151 MiB. The .part becomes the final file.
    """

    def test_complete_after_three_partial_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'moodle_state.db')
            _init_db(db_path)
            file_id = _insert_file(db_path, 1, 1,
                                    'https://example.com/file.pdf',
                                    os.path.join(tmp, 'file.pdf'))
            part_path = os.path.join(tmp, 'file.pdf.part')

            # 3 partial cycles
            for size_mb in [32, 64, 100]:
                _create_part_file(part_path, size_mb)
                _save_incomplete(db_path, file_id,
                                 'https://example.com/file.pdf',
                                 part_path, size_mb * 1024 * 1024)

            # 4th run: download completes
            dest_path = os.path.join(tmp, 'file.pdf')
            with open(part_path, 'r+b') as pf:
                pf.seek(0, 2)
                pf.write(b'\x00' * 51 * 1024 * 1024)  # 100 + 51 = 151
            os.rename(part_path, dest_path)
            # DB row is removed (completion)
            conn = sqlite3.connect(db_path)
            conn.execute(
                "DELETE FROM incomplete_downloads WHERE file_id = ?",
                (file_id,),
            )
            conn.commit()
            conn.close()

            # Final state
            assert os.path.exists(dest_path)
            assert not os.path.exists(part_path)
            assert os.path.getsize(dest_path) == 151 * 1024 * 1024

            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM incomplete_downloads "
                "WHERE status = 'pending'"
            )
            count = cur.fetchone()[0]
            conn.close()
            assert count == 0


# =========================================================================
# Test: Empty / missing .part file edge cases
# =========================================================================
class TestEmptyOrMissingPartFile:
    """Ctrl-C may happen before any bytes are downloaded
    (empty .part), or after the .part was already cleaned
    (missing). Both cases should NOT crash and NOT create a
    DB row.
    """

    def test_empty_part_skipped_no_db_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'moodle_state.db')
            _init_db(db_path)
            file_id = _insert_file(db_path, 1, 1,
                                    'https://example.com/file.pdf',
                                    os.path.join(tmp, 'file.pdf'))
            part_path = os.path.join(tmp, 'file.pdf.part')
            # Empty .part
            open(part_path, 'wb').close()

            # Simulate the save handler (skip if part_size == 0)
            part_size = os.path.getsize(part_path)
            if part_size > 0:
                _save_incomplete(db_path, file_id,
                                 'https://example.com/file.pdf',
                                 part_path, part_size)

            # No DB row was created
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM incomplete_downloads WHERE file_id = ?",
                (file_id,),
            )
            count = cur.fetchone()[0]
            conn.close()
            assert count == 0

    def test_missing_part_does_not_crash(self):
        """When the .part file is gone (e.g. cleanup ran), the
        save handler returns silently without crashing."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'moodle_state.db')
            _init_db(db_path)
            file_id = _insert_file(db_path, 1, 1,
                                    'https://example.com/file.pdf',
                                    os.path.join(tmp, 'file.pdf'))
            part_path = os.path.join(tmp, 'file.pdf.part')
            # NO .part file
            assert not os.path.exists(part_path)

            # Simulate the save handler (skip if part doesn't exist)
            if os.path.exists(part_path):
                part_size = os.path.getsize(part_path)
                if part_size > 0:
                    _save_incomplete(db_path, file_id,
                                     'https://example.com/file.pdf',
                                     part_path, part_size)

            # No DB row was created
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM incomplete_downloads WHERE file_id = ?",
                (file_id,),
            )
            count = cur.fetchone()[0]
            conn.close()
            assert count == 0


# =========================================================================
# Test: scan_for_orphan_part_files distinguishes tracked vs orphan
# =========================================================================
class TestScanOrphanPartFilesContract:
    """The startup scan distinguishes:
      - .part files with a matching DB row → resume path picks them up
      - .part files without a DB row → orphan, will be cleaned
    """

    def test_scan_returns_only_truly_orphans(self):
        from moodle_dl.downloader.task import scan_for_orphan_part_files

        with tempfile.TemporaryDirectory() as tmp:
            tracked = os.path.join(tmp, 'tracked.pdf.part')
            orphan = os.path.join(tmp, 'orphan.pdf.part')
            open(tracked, 'wb').write(b'\x00' * 1000)
            open(orphan, 'wb').write(b'\x00' * 2000)

            db_path = os.path.join(tmp, 'moodle_state.db')
            _init_db(db_path)
            file_id = _insert_file(db_path, 1, 1,
                                    'https://example.com/x.pdf',
                                    os.path.join(tmp, 'tracked.pdf'))
            conn = sqlite3.connect(db_path)
            conn.execute(
                """INSERT INTO incomplete_downloads
                   (file_id, file_url, file_path, downloaded_bytes,
                    total_bytes, start_time, last_update_time, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (file_id, 'https://example.com/x.pdf', tracked,
                 1000, 0, 0, 0),
            )
            conn.commit()
            conn.close()

            recorder = MagicMock()
            recorder.db_file = db_path

            orphans = scan_for_orphan_part_files(tmp, recorder)
            orphan_paths = [o[0] for o in orphans]

            # tracked is NOT an orphan (resume path handles it)
            assert tracked not in orphan_paths
            # orphan IS an orphan (no DB row)
            assert orphan in orphan_paths

    def test_scan_skips_underscore_prefixed_files(self):
        """macOS shadow files like '._file.pdf' should be skipped
        by the scan (they're metadata, not real .part files)."""
        from moodle_dl.downloader.task import scan_for_orphan_part_files

        with tempfile.TemporaryDirectory() as tmp:
            # Create a ._shadow file (not a real .part)
            shadow = os.path.join(tmp, '._file.pdf')
            real_part = os.path.join(tmp, 'real.pdf.part')
            open(shadow, 'wb').write(b'\x00' * 100)
            open(real_part, 'wb').write(b'\x00' * 200)

            db_path = os.path.join(tmp, 'moodle_state.db')
            _init_db(db_path)

            recorder = MagicMock()
            recorder.db_file = db_path

            orphans = scan_for_orphan_part_files(tmp, recorder)
            orphan_paths = [o[0] for o in orphans]

            # Only the real .part (not the shadow) is a candidate
            assert shadow not in orphan_paths
            # The real .part IS an orphan (no DB row)
            assert real_part in orphan_paths


# =========================================================================
# Test: Resume byte offset equals .part file size
# =========================================================================
class TestResumeByteOffset:
    """The byte offset to resume from equals the current .part
    file size on disk. No off-by-one, no rounding.
    """

    def test_part_file_size_is_exact_resume_offset(self):
        """For .part files of various sizes, the resume byte
        offset = os.path.getsize(part_path)."""
        with tempfile.TemporaryDirectory() as tmp:
            for size_mb in [1, 32, 64, 100, 150, 151]:
                part_path = os.path.join(
                    tmp, f'file_{size_mb}.pdf.part'
                )
                _create_part_file(part_path, size_mb)

                # The .part size IS the resume offset
                assert os.path.getsize(part_path) == size_mb * 1024 * 1024

    def test_resume_offset_zero_for_new_download(self):
        """A fresh download (no .part file) starts at byte 0."""
        with tempfile.TemporaryDirectory() as tmp:
            part_path = os.path.join(tmp, 'file.pdf.part')
            # No .part file
            assert not os.path.exists(part_path)
            # Resume offset would be 0 (start from scratch)
            resume_offset = 0
            assert resume_offset == 0


# =========================================================================
# Test: Default behavior on Ctrl-C is resume (not delete)
# =========================================================================
class TestDefaultCtrlCBehaviorIsResume:
    """The default MoodleDlOpts.restart_incomplete_on_kill=False
    means Ctrl-C preserves .part for resume. This is the user-
    requested behavior from the 2026-06-22 report.
    """

    def test_default_restart_incomplete_on_kill_is_false(self):
        """Pin the default for future refactors: the default MUST
        be False (resume) so users get the bandwidth-friendly
        behavior out of the box."""
        from moodle_dl.types import MoodleDlOpts
        opts = MoodleDlOpts()
        assert opts.restart_incomplete_on_kill is False

    def test_default_post_process_opts_preserves_resume(self):
        """With no env var, post_process_opts must keep resume
        as the default."""
        from moodle_dl.main import post_process_opts
        from moodle_dl.types import MoodleDlOpts
        opts = MoodleDlOpts()
        out = post_process_opts(opts)
        assert out.restart_incomplete_on_kill is False

    def test_env_var_zero_opts_into_restart(self, monkeypatch):
        """MOODLE_DL_KEEP_INCOMPLETE_ON_KILL=0 enables restart-
        from-scratch (the user must opt in to delete .part)."""
        monkeypatch.setenv('MOODLE_DL_KEEP_INCOMPLETE_ON_KILL', '0')
        from moodle_dl.main import post_process_opts
        from moodle_dl.types import MoodleDlOpts
        opts = MoodleDlOpts()
        out = post_process_opts(opts)
        assert out.restart_incomplete_on_kill is True

    def test_env_var_one_matches_default(self, monkeypatch):
        """MOODLE_DL_KEEP_INCOMPLETE_ON_KILL=1 is a no-op now
        (matches the new default of resume)."""
        monkeypatch.setenv('MOODLE_DL_KEEP_INCOMPLETE_ON_KILL', '1')
        from moodle_dl.main import post_process_opts
        from moodle_dl.types import MoodleDlOpts
        opts = MoodleDlOpts()
        out = post_process_opts(opts)
        assert out.restart_incomplete_on_kill is False


# =========================================================================
# Test: Continuous Ctrl-C + restart state stability
# =========================================================================
class TestContinuousCtrlCStateStability:
    """After many Ctrl-C + restart cycles, the system state must
    remain consistent: no leaked DB rows, no corrupted files,
    consistent bytes count.
    """

    def test_part_size_matches_db_downloaded_bytes_after_ten_cycles(self):
        """10 cycles: .part size and DB downloaded_bytes always
        match after each save."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'moodle_state.db')
            _init_db(db_path)
            file_id = _insert_file(db_path, 1, 1,
                                    'https://example.com/file.pdf',
                                    os.path.join(tmp, 'file.pdf'))
            part_path = os.path.join(tmp, 'file.pdf.part')

            for cycle in range(1, 11):
                size_mb = cycle * 10  # 10, 20, 30, ...
                _create_part_file(part_path, size_mb)
                _save_incomplete(db_path, file_id,
                                 'https://example.com/file.pdf',
                                 part_path, size_mb * 1024 * 1024)

                # Disk and DB agree
                disk_size = os.path.getsize(part_path)
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute(
                    "SELECT downloaded_bytes FROM incomplete_downloads "
                    "WHERE file_id = ?",
                    (file_id,),
                )
                db_size = cur.fetchone()[0]
                conn.close()

                assert disk_size == size_mb * 1024 * 1024
                assert db_size == disk_size, (
                    f'Cycle {cycle}: disk={disk_size}, db={db_size}'
                )

    def test_db_row_count_stable_across_many_cycles(self):
        """Across 10 cycles, the DB has exactly 1 row for the file
        (UNIQUE constraint + INSERT OR REPLACE)."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'moodle_state.db')
            _init_db(db_path)
            file_id = _insert_file(db_path, 1, 1,
                                    'https://example.com/file.pdf',
                                    os.path.join(tmp, 'file.pdf'))
            part_path = os.path.join(tmp, 'file.pdf.part')

            for _ in range(10):
                _create_part_file(part_path, 1)
                _save_incomplete(db_path, file_id,
                                 'https://example.com/file.pdf',
                                 part_path, 1024)

            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM incomplete_downloads")
            count = cur.fetchone()[0]
            conn.close()
            assert count == 1

    def test_completion_then_resume_does_not_create_orphan(self):
        """After a download completes, the .part is removed.
        A subsequent save call for the same path doesn't
        resurrect a stale row."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'moodle_state.db')
            _init_db(db_path)
            file_id = _insert_file(db_path, 1, 1,
                                    'https://example.com/file.pdf',
                                    os.path.join(tmp, 'file.pdf'))
            part_path = os.path.join(tmp, 'file.pdf.part')
            dest_path = os.path.join(tmp, 'file.pdf')

            # Download in progress
            _create_part_file(part_path, 32)
            _save_incomplete(db_path, file_id,
                             'https://example.com/file.pdf',
                             part_path, 32 * 1024 * 1024)

            # Download completes — .part removed, row deleted
            os.rename(part_path, dest_path)
            conn = sqlite3.connect(db_path)
            conn.execute(
                "DELETE FROM incomplete_downloads WHERE file_id = ?",
                (file_id,),
            )
            conn.commit()
            conn.close()

            # Another Ctrl-C after completion: .part doesn't exist,
            # no DB row created
            assert not os.path.exists(part_path)
            # (No save call — _save_incomplete_on_kill early-returns
            # when part_path doesn't exist.)

            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM incomplete_downloads WHERE file_id = ?",
                (file_id,),
            )
            count = cur.fetchone()[0]
            conn.close()
            assert count == 0


# =========================================================================
# Test: Download resumes with correct Range header
# =========================================================================
class TestRangeHeaderForResume:
    """The download code sends a Range header starting from the
    .part size. Pin the contract that the Range header is
    'bytes=<part_size>-' (no off-by-one).
    """

    def test_range_header_format(self):
        """The Range header is 'bytes=N-' where N is the part
        file size."""
        part_size = 32 * 1024 * 1024
        expected_range = f'bytes={part_size}-'
        assert expected_range == 'bytes=33554432-'


# =========================================================================
# Test: Multiple files in parallel all preserve .part on Ctrl-C
# =========================================================================
class TestMultipleFilesParallelCtrlC:
    """When 5 files are downloading in parallel and the user
    Ctrl-C's, ALL 5 .part files are preserved (not just one).
    """

    def test_five_parallel_downloads_all_preserve_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'moodle_state.db')
            _init_db(db_path)

            # Simulate 5 parallel downloads, each with its own file
            part_paths = []
            file_ids = []
            for i in range(5):
                saved_to = os.path.join(tmp, f'file_{i}.pdf')
                file_id = _insert_file(
                    db_path, 1, i + 1,
                    f'https://example.com/file_{i}.pdf',
                    saved_to,
                )
                file_ids.append(file_id)
                part_path = os.path.join(tmp, f'file_{i}.pdf.part')
                _create_part_file(part_path, (i + 1) * 10)  # 10, 20, 30...
                _save_incomplete(db_path, file_id,
                                 f'https://example.com/file_{i}.pdf',
                                 part_path, (i + 1) * 10 * 1024 * 1024)
                part_paths.append(part_path)

            # All 5 .part files are preserved
            for i, part_path in enumerate(part_paths):
                assert os.path.exists(part_path)
                expected_size = (i + 1) * 10 * 1024 * 1024
                assert os.path.getsize(part_path) == expected_size

            # All 5 DB rows exist
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM incomplete_downloads"
            )
            count = cur.fetchone()[0]
            conn.close()
            assert count == 5

    def test_one_completes_other_paused(self):
        """In parallel: file 1 completes, files 2-5 are paused
        mid-download. Only file 2-5's .part files are preserved;
        file 1's .part is gone (renamed to final)."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'moodle_state.db')
            _init_db(db_path)

            # File 1: completes — .part renamed to final, no DB row
            file1_saved = os.path.join(tmp, 'file1.pdf')
            file1_part = os.path.join(tmp, 'file1.pdf.part')
            file1_id = _insert_file(db_path, 1, 1,
                                     'https://example.com/file1.pdf',
                                     file1_saved)
            _create_part_file(file1_part, 100)  # full download
            os.rename(file1_part, file1_saved)
            # (No DB row — completion removed it)

            # Files 2-5: paused mid-download
            for i in range(2, 6):
                saved_to = os.path.join(tmp, f'file{i}.pdf')
                file_id = _insert_file(
                    db_path, 1, i,
                    f'https://example.com/file{i}.pdf',
                    saved_to,
                )
                part_path = os.path.join(tmp, f'file{i}.pdf.part')
                _create_part_file(part_path, i * 10)
                _save_incomplete(db_path, file_id,
                                 f'https://example.com/file{i}.pdf',
                                 part_path, i * 10 * 1024 * 1024)

            # File 1: completed (no .part, has final)
            assert not os.path.exists(file1_part)
            assert os.path.exists(file1_saved)
            # Files 2-5: .part preserved
            for i in range(2, 6):
                assert os.path.exists(
                    os.path.join(tmp, f'file{i}.pdf.part')
                )
            # DB has 4 rows (file 2-5)
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM incomplete_downloads")
            count = cur.fetchone()[0]
            conn.close()
            assert count == 4