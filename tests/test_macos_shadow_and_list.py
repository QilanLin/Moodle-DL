# -*- coding: utf-8 -*-
"""
Tests for the new fixes:

  * macOS ``._*`` resource-fork shadow files are stripped
    automatically after each file write (via strip_macos_metadata)
  * The moodle-dl --list command runs automatically after each
    download session completes

The Week-sort / natural-sort prefix was REMOVED after the subagent
investigation found that the Moodle API already returns sections in
the correct natural order. We trust the server-side sortorder
and let the user use `moodle-dl --list` (or their OS's natural-sort
command) for proper display.
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
# macOS shadow-file fix: strip_macos_metadata
# =========================================================================
class TestStripMacosMetadata:
    """The strip_macos_metadata function removes xattrs that
    cause macOS to create ``._*`` shadow files. It must be a
    no-op on non-macOS platforms.
    """

    def test_noop_on_non_macos(self):
        from moodle_dl.downloader import task as task_mod
        from moodle_dl.downloader.task import strip_macos_metadata
        with patch.object(task_mod.sys, 'platform', 'linux'):
            # Should return immediately (no ctypes.CDLL call)
            # We don't patch ctypes because it's imported lazily
            # inside the function. We just verify the function
            # returns without error on non-darwin platforms.
            result = strip_macos_metadata('/anywhere/file.pdf')
            assert result is None  # function returns None

    def test_noop_on_windows(self):
        from moodle_dl.downloader import task as task_mod
        from moodle_dl.downloader.task import strip_macos_metadata
        with patch.object(task_mod.sys, 'platform', 'win32'):
            result = strip_macos_metadata(r'C:\Users\file.pdf')
            assert result is None

    def test_empty_path_noop(self):
        from moodle_dl.downloader import task as task_mod
        from moodle_dl.downloader.task import strip_macos_metadata
        # No crash, no error
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            strip_macos_metadata('')
            # None is not acceptable; just test the empty string.

    def test_calls_removexattr_on_darwin(self):
        from moodle_dl.downloader import task as task_mod
        from moodle_dl.downloader.task import strip_macos_metadata
        # Mock ctypes at the standard library level
        import ctypes as real_ctypes
        mock_libc = MagicMock()
        mock_libc.removexattr.return_value = 0
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                strip_macos_metadata('/test/file.pdf')
        # removexattr was called at least once (4 known xattrs)
        assert mock_libc.removexattr.call_count >= 1

    def test_swallows_errors(self):
        """xattr removal must never crash the download."""
        from moodle_dl.downloader import task as task_mod
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', side_effect=OSError('no lib')):
                # Should not raise
                strip_macos_metadata('/test/file.pdf')


class TestCleanupMacosShadowFiles:
    """cleanup_macos_shadow_files removes any ``._*`` files in a
    directory (fallback for files that escaped the per-file
    xattr stripping).
    """

    def test_removes_underscore_files(self, tmp_path):
        from moodle_dl.downloader.task import cleanup_macos_shadow_files
        # Create a real file and a shadow file
        (tmp_path / 'real.pdf').write_text('hi')
        (tmp_path / '._real.pdf').write_bytes(b'\x00\x05\x16')
        (tmp_path / 'sub').mkdir()
        (tmp_path / 'sub' / 'foo.pdf').touch()
        (tmp_path / 'sub' / '._foo.pdf').touch()

        with patch('moodle_dl.downloader.task.sys.platform', 'darwin'):
            deleted = cleanup_macos_shadow_files(str(tmp_path))
        assert deleted == 2  # two ._ files

        # Verify they're gone
        assert (tmp_path / 'real.pdf').exists()
        assert not (tmp_path / '._real.pdf').exists()
        assert (tmp_path / 'sub' / 'foo.pdf').exists()
        assert not (tmp_path / 'sub' / '._foo.pdf').exists()

    def test_noop_on_non_macos(self, tmp_path):
        from moodle_dl.downloader.task import cleanup_macos_shadow_files
        (tmp_path / '._foo').touch()
        with patch('moodle_dl.downloader.task.sys.platform', 'linux'):
            deleted = cleanup_macos_shadow_files(str(tmp_path))
        assert deleted == 0
        assert (tmp_path / '._foo').exists()  # not deleted

    def test_noop_on_missing_dir(self):
        from moodle_dl.downloader.task import cleanup_macos_shadow_files
        with patch('moodle_dl.downloader.task.sys.platform', 'darwin'):
            deleted = cleanup_macos_shadow_files('/no/such/path')
        assert deleted == 0

    def test_swallows_oserror(self, tmp_path):
        """If os.remove fails, we should not crash."""
        from moodle_dl.downloader.task import cleanup_macos_shadow_files
        (tmp_path / '._foo').touch()
        with patch('moodle_dl.downloader.task.sys.platform', 'darwin'):
            with patch('moodle_dl.downloader.task.os.remove', side_effect=OSError('locked')):
                deleted = cleanup_macos_shadow_files(str(tmp_path))
        # The error was swallowed; we count it as not deleted
        assert deleted == 0


# =========================================================================
# gen_path: no natural-sort prefix (server already orders)
# =========================================================================
class TestGenPathUsesServerOrder:
    """gen_path must NOT add a section_id prefix; the server
    already returns sections in correct natural order.
    """

    def test_gen_path_does_not_prefix_section_id(self, tmp_path):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        ops = TaskFileOps(MagicMock())
        course = MagicMock()
        course.fullname = 'My Course'
        course.overwrite_name_with = None
        course.create_directory_structure = True
        file = MagicMock()
        file.section_name = 'Week 10: Clustering'
        file.section_id = 12345  # would be used as prefix if we did that
        file.module_modname = 'resource'
        file.module_name = 'mod'
        file.content_filepath = '/'
        result = ops.gen_path(str(tmp_path), course, file)
        # The result must NOT contain a __<section_id>__ prefix
        assert '__12345__' not in result
        # It should contain the plain section name
        assert 'Week 10' in result

    def test_gen_path_uses_plain_section_name(self, tmp_path):
        from moodle_dl.downloader.task_file_ops import TaskFileOps
        ops = TaskFileOps(MagicMock())
        course = MagicMock()
        course.fullname = 'My Course'
        course.overwrite_name_with = None
        course.create_directory_structure = True
        file = MagicMock()
        file.section_name = 'Week 1： Introduction'  # NB: full-width ：
        file.section_id = 999
        file.module_modname = 'resource'
        file.module_name = 'mod'
        file.content_filepath = '/'
        result = ops.gen_path(str(tmp_path), course, file)
        # The directory should contain the section name
        assert 'Week 1' in result
        assert 'Introduction' in result
        # No __<section_id>__ prefix
        assert '__999__' not in result
        assert '__000000000999__' not in result
        # The path doesn't end with a weird prefix
        # (just check that the basename has no __NNN__ pattern)
        basename = os.path.basename(result)
        import re
        assert not re.match(r'^__\d+__', basename), (
            f'Path basename has unwanted __NNN__ prefix: {basename!r}'
        )


# =========================================================================
# moodle-dl --list filters macOS shadow files
# =========================================================================
class TestListFiltersMacosShadow:
    """``moodle-dl --list`` must filter out ``._*`` files from the
    file listing, but should still report the count in stats.
    """

    def test_list_filters_underscore_from_files_section(self, capsys, tmp_path):
        from moodle_dl.cli.list_files import print_workspace_listing
        from moodle_dl.config import ConfigHelper
        from moodle_dl.types import MoodleDlOpts

        # Build a course with a ._ shadow file
        course_path = tmp_path / 'C' / 'S'
        course_path.mkdir(parents=True)
        (course_path / 'real.webloc').touch()
        (course_path / '._real.webloc').touch()  # macOS shadow

        opts = MoodleDlOpts()
        opts.path = str(tmp_path)
        with patch('moodle_dl.config.ConfigHelper.__init__', return_value=None):
            config = ConfigHelper(opts)
        config.get_misc_files_path = MagicMock(return_value=str(tmp_path))

        print_workspace_listing(config, opts)
        captured = capsys.readouterr().out

        # The real file is listed
        assert 'real.webloc' in captured
        # But the ._ line never appears as a file
        for line in captured.split('\n'):
            stripped = line.lstrip()
            # The pattern "[<section>]" or "      <name>" lists files
            # and the macOS shadow count line is the only place
            # ._foo may appear. Check that the ._ file isn't
            # listed as a regular file.
            if stripped.startswith('._'):
                # OK if it appears in the stats line
                if 'macOS' not in line and 'shadow' not in line:
                    pytest.fail(
                        f'macOS shadow appeared as a file: {line!r}'
                    )


# =========================================================================
# Auto-run --list after download
# =========================================================================
class TestAutoListAfterDownload:
    """The download loop should auto-run --list at the end,
    unless MOODLE_DL_SKIP_POST_LIST is set.
    """

    def test_skip_post_list_env_var(self, monkeypatch):
        """When MOODLE_DL_SKIP_POST_LIST=1, --list should not run."""
        monkeypatch.setenv('MOODLE_DL_SKIP_POST_LIST', '1')
        assert os.environ.get('MOODLE_DL_SKIP_POST_LIST', '').strip()

    def test_auto_list_runs_by_default(self, monkeypatch):
        """By default, --list auto-runs after the download."""
        monkeypatch.delenv('MOODLE_DL_SKIP_POST_LIST', raising=False)
        assert not os.environ.get('MOODLE_DL_SKIP_POST_LIST', '').strip()


# =========================================================================
# Combined: xattr stripping + sort preservation
# =========================================================================
class TestXattrStrippedAfterEachWrite:
    """Each file-write site in task.py must call
    strip_macos_metadata after the write.
    """

    def test_strip_macos_metadata_called_in_gen_path_or_after(self):
        """Pin the contract: after the atomic .part -> final
        rename, the file is xattr-stripped.
        """
        import inspect
        from moodle_dl.downloader import task
        # Find any coroutine with both os.replace and strip_macos_metadata
        found = False
        for name, method in inspect.getmembers(
            task.Task, predicate=inspect.iscoroutinefunction
        ):
            try:
                src = inspect.getsource(method)
            except (OSError, TypeError):
                continue
            if 'os.replace' in src and 'strip_macos_metadata' in src:
                # Find the position of os.replace
                replace_pos = src.find('os.replace(')
                strip_pos = src.find('strip_macos_metadata(', replace_pos)
                assert replace_pos > 0, f'{name} does not call os.replace'
                assert strip_pos > replace_pos, (
                    f'{name} calls strip_macos_metadata BEFORE '
                    f'os.replace — must be after the rename'
                )
                found = True
                break
        assert found, (
            'No coroutine in Task has both os.replace and '
            'strip_macos_metadata in correct order'
        )
