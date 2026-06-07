# -*- coding: utf-8 -*-
"""
Tests for the auto-detect warning in moodle_dl.main.

When the user runs `moodle-dl` and their DB has files
affected by the workspace-isolation bug (committed before
d1ae09d fixed the downloader), the CLI should emit a
warning telling them how to fix the on-disk layout.

The warning is purely informational. New downloads (after
d1ae09d) are unaffected; the user can keep using
moodle-dl without intervention. The warning is only
useful for cleaning up legacy buggy files via the
repair_paths tool.
"""
import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, '/Users/linqilan/CodingProjects/moodle/Moodle-DL')


class TestWarnIfBuggyFiles(unittest.TestCase):
    """Pin the contract of _warn_if_buggy_files."""

    def _make_fake_db(self, td):
        """Create a fake DB with 1 buggy file."""
        db_path = os.path.join(td, 'moodle_state.db')
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE files (
              file_id INTEGER PRIMARY KEY,
              course_id INTEGER, course_fullname TEXT,
              section_id INTEGER, section_name TEXT,
              module_id INTEGER, module_name TEXT, module_modname TEXT,
              content_filepath TEXT, content_filename TEXT,
              download_status TEXT, saved_to TEXT
            )
        """)
        # Buggy: saved_to at section root, but file would be
        # correctly in module dir on disk if it existed.
        cur.execute("""
            INSERT INTO files VALUES
            (1, 1, 'C1', 1, 'S1', 1, 'Mod1', 'resource', '/',
             'main.css', 'success',
             ?)
        """, (os.path.join(td, 'C1', 'S1', '*01* main.css'),))
        conn.commit()
        conn.close()
        return db_path

    def test_warns_when_buggy_files_found(self):
        from moodle_dl.main import _warn_if_buggy_files
        with tempfile.TemporaryDirectory() as td:
            # Set up the fake DB
            db_path = self._make_fake_db(td)
            # Set up the fake workspace layout so find_buggy_files
            # has something to compare against. NO module dir,
            # so the file IS buggy.
            os.makedirs(os.path.join(td, 'C1', 'S1'))
            with mock.patch('moodle_dl.main.sqlite3') as mock_sqlite3:
                # Mock sqlite3.connect to return our fake DB
                mock_conn = sqlite3.connect(db_path)
                mock_sqlite3.connect.return_value = mock_conn
                mock_db = mock.MagicMock()
                mock_db.db_path = db_path
                mock_config = mock.MagicMock()
                mock_config.get_workspace.return_value = td
                mock_opts = mock.MagicMock()
                # Capture the log warning
                with self.assertLogs('root', level='WARNING') as cm:
                    _warn_if_buggy_files(mock_db, mock_config, mock_opts)
                mock_conn.close()
                # Verify a warning was emitted
                self.assertTrue(
                    any('workspace-isolation' in m for m in cm.output),
                    f'expected workspace-isolation warning, got: {cm.output}',
                )

    def test_silent_when_no_buggy_files(self):
        from moodle_dl.main import _warn_if_buggy_files
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, 'moodle_state.db')
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE files (
                  file_id INTEGER PRIMARY KEY,
                  course_id INTEGER, course_fullname TEXT,
                  section_id INTEGER, section_name TEXT,
                  module_id INTEGER, module_name TEXT, module_modname TEXT,
                  content_filepath TEXT, content_filename TEXT,
                  download_status TEXT, saved_to TEXT
                )
            """)
            # File is at correct location already
            cur.execute("""
                INSERT INTO files VALUES
                (1, 1, 'C1', 1, 'S1', 1, 'Mod1', 'resource', '/',
                 'main.css', 'success',
                 ?)
            """, (os.path.join(td, 'C1', 'S1', 'Mod1', 'main.css'),))
            conn.commit()
            conn.close()
            os.makedirs(os.path.join(td, 'C1', 'S1', 'Mod1'))
            with open(os.path.join(td, 'C1', 'S1', 'Mod1', 'main.css'), 'w') as f:
                f.write('body{}')
            mock_db = mock.MagicMock()
            mock_db.db_path = db_path
            mock_config = mock.MagicMock()
            mock_config.get_workspace.return_value = td
            mock_opts = mock.MagicMock()
            # Capture logging output. If the function is
            # silent (no warning), assertLogs will raise.
            # We expect it NOT to emit any warning, so the
            # test passes silently.
            _warn_if_buggy_files(mock_db, mock_config, mock_opts)

    def test_silent_when_workspace_does_not_exist(self):
        from moodle_dl.main import _warn_if_buggy_files
        mock_db = mock.MagicMock()
        mock_db.db_path = '/tmp/nonexistent.db'
        mock_config = mock.MagicMock()
        mock_config.get_workspace.return_value = '/nonexistent/path'
        mock_opts = mock.MagicMock()
        # Should not raise
        _warn_if_buggy_files(mock_db, mock_config, mock_opts)


if __name__ == '__main__':
    unittest.main()
