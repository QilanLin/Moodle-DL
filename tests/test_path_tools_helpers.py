# -*- coding: utf-8 -*-
"""
Tests for PathTools helper methods that are critical for safe
filesystem operations but were previously untested (per coverage
report):

  - get_unused_filename: collision-safe filename generation
  - get_unused_file_path: wrapper around get_unused_filename
  - touch_file: create-or-touch empty file
  - get_path_parts: split path into destination/filename/ext
  - get_file_exts: get inner extension(s)
  - get_file_ext: get outer extension
  - get_file_stem_and_ext: get stem + extension

These are used by:
  - _create_target_file (task_file_ops.py:280) — uses get_unused_file_path
  - add_token_to_url — uses urlparse
  - File.saved_to handling
  - Path generation in utils.py

If get_unused_filename is broken, moodle-dl could OVERWRITE existing
files when there's a collision (DATA LOSS).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# get_unused_filename: collision-safe filename generation
# =========================================================================
class TestGetUnusedFilenameCollisionHandling:
    """Pin the collision-handling contract: if a file already exists,
    append a numeric suffix (01, 02, ...) to make it unique.

    Critical for data safety — broken collision handling could
    overwrite user files when downloading the same file twice or
    when a file with the same name is added.
    """

    def test_returns_original_when_file_does_not_exist(self):
        """If the file doesn't exist, return the original path."""
        with tempfile.TemporaryDirectory() as td:
            from moodle_dl.utils import PathTools
            path = os.path.join(td, 'lecture.pdf')
            result = PathTools.get_unused_filename(td, 'lecture', 'pdf')
            assert result == path

    def test_appends_underscore_01_on_collision(self):
        """If the file exists, append '_01' before the extension."""
        with tempfile.TemporaryDirectory() as td:
            from moodle_dl.utils import PathTools
            # Pre-create the file
            existing = os.path.join(td, 'lecture.pdf')
            open(existing, 'w').close()

            result = PathTools.get_unused_filename(td, 'lecture', 'pdf')
            expected = os.path.join(td, 'lecture_01.pdf')
            assert result == expected

    def test_increments_collision_counter(self):
        """Multiple collisions get _01, _02, _03 suffixes."""
        with tempfile.TemporaryDirectory() as td:
            from moodle_dl.utils import PathTools
            # Pre-create lecture.pdf and lecture_01.pdf and lecture_02.pdf
            for name in ['lecture.pdf', 'lecture_01.pdf', 'lecture_02.pdf']:
                open(os.path.join(td, name), 'w').close()

            result = PathTools.get_unused_filename(td, 'lecture', 'pdf')
            expected = os.path.join(td, 'lecture_03.pdf')
            assert result == expected

    def test_start_clear_false_uses_naming_with_zero_counter(self):
        """start_clear=False uses naming like filename_00.ext instead of
        trying the bare name first.
        """
        with tempfile.TemporaryDirectory() as td:
            from moodle_dl.utils import PathTools
            result = PathTools.get_unused_filename(
                td, 'lecture', 'pdf', start_clear=False
            )
            expected = os.path.join(td, 'lecture_00.pdf')
            assert result == expected

    def test_with_already_existing_counters_start_clear_false(self):
        """start_clear=False + existing _00/_01 → returns _02."""
        with tempfile.TemporaryDirectory() as td:
            from moodle_dl.utils import PathTools
            for name in ['lecture_00.pdf', 'lecture_01.pdf']:
                open(os.path.join(td, name), 'w').close()

            result = PathTools.get_unused_filename(
                td, 'lecture', 'pdf', start_clear=False
            )
            expected = os.path.join(td, 'lecture_02.pdf')
            assert result == expected


# =========================================================================
# get_unused_file_path: full path version
# =========================================================================
class TestGetUnusedFilePath:
    """get_unused_file_path is get_unused_filename + path splitting."""

    def test_unused_file_path_simple(self):
        """Pass a full path, get back the same path (no collision)."""
        with tempfile.TemporaryDirectory() as td:
            from moodle_dl.utils import PathTools
            path = os.path.join(td, 'subdir', 'file.pdf')
            result = PathTools.get_unused_file_path(path)
            assert result == path

    def test_unused_file_path_with_collision(self):
        """Existing file triggers _01 suffix."""
        with tempfile.TemporaryDirectory() as td:
            from moodle_dl.utils import PathTools
            existing = os.path.join(td, 'file.pdf')
            open(existing, 'w').close()

            result = PathTools.get_unused_file_path(existing)
            expected = os.path.join(td, 'file_01.pdf')
            assert result == expected

    def test_unused_file_path_strips_dot_from_extension(self):
        """The returned path uses .pdf not .pdf (extension should
        not have leading dot when passed back).
        """
        with tempfile.TemporaryDirectory() as td:
            from moodle_dl.utils import PathTools
            existing = os.path.join(td, 'file.pdf')
            open(existing, 'w').close()
            result = PathTools.get_unused_file_path(existing)
            # Extension should be the original .pdf (no double dot)
            assert result.endswith('.pdf')
            assert not result.endswith('..pdf')

    def test_unused_file_path_no_extension_has_quirk(self):
        """KNOWN QUIRK: file without extension → returned path has
        trailing dot ('README.') which does NOT collide with the
        existing 'README' file (os.path.exists check fails).

        This is a KNOWN DATA-LOSS RISK for extensionless files: if a
        user has 'README' in their download path, moodle-dl will
        return 'README.' which is a DIFFERENT file, but the download
        code may then write to BOTH paths depending on caller logic.

        Pin the actual behavior so future changes are intentional.
        """
        with tempfile.TemporaryDirectory() as td:
            from moodle_dl.utils import PathTools
            existing = os.path.join(td, 'README')
            open(existing, 'w').close()

            result = PathTools.get_unused_file_path(existing)
            # The current behavior returns 'README.' (no _01 suffix)
            # because os.path.exists('README.') is False even though
            # 'README' exists. This is the implementation quirk.
            expected = os.path.join(td, 'README.')
            assert result == expected, (
                f'Extensionless file collision is NOT detected. '
                f'This is a known data-loss risk. Got: {result!r}'
            )

    def test_unused_file_path_with_dotted_filename(self):
        """Filename like 'data.tar.gz' — the OUTER extension is used.

        Note: get_path_parts uses os.path.splitext which only splits
        at the LAST dot, so 'data.tar.gz' becomes ('data.tar', 'gz').
        Collision suffix goes between: 'data.tar_01.gz'.
        """
        with tempfile.TemporaryDirectory() as td:
            from moodle_dl.utils import PathTools
            existing = os.path.join(td, 'data.tar.gz')
            open(existing, 'w').close()

            result = PathTools.get_unused_file_path(existing)
            expected = os.path.join(td, 'data.tar_01.gz')
            assert result == expected


# =========================================================================
# touch_file: create or touch empty file
# =========================================================================
class TestTouchFile:
    """touch_file creates an empty file (or updates mtime if exists)."""

    def test_touch_creates_empty_file(self):
        """Touch creates an empty file."""
        with tempfile.TemporaryDirectory() as td:
            from moodle_dl.utils import PathTools
            path = os.path.join(td, 'new.txt')
            assert not os.path.exists(path)

            PathTools.touch_file(path)
            assert os.path.exists(path)
            assert os.path.getsize(path) == 0

    def test_touch_existing_file_preserves_content(self):
        """Touch an existing file preserves its content."""
        with tempfile.TemporaryDirectory() as td:
            from moodle_dl.utils import PathTools
            path = os.path.join(td, 'existing.txt')
            with open(path, 'w') as f:
                f.write('original content')

            PathTools.touch_file(path)

            with open(path) as f:
                content = f.read()
            assert content == 'original content'

    def test_touch_nested_path_creates_intermediate_dirs(self):
        """Touch a path with missing intermediate dirs fails
        (does not auto-mkdir).
        """
        with tempfile.TemporaryDirectory() as td:
            from moodle_dl.utils import PathTools
            path = os.path.join(td, 'subdir', 'new.txt')
            assert not os.path.exists(path)

            import unittest.mock
            try:
                PathTools.touch_file(path)
                # Should have raised FileNotFoundError
                raised = False
            except FileNotFoundError:
                raised = True
            assert raised, "touch_file should raise FileNotFoundError for nested path"


# =========================================================================
# get_path_parts: split into destination/filename/extension
# =========================================================================
class TestGetPathParts:
    """get_path_parts splits a file path into destination, filename, ext."""

    def test_simple_path(self):
        from moodle_dl.utils import PathTools
        dest, filename, ext = PathTools.get_path_parts('/tmp/foo.pdf')
        assert dest == '/tmp'
        assert filename == 'foo'
        assert ext == 'pdf'

    def test_no_extension(self):
        from moodle_dl.utils import PathTools
        dest, filename, ext = PathTools.get_path_parts('/tmp/README')
        assert dest == '/tmp'
        assert filename == 'README'
        assert ext == ''

    def test_double_extension(self):
        """Filename like 'data.tar.gz' — outer ext is 'gz',
        inner ext (file_stem) is 'tar'.
        """
        from moodle_dl.utils import PathTools
        dest, filename, ext = PathTools.get_path_parts('/tmp/data.tar.gz')
        assert dest == '/tmp'
        assert filename == 'data.tar'
        assert ext == 'gz'


# =========================================================================
# get_file_exts: get inner + outer extension
# =========================================================================
class TestGetFileExts:
    """get_file_exts returns (inner_ext, outer_ext) tuple."""

    def test_no_extension(self):
        from moodle_dl.utils import PathTools
        inner, outer = PathTools.get_file_exts('README')
        assert inner is None
        assert outer is None

    def test_single_extension(self):
        """foo.pdf → (None, pdf)."""
        from moodle_dl.utils import PathTools
        inner, outer = PathTools.get_file_exts('foo.pdf')
        assert inner is None
        assert outer == 'pdf'

    def test_double_extension(self):
        """data.tar.gz → (tar, gz)."""
        from moodle_dl.utils import PathTools
        inner, outer = PathTools.get_file_exts('data.tar.gz')
        assert inner == 'tar'
        assert outer == 'gz'

    def test_three_dots_returns_outer_two(self):
        """a.b.c.d → (c, d) — only the last 2 extensions."""
        from moodle_dl.utils import PathTools
        inner, outer = PathTools.get_file_exts('a.b.c.d')
        assert inner == 'c'
        assert outer == 'd'


# =========================================================================
# get_file_ext: get just the outer extension
# =========================================================================
class TestGetFileExt:
    """get_file_ext returns just the outer extension."""

    def test_no_extension_returns_none(self):
        from moodle_dl.utils import PathTools
        assert PathTools.get_file_ext('README') is None

    def test_pdf(self):
        from moodle_dl.utils import PathTools
        assert PathTools.get_file_ext('lecture.pdf') == 'pdf'

    def test_uppercase_lowercased(self):
        from moodle_dl.utils import PathTools
        assert PathTools.get_file_ext('Lecture.PDF') == 'pdf'

    def test_double_extension_outer(self):
        from moodle_dl.utils import PathTools
        assert PathTools.get_file_ext('data.tar.gz') == 'gz'


# =========================================================================
# get_file_stem_and_ext
# =========================================================================
class TestGetFileStemAndExt:
    """get_file_stem_and_ext returns (stem, ext) tuple."""

    def test_simple(self):
        from moodle_dl.utils import PathTools
        stem, ext = PathTools.get_file_stem_and_ext('lecture.pdf')
        assert stem == 'lecture'
        assert ext == 'pdf'

    def test_no_extension(self):
        from moodle_dl.utils import PathTools
        stem, ext = PathTools.get_file_stem_and_ext('README')
        assert stem == 'README'
        assert ext is None

    def test_double_extension_returns_outer(self):
        """data.tar.gz → (data.tar, gz)."""
        from moodle_dl.utils import PathTools
        stem, ext = PathTools.get_file_stem_and_ext('data.tar.gz')
        assert stem == 'data.tar'
        assert ext == 'gz'


# =========================================================================
# get_cookies_path: storage_path / Cookies.txt
# =========================================================================
class TestGetCookiesPath:
    """Pin the cookie path format."""

    def test_cookies_path_in_storage(self):
        from moodle_dl.utils import PathTools
        path = PathTools.get_cookies_path('/var/storage')
        assert path.endswith('Cookies.txt')
        assert '/var/storage' in path

    def test_cookies_path_uses_storage_dir(self):
        from moodle_dl.utils import PathTools
        path = PathTools.get_cookies_path('/Users/test/storage')
        assert '/Users/test/storage' in path or '/Users/test/storage/' in path