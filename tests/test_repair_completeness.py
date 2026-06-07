# -*- coding: utf-8 -*-
"""
Tests for the 'single-run completeness' contract of repair_paths.

The user observed that after a single repair_paths run, the
DB still flags hundreds of files as 'buggy', even though
running the tool a second time produces 0 moves. The user's
definition of acceptable: a single run should either:
  (a) move the file to its correct location and update the DB,
      OR
  (b) definitively mark the file as unfixable (e.g. 404 — the
      file does not exist on disk anywhere).

Cases that should NOT remain 'buggy' after one run:
  1. File exists on disk in the module dir, DB saved_to is
     wrong (just a DB sync issue, not a disk move).
  2. File is at a non-module-dir location (e.g. section root)
     and can be moved.
  3. File has a different content_filename extension than
     what's on disk (e.g. .html vs .html.md) — should be
     fuzzy-matched.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, '/Users/linqilan/CodingProjects/moodle/Moodle-DL')

from moodle_dl.downloader.task_path_repair import find_buggy_files


def _create_files_db(td):
    """Create a temporary DB and return (db_path, conn)."""
    db_path = os.path.join(td, 'moodle_state.db')
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE files (
          file_id INTEGER PRIMARY KEY,
          course_id INTEGER,
          course_fullname TEXT,
          section_name TEXT,
          section_id INTEGER,
          module_id INTEGER,
          module_name TEXT,
          module_modname TEXT,
          content_filepath TEXT,
          content_filename TEXT,
          download_status TEXT,
          saved_to TEXT
        )
    """)
    conn.commit()
    return db_path, conn


class TestFindBuggyFilesCompleteness(unittest.TestCase):
    """Pin the contract that a single repair_paths run leaves
    no false positives: every file flagged as 'buggy' after
    a run must be either unfixable (404) or genuinely at a
    non-module-dir location that needs another tool's
    intervention."""

    def test_no_false_positive_for_case_mismatch_dir(self):
        """Module name is 'INTRODUCTION TO PART 2' (uppercase)
        but the directory is 'Introduction to Part 2'
        (titlecase). The file is correctly placed in the
        dir but case-sensitive matching flags it as buggy."""
        with tempfile.TemporaryDirectory() as td:
            db_path, conn = _create_files_db(td)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO files VALUES
                (1, 1, 'Course', 'Section', 1, 1,
                 'INTRODUCTION TO PART 2', 'resource', '/',
                 'INTRODUCTION TO PART 2.md', 'success',
                 '/Course/Section/Introduction to Part 2/*01* INTRODUCTION TO PART 2.md')
            """)
            conn.commit()
            conn.close()
            conn = sqlite3.connect(db_path)
            buggy = find_buggy_files(conn)
            conn.close()
            self.assertEqual(
                len(buggy), 0,
                f'file in case-mismatched dir should NOT be buggy: {buggy}',
            )

    def test_no_false_positive_for_fuzzy_extension_match(self):
        """content_filename is 'foo.html' but disk has
        'foo.html.md' (moodle-dl quirk for description files).
        find_buggy_files should not flag this as buggy if
        the disk file with the matching stripped extension
        exists in the module dir.
        Currently this test demonstrates the missing
        feature: find_buggy_files looks at saved_to
        literally, so the .html.md basename appears as a
        substring match of .html (or not at all), causing
        the file to be flagged as buggy.
        The fix is to look at the disk, not the saved_to,
        for the actual on-disk filename.
        """
        with tempfile.TemporaryDirectory() as td:
            db_path, conn = _create_files_db(td)
            # Workspace layout:
            #   td/Course/Section/Mod/foo.html.md
            # saved_to says:
            #   td/Course/Section/*01* foo.html (the buggy
            #   pre-repair state).
            course_dir = os.path.join(td, 'Course', 'Section', 'Mod')
            os.makedirs(course_dir)
            with open(os.path.join(course_dir, 'foo.html.md'), 'w') as f:
                f.write('content')
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO files VALUES
                (1, 1, 'Course', 'Section', 1, 1,
                 'Mod', 'resource', '/', 'foo.html', 'success',
                 ?)
            """, (os.path.join(td, 'Course', 'Section', '*01* foo.html'),))
            conn.commit()
            conn.close()
            conn = sqlite3.connect(db_path)
            # workspace = td (the real disk root)
            buggy = find_buggy_files(conn, workspace=td)
            conn.close()
            self.assertEqual(
                len(buggy), 0,
                f'file in module dir (even with extension '
                f'variation) should NOT be buggy: {buggy}',
            )


if __name__ == '__main__':
    unittest.main()
