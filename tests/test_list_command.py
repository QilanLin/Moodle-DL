# -*- coding: utf-8 -*-
"""
Tests for the moodle-dl --list command and its natural-sort helper.

These tests verify:

  * natural_sort_key puts "Week 10" after "Week 9" (NOT after "Week 1")
  * extract_numeric_prefix pulls *NN* out of *05* foo.webloc
  * is_macos_shadow correctly filters ._* files
  * collect_db_files handles missing / corrupt DB
  * collect_fs_files hides ._* and walks subdirectories
  * find_mismatches catches DB↔FS inconsistencies
  * print_workspace_listing produces a sorted, filtered report
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Helpers (unit tests)
# =========================================================================
class TestNaturalSortKey:
    """The natural-sort comparator must put Week 10 after Week 9."""

    def test_week_natural_sort(self):
        from moodle_dl.cli.list_files import natural_sort_key
        items = [
            'Week 11',
            'Week 1',
            'Week 10',
            'Week 2',
            'Week 3',
        ]
        items.sort(key=natural_sort_key)
        assert items == [
            'Week 1', 'Week 2', 'Week 3', 'Week 10', 'Week 11',
        ]

    def test_pure_alphabetical_fails(self):
        """The bug we're fixing: ``sorted()`` is alphabetical,
        which puts Week 10 between Week 1 and Week 2.
        """
        items = ['Week 1', 'Week 10', 'Week 2', 'Week 3', 'Week 4']
        # Naive sort (alphabetical) — wrong
        assert sorted(items) == ['Week 1', 'Week 10', 'Week 2', 'Week 3', 'Week 4']

    def test_natural_sort_with_more_digits(self):
        from moodle_dl.cli.list_files import natural_sort_key
        items = ['Module 100', 'Module 2', 'Module 1', 'Module 20']
        items.sort(key=natural_sort_key)
        assert items == ['Module 1', 'Module 2', 'Module 20', 'Module 100']

    def test_natural_sort_case_insensitive(self):
        from moodle_dl.cli.list_files import natural_sort_key
        items = ['apple', 'Banana', 'cherry']
        items.sort(key=natural_sort_key)
        assert items == ['apple', 'Banana', 'cherry']


class TestExtractNumericPrefix:
    """Extracts *NN* from a moodle-dl filename."""

    def test_extract_05(self):
        from moodle_dl.cli.list_files import extract_numeric_prefix
        assert extract_numeric_prefix('*05* foo.webloc') == 5

    def test_extract_two_digit(self):
        from moodle_dl.cli.list_files import extract_numeric_prefix
        assert extract_numeric_prefix('*10* some_file.pdf') == 10

    def test_no_prefix(self):
        from moodle_dl.cli.list_files import extract_numeric_prefix
        assert extract_numeric_prefix('no_prefix.pdf') == 9999

    def test_empty_string(self):
        from moodle_dl.cli.list_files import extract_numeric_prefix
        assert extract_numeric_prefix('') == 9999

    def test_does_not_match_partial(self):
        """'*foo* bar' should NOT match (foo is not numeric)."""
        from moodle_dl.cli.list_files import extract_numeric_prefix
        assert extract_numeric_prefix('*foo* bar') == 9999


class TestIsMacosShadow:
    """Filters out ._* files (macOS AppleDouble)."""

    def test_shadow_file(self):
        from moodle_dl.cli.list_files import is_macos_shadow
        assert is_macos_shadow('._*05* foo.webloc') is True

    def test_real_file(self):
        from moodle_dl.cli.list_files import is_macos_shadow
        assert is_macos_shadow('*05* foo.webloc') is False

    def test_empty(self):
        from moodle_dl.cli.list_files import is_macos_shadow
        assert is_macos_shadow('') is False

    def test_only_underscore_prefix(self):
        from moodle_dl.cli.list_files import is_macos_shadow
        assert is_macos_shadow('._foo') is True


# =========================================================================
# DB / FS collectors (unit + integration)
# =========================================================================
class TestCollectDbFiles:
    """Reads the SQLite database and groups by course."""

    def test_missing_db_returns_empty(self, tmp_path):
        from moodle_dl.cli.list_files import collect_db_files
        result = collect_db_files(str(tmp_path / 'nonexistent.db'))
        assert result == {}

    def test_corrupt_db_returns_empty(self, tmp_path):
        from moodle_dl.cli.list_files import collect_db_files
        # Truncated file (not a valid SQLite)
        db = tmp_path / 'bad.db'
        db.write_bytes(b'not a database' * 100)
        result = collect_db_files(str(db))
        assert result == {}

    def test_real_db(self, tmp_path):
        """Pin the contract: collect_db_files reads file_id,
        course_fullname, saved_to and groups by course."""
        from moodle_dl.cli.list_files import collect_db_files
        # Build a real SQLite DB with the expected schema
        db = tmp_path / 'state.db'
        conn = sqlite3.connect(str(db))
        conn.execute(
            'CREATE TABLE files ('
            'file_id INTEGER, course_fullname TEXT, saved_to TEXT)'
        )
        conn.execute(
            'INSERT INTO files VALUES (1, "Course A", "/path/to/a.pdf")'
        )
        conn.execute(
            'INSERT INTO files VALUES (2, "Course A", "/path/to/b.pdf")'
        )
        conn.execute(
            'INSERT INTO files VALUES (3, "Course B", "/other/c.pdf")'
        )
        conn.commit()
        conn.close()
        result = collect_db_files(str(db))
        assert set(result.keys()) == {'Course A', 'Course B'}
        assert result['Course A'][1] == '/path/to/a.pdf'
        assert result['Course A'][2] == '/path/to/b.pdf'
        assert result['Course B'][3] == '/other/c.pdf'


class TestCollectFsFiles:
    """Walks the workspace, filters ._* files, groups by directory."""

    def test_filters_macos_shadow(self, tmp_path):
        from moodle_dl.cli.list_files import collect_fs_files
        (tmp_path / 'section' / 'sub').mkdir(parents=True)
        (tmp_path / 'section' / '*01* foo.webloc').touch()
        (tmp_path / 'section' / '._*01* foo.webloc').touch()  # shadow
        (tmp_path / 'section' / 'sub' / 'bar.pdf').touch()
        (tmp_path / 'section' / 'sub' / '._bar.pdf').touch()  # shadow
        result = collect_fs_files(str(tmp_path))
        # Both real files present
        all_files = [f for files in result.values() for f in files]
        assert '*01* foo.webloc' in all_files
        assert 'bar.pdf' in all_files
        # No shadow files
        assert not any(is_macos_shadow_helper(f) for f in all_files)

    def test_empty_workspace(self, tmp_path):
        from moodle_dl.cli.list_files import collect_fs_files
        result = collect_fs_files(str(tmp_path))
        assert result == {}

    def test_nonexistent_workspace(self, tmp_path):
        from moodle_dl.cli.list_files import collect_fs_files
        result = collect_fs_files(str(tmp_path / 'doesnotexist'))
        assert result == {}


def is_macos_shadow_helper(name: str) -> bool:
    from moodle_dl.cli.list_files import is_macos_shadow
    return is_macos_shadow(name)


class TestFindMismatches:
    """Cross-references DB against FS."""

    def test_all_consistent(self, tmp_path):
        from moodle_dl.cli.list_files import collect_db_files, find_mismatches
        # Create a real file
        real_path = tmp_path / 'real.txt'
        real_path.write_text('hello')
        # DB has it
        db = tmp_path / 'state.db'
        conn = sqlite3.connect(str(db))
        conn.execute(
            'CREATE TABLE files ('
            'file_id INTEGER, course_fullname TEXT, saved_to TEXT)'
        )
        conn.execute(
            'INSERT INTO files VALUES (1, "C", ?)',
            (str(real_path),),
        )
        conn.commit()
        conn.close()
        db_files = collect_db_files(str(db))
        missing, orphans = find_mismatches(db_files, str(tmp_path))
        assert missing == []
        assert orphans == []

    def test_missing_on_disk(self, tmp_path):
        from moodle_dl.cli.list_files import collect_db_files, find_mismatches
        # DB says file exists, but FS doesn't
        db = tmp_path / 'state.db'
        conn = sqlite3.connect(str(db))
        conn.execute(
            'CREATE TABLE files ('
            'file_id INTEGER, course_fullname TEXT, saved_to TEXT)'
        )
        conn.execute(
            'INSERT INTO files VALUES (1, "C", "/never/existed.txt")'
        )
        conn.commit()
        conn.close()
        db_files = collect_db_files(str(db))
        missing, _ = find_mismatches(db_files, str(tmp_path))
        assert len(missing) == 1
        assert '/never/existed.txt' in missing[0]

    def test_orphan_part_files_detected(self, tmp_path):
        from moodle_dl.cli.list_files import find_mismatches
        # No DB, just orphan .part files
        (tmp_path / 'sub').mkdir()
        (tmp_path / 'sub' / 'file.pdf.part').touch()
        (tmp_path / 'sub' / 'other.pdf').touch()  # not .part
        missing, orphans = find_mismatches({}, str(tmp_path))
        assert len(orphans) == 1
        assert 'file.pdf.part' in orphans[0]
        # The .pdf is NOT an orphan (just a regular file)
        assert not any('other.pdf' in o for o in orphans)


# =========================================================================
# End-to-end: print_workspace_listing
# =========================================================================
class TestPrintWorkspaceListing:
    """End-to-end: build a real workspace + DB, run --list, verify output."""

    def test_e2e_natural_sort_printed(self, capsys, tmp_path):
        """The output should show Week 1, Week 2, ..., Week 10
        (natural order), not Week 1, Week 10, Week 2 (alphabetical).
        """
        from moodle_dl.cli.list_files import print_workspace_listing
        from moodle_dl.config import ConfigHelper
        from moodle_dl.types import MoodleDlOpts

        # Build a course with multi-digit weeks. The course
        # directory must be at the top of opts.path (so the
        # listing finds it).
        course_path = tmp_path / 'My Course'
        (course_path / 'Week 10').mkdir(parents=True)
        (course_path / 'Week 10' / 'foo.pdf').touch()
        (course_path / 'Week 1').mkdir(parents=True)
        (course_path / 'Week 1' / 'bar.pdf').touch()
        (course_path / 'Week 2').mkdir(parents=True)
        (course_path / 'Week 2' / 'baz.pdf').touch()

        opts = MoodleDlOpts()
        opts.path = str(tmp_path)
        # Need to bypass ConfigHelper's init flow
        with patch('moodle_dl.config.ConfigHelper.__init__', return_value=None):
            config = ConfigHelper(opts)
        config.get_misc_files_path = MagicMock(return_value=str(tmp_path))

        print_workspace_listing(config, opts)

        captured = capsys.readouterr().out

        # Find the positions of Week 1, Week 2, Week 10
        pos_1 = captured.find('Week 1\n')
        pos_2 = captured.find('Week 2\n')
        pos_10 = captured.find('Week 10\n')

        # The natural sort order must be 1 < 2 < 10
        assert pos_1 > 0, 'Week 1 should appear in output'
        assert pos_2 > pos_1, (
            f'Week 2 should appear AFTER Week 1, but '
            f'pos_1={pos_1}, pos_2={pos_2}'
        )
        assert pos_10 > pos_2, (
            f'Week 10 should appear AFTER Week 2 (natural sort), but '
            f'pos_2={pos_2}, pos_10={pos_10}. This is the bug we '
            f'are fixing.'
        )

    def test_e2e_filters_macos_shadow(self, capsys, tmp_path):
        from moodle_dl.cli.list_files import print_workspace_listing
        from moodle_dl.config import ConfigHelper
        from moodle_dl.types import MoodleDlOpts

        # Build a section with a ._ shadow file
        section_path = tmp_path / 'C' / 'S'
        section_path.mkdir(parents=True)
        (section_path / 'real.webloc').touch()
        (section_path / '._real.webloc').touch()  # macOS shadow

        opts = MoodleDlOpts()
        opts.path = str(tmp_path)
        with patch('moodle_dl.config.ConfigHelper.__init__', return_value=None):
            config = ConfigHelper(opts)
        config.get_misc_files_path = MagicMock(return_value=str(tmp_path))

        print_workspace_listing(config, opts)

        captured = capsys.readouterr().out
        # The real file should be listed
        assert 'real.webloc' in captured
        # The shadow file should NOT be listed in the file section
        # (we still report the count in stats, so it appears once)
        shadow_count = captured.count('._real.webloc')
        # Allowed: 0 (filtered) or 1 (in stats line)
        # The test for "not in files" passes if we don't have a line like
        # "  ._real.webloc" with leading whitespace
        for line in captured.split('\n'):
            if line.lstrip().startswith('._real.webloc'):
                # This is a "files in this section" line — bug
                if 'files' not in line.lower() and 'shadow' not in line.lower():
                    pytest.fail(
                        f'macOS shadow file appeared in file listing: {line!r}'
                    )

    def test_e2e_reports_missing_files(self, capsys, tmp_path):
        from moodle_dl.cli.list_files import print_workspace_listing
        from moodle_dl.config import ConfigHelper
        from moodle_dl.types import MoodleDlOpts

        # Build a course + section with a real file, plus a DB
        # entry pointing to a non-existent file
        course_path = tmp_path / 'C' / 'S'
        course_path.mkdir(parents=True)
        (course_path / 'real.txt').write_text('hi')

        db = tmp_path / 'moodle_state.db'
        conn = sqlite3.connect(str(db))
        conn.execute(
            'CREATE TABLE files ('
            'file_id INTEGER, course_fullname TEXT, saved_to TEXT)'
        )
        # Real file (exists)
        conn.execute(
            'INSERT INTO files VALUES (1, "C", ?)',
            (str(course_path / 'real.txt'),),
        )
        # Missing file (in DB but not on disk)
        conn.execute(
            'INSERT INTO files VALUES (2, "C", "/never/exists.txt")'
        )
        conn.commit()
        conn.close()

        opts = MoodleDlOpts()
        opts.path = str(tmp_path)
        with patch('moodle_dl.config.ConfigHelper.__init__', return_value=None):
            config = ConfigHelper(opts)
        config.get_misc_files_path = MagicMock(return_value=str(tmp_path))

        print_workspace_listing(config, opts)
        captured = capsys.readouterr().out
        # Should report 1 missing file
        assert 'NOT on disk' in captured
        assert '/never/exists.txt' in captured

    def test_e2e_workspace_missing(self, capsys, tmp_path):
        from moodle_dl.cli.list_files import print_workspace_listing
        from moodle_dl.config import ConfigHelper
        from moodle_dl.types import MoodleDlOpts

        opts = MoodleDlOpts()
        opts.path = str(tmp_path / 'doesnotexist')
        with patch('moodle_dl.config.ConfigHelper.__init__', return_value=None):
            config = ConfigHelper(opts)
        config.get_misc_files_path = MagicMock(return_value=str(tmp_path))

        print_workspace_listing(config, opts)
        captured = capsys.readouterr().out
        assert 'does not exist' in captured

    def test_e2e_real_cs3_workspace(self, capsys):
        """End-to-end smoke test: run --list against the real
        /Volumes/Untitled/CS3 workspace and verify it doesn't crash.
        """
        from moodle_dl.cli.list_files import print_workspace_listing
        from moodle_dl.config import ConfigHelper
        from moodle_dl.types import MoodleDlOpts

        workspace = '/Volumes/Untitled/CS3'
        if not os.path.isdir(workspace):
            pytest.skip(f'Real workspace not available: {workspace}')

        opts = MoodleDlOpts()
        opts.path = workspace
        with patch('moodle_dl.config.ConfigHelper.__init__', return_value=None):
            config = ConfigHelper(opts)
        config.get_misc_files_path = MagicMock(return_value=workspace)

        # Should not raise
        print_workspace_listing(config, opts)
        captured = capsys.readouterr().out
        # Should show the course
        assert '6CCS3ML1' in captured
        # Should show module overview
        assert 'Module Overview' in captured
        # Should report macOS shadow count
        assert 'macOS ._ shadow files' in captured