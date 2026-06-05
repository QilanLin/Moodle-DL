"""
Tests for PathTools helpers in moodle_dl/utils.py.

These tests focus on edge cases that test_utils_misc.py does *not* exercise
in depth. The autouse fixture restores `PathTools.restricted_filenames` so
test ordering does not matter.
"""

import os
import sys
from pathlib import Path

import pytest

from moodle_dl import utils as utils_module
from moodle_dl.utils import PathTools


@pytest.fixture(autouse=True)
def restore_path_tools_state():
    """Save and restore the class-level `restricted_filenames` flag."""
    original = PathTools.restricted_filenames
    yield
    PathTools.restricted_filenames = original


# --- truncate_filename / truncate_name --------------------------------------


def test_truncate_filename_below_limit_is_unchanged():
    # Short enough -> no truncation, even with restricted_filenames toggled.
    PathTools.restricted_filenames = False
    assert PathTools.truncate_filename('abc.pdf', is_file=True, max_length=10) == 'abc.pdf'


def test_truncate_filename_long_file_keeps_short_extension():
    # max_length=8, '.pdf' is 4 chars, so stem truncates to 3 + '.' + 'pdf'.
    PathTools.restricted_filenames = False
    assert PathTools.truncate_filename('abcdef.pdf', is_file=True, max_length=8) == 'abc\u2026.pdf'


def test_truncate_filename_long_extension_treated_as_no_extension():
    # An extension longer than 20 chars is considered "not an extension".
    # The full name is truncated as-is, so the dot before the long extension
    # is preserved (max_length=8 -> first 7 chars + '…').
    PathTools.restricted_filenames = False
    out = PathTools.truncate_filename(
        'abcdef.somelongthingthatsclearlynotreal', is_file=True, max_length=8
    )
    assert out == 'abcdef.\u2026'


def test_truncate_filename_no_extension_uses_truncate_name():
    # is_file=False -> no extension detection at all.
    PathTools.restricted_filenames = False
    assert PathTools.truncate_filename('abcdef', is_file=False, max_length=4) == 'abc\u2026'


def test_truncate_filename_zero_length_extension_falls_through():
    # A name that ends with '.' has effectively no extension.
    PathTools.restricted_filenames = False
    assert PathTools.truncate_filename('abcdef.', is_file=True, max_length=4) == 'abc\u2026'


def test_truncate_name_uses_three_dots_in_restricted_mode():
    PathTools.restricted_filenames = True
    # 5-char name in restricted mode -> first 2 chars + '...'
    assert PathTools.truncate_name('abcdef', max_length=5) == 'ab...'


def test_truncate_name_uses_unicode_ellipsis_in_unrestricted_mode():
    PathTools.restricted_filenames = False
    assert PathTools.truncate_name('abcdef', max_length=5) == 'abcd\u2026'


# --- sanitize_filename ------------------------------------------------------


def test_sanitize_filename_empty_string_returns_empty():
    # The very first check in sanitize_filename.
    assert PathTools.sanitize_filename('') == ''


def test_sanitize_filename_all_illegal_chars_becomes_underscore():
    # In restricted mode, every char in '&'\\{}[];:,.<>/*?|"' is illegal.
    PathTools.restricted_filenames = False
    assert PathTools.sanitize_filename('////', restricted=True) == '_'


def test_sanitize_filename_accent_chars_replaced_in_restricted_mode():
    # 'é' -> 'e' via ACCENT_CHARS, ' ' -> '_' via restricted rule.
    PathTools.restricted_filenames = False
    assert PathTools.sanitize_filename('é test', restricted=True) == 'e_test'


def test_sanitize_filename_unrestricted_uses_full_width_slash():
    # '/' becomes U+29F8 (the "solidus" big symbol) in unrestricted mode.
    PathTools.restricted_filenames = False
    assert PathTools.sanitize_filename('a/b', restricted=False) == 'a\u29f8b'


def test_sanitize_filename_unrestricted_uses_full_width_backslash():
    # '\\' becomes U+29F9 (the "reverse solidus" big symbol).
    PathTools.restricted_filenames = False
    assert PathTools.sanitize_filename('a\\b', restricted=False) == 'a\u29f9b'


def test_sanitize_filename_timestamp_colons_replaced_with_underscores():
    # The regex `[0-9]+(?::[0-9]+)+` rewrites "12:34:56" -> "12_34_56".
    PathTools.restricted_filenames = False
    assert PathTools.sanitize_filename('12:34:56', restricted=False) == '12_34_56'


def test_sanitize_filename_is_id_false_strips_underscores():
    # With is_id=False the result is normalised: collapse '__', strip leading '_'.
    PathTools.restricted_filenames = False
    assert PathTools.sanitize_filename('___', restricted=False, is_id=False) == '_'
    assert PathTools.sanitize_filename('-leading', restricted=False, is_id=False) == '_leading'


def test_sanitize_filename_is_id_false_keeps_internal_underscores():
    PathTools.restricted_filenames = False
    # Two leading dashes become a single leading '_' but internal content stays.
    assert PathTools.sanitize_filename('_-_Title', restricted=True, is_id=False) == 'Title'


def test_sanitize_filename_is_id_default_treats_colons_in_naming():
    # In restricted mode, ':' is replaced with the NUL-tagged '_-' (which the
    # collapse then condenses).
    PathTools.restricted_filenames = False
    assert PathTools.sanitize_filename('a:b', restricted=True) == 'a_-b'


def test_sanitize_filename_unrestricted_keeps_quote_as_fullwidth():
    # In unrestricted mode '"' is in the '"/\\|*:<>?' set and is replaced
    # with the fullwidth form '\uff02' (since it is not '/' or '\\').
    PathTools.restricted_filenames = False
    assert PathTools.sanitize_filename('"hi"', restricted=False) == '\uff02hi\uff02'


def test_sanitize_filename_full_width_chars_normalised_via_nfkc():
    # Full-width 'Ａ' (U+FF21) normalises to 'A' under NFKC.
    PathTools.restricted_filenames = False
    assert PathTools.sanitize_filename('Ａ', restricted=True) == 'A'


# --- sanitize_path ----------------------------------------------------------


def test_sanitize_path_normalises_dot_segments_via_normpath():
    # os.path.normpath collapses './a/../b' to 'b' before sanitization.
    # The important guarantee is that '.' and '..' are not passed through
    # to_valid_name (which would strip them as illegal chars).
    PathTools.restricted_filenames = False
    result = PathTools.sanitize_path('./a/../b')
    # The result must not contain leading/trailing dots in segment names.
    for segment in result.split(os.sep):
        assert segment not in ('.', '..')


def test_sanitize_path_preserves_dot_and_dotdot_at_normpath_level():
    # When the path is purely navigational ('a/..'), normpath returns '.'
    # and sanitize_path passes '.' through unchanged (special case).
    PathTools.restricted_filenames = False
    result = PathTools.sanitize_path('a/..')
    # The leading '.' in the result must come from normpath, not to_valid_name.
    assert result == '.'


def test_sanitize_path_collapses_multiple_separators():
    # os.path.normpath collapses runs of separators.
    PathTools.restricted_filenames = False
    assert PathTools.sanitize_path('a//b///c') == os.path.join('a', 'b', 'c')


def test_sanitize_path_sanitizes_illegal_chars_in_single_segment():
    # When '/' appears inside a single segment (e.g. on Windows, after
    # splitdrive), to_valid_name replaces it with the fullwidth form.
    PathTools.restricted_filenames = False
    result = PathTools.sanitize_path('a\u29f8b')  # 'a' + big-slash + 'b'
    assert result == 'a\u29f8b'


def test_sanitize_path_passes_simple_segments_through():
    # Plain ASCII path segments should round-trip unchanged.
    PathTools.restricted_filenames = False
    assert PathTools.sanitize_path('foo/bar/baz') == os.path.join('foo', 'bar', 'baz')


def test_sanitize_path_windows_drive_letter_mocked(monkeypatch):
    # os.path.splitdrive recognises drive letters only on Windows. We mock
    # the drive/UNC detection indirectly by patching os.path.splitdrive.
    PathTools.restricted_filenames = False
    monkeypatch.setattr(utils_module.os.path, 'splitdrive', lambda p: ('C:', p[2:]) if p.startswith('C:') else ('', p))
    result = PathTools.sanitize_path('C:/foo/bar?')
    # The drive prefix is preserved and 'bar?' becomes 'bar\uff1f'.
    assert result.startswith('C:' + os.path.sep)
    assert result.endswith(os.path.join('foo', 'bar\uff1f'))


# --- path_of_file* helpers --------------------------------------------------


def test_path_of_file_in_module_sanitizes_every_segment(tmp_path):
    PathTools.restricted_filenames = False
    out = PathTools.path_of_file_in_module(
        str(tmp_path),
        'Course: 1',
        'Week/One',
        'Module*Name',
        'sub/Slides?.pdf',
    )
    parts = Path(out).parts
    assert parts[-5:] == (
        'Course\uff1a 1',
        'Week\u29f8One',
        'Module\uff0aName',
        'sub',
        'Slides\uff1f.pdf',
    )


def test_path_of_file_strips_leading_slash_from_subpath(tmp_path):
    # The trailing .strip('/') on sanitize_path output removes the leading
    # '//' on the sub-path so only a single 'sub' segment remains.
    PathTools.restricted_filenames = False
    out = PathTools.path_of_file(str(tmp_path), 'Course A', 'Section', '//sub//file.pdf')
    parts = Path(out).parts
    # The last two path components should be the file under 'sub'.
    assert parts[-1] == 'file.pdf'
    assert parts[-2] == 'sub'


def test_flat_path_of_file_drops_module_and_section(tmp_path):
    PathTools.restricted_filenames = False
    out = PathTools.flat_path_of_file(str(tmp_path), 'Course: 1', 'sub/Slides?.pdf')
    # The last two path components should be the subdir + sanitized file.
    parts = Path(out).parts
    assert parts[-1] == 'Slides\uff1f.pdf'
    assert parts[-2] == 'sub'


def test_path_of_file_in_module_uses_storage_path_as_root(tmp_path):
    PathTools.restricted_filenames = False
    out = PathTools.path_of_file_in_module(
        str(tmp_path),
        'C',
        'S',
        'M',
        '',
    )
    # Storage path should be the very first part of the result.
    assert str(out).startswith(str(tmp_path))


# --- remove_file / get_abs_path / make_path / make_dirs / make_base_dir ----


def test_remove_file_nonexistent_does_not_raise(tmp_path):
    # No exception even when the file does not exist.
    PathTools.remove_file(str(tmp_path / 'does-not-exist.txt'))
    PathTools.remove_file(None)


def test_remove_file_removes_existing_file(tmp_path):
    target = tmp_path / 'x.txt'
    target.write_text('hi', encoding='utf-8')
    PathTools.remove_file(str(target))
    assert not target.exists()


def test_get_abs_path_resolves_relative_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = PathTools.get_abs_path('rel/file.txt')
    # Result should be absolute and rooted at tmp_path.
    assert os.path.isabs(resolved)
    assert resolved.startswith(str(tmp_path.resolve()))


def test_get_abs_path_returns_absolute_unchanged(tmp_path):
    absolute = str(tmp_path / 'already-absolute.txt')
    assert PathTools.get_abs_path(absolute) == absolute


def test_make_path_joins_multiple_filenames(tmp_path):
    base = str(tmp_path)
    assert PathTools.make_path(base, 'a', 'b', 'c.txt') == str(Path(base) / 'a' / 'b' / 'c.txt')


def test_make_path_with_no_filenames_returns_base(tmp_path):
    assert PathTools.make_path(str(tmp_path)) == str(tmp_path)


def test_make_base_dir_creates_parent_chain(tmp_path):
    target = tmp_path / 'a' / 'b' / 'file.txt'
    PathTools.make_base_dir(str(target))
    assert target.parent.is_dir()
    assert not target.exists()  # base dir only, not the file


def test_make_dirs_existing_is_noop(tmp_path):
    # Calling make_dirs on an existing dir must not raise (exist_ok=True).
    PathTools.make_dirs(str(tmp_path))
    PathTools.make_dirs(str(tmp_path))  # second call must still succeed


def test_make_dirs_creates_nested(tmp_path):
    target = tmp_path / 'x' / 'y' / 'z'
    PathTools.make_dirs(str(target))
    assert target.is_dir()


# --- win_max_path_length_workaround ----------------------------------------


def test_win_max_path_length_workaround_prepends_unc_prefix(monkeypatch):
    # Mock the Windows detection and the abs path resolver.
    monkeypatch.setattr(utils_module.os, 'name', 'nt', raising=False)
    monkeypatch.setattr(utils_module.sys, 'platform', 'win32')
    monkeypatch.setattr(PathTools, 'get_abs_path', staticmethod(lambda p: 'C:\\abs' + p.replace('/', '\\')))
    out = PathTools.win_max_path_length_workaround('/relative/path.txt')
    # Result must start with the literal "\\\\?\\" prefix.
    assert out.startswith('\\\\?\\')
    assert 'absolute' in out.lower() or 'abs' in out.lower() or out.startswith('\\\\?\\C:')


def test_win_max_path_length_workaround_noop_on_non_windows(monkeypatch):
    # When os.name != 'nt' and platform is not 'win32'/'cygwin', no prefix.
    monkeypatch.setattr(utils_module.os, 'name', 'posix', raising=False)
    monkeypatch.setattr(utils_module.sys, 'platform', 'linux')
    assert PathTools.win_max_path_length_workaround('/tmp/file.txt') == '/tmp/file.txt'


# --- get_unused_filename / get_unused_file_path ----------------------------


def test_get_unused_filename_returns_original_when_no_collision(tmp_path):
    # Nothing exists yet -> the "clear" name is returned.
    out = PathTools.get_unused_filename(str(tmp_path), 'video', 'mp4')
    assert out == str(tmp_path / 'video.mp4')


def test_get_unused_filename_appends_underscore_zero_one(tmp_path):
    # Create the clear file, the next call should add '_01'.
    (tmp_path / 'video.mp4').write_text('x', encoding='utf-8')
    out = PathTools.get_unused_filename(str(tmp_path), 'video', 'mp4')
    assert out == str(tmp_path / 'video_01.mp4')

    # And again: '_02'.
    (tmp_path / 'video_01.mp4').write_text('x', encoding='utf-8')
    out = PathTools.get_unused_filename(str(tmp_path), 'video', 'mp4')
    assert out == str(tmp_path / 'video_02.mp4')


def test_get_unused_filename_start_clear_false_uses_zero(tmp_path):
    # With start_clear=False the very first call uses '_00' regardless.
    (tmp_path / 'video.mp4').write_text('x', encoding='utf-8')
    out = PathTools.get_unused_filename(str(tmp_path), 'video', 'mp4', start_clear=False)
    assert out == str(tmp_path / 'video_00.mp4')


def test_get_unused_file_path_uses_path_parts(tmp_path):
    # get_unused_file_path splits a full path and forwards the parts.
    target = tmp_path / 'archive.tar.gz'
    target.write_text('x', encoding='utf-8')
    out = PathTools.get_unused_file_path(str(target))
    # The 'tar.gz' -> file_name='archive.tar', ext='gz'
    assert out == str(tmp_path / 'archive.tar_01.gz')


def test_get_unused_file_path_no_collision(tmp_path):
    out = PathTools.get_unused_file_path(str(tmp_path / 'fresh.bin'))
    assert out == str(tmp_path / 'fresh.bin')


# --- get_path_parts ---------------------------------------------------------


def test_get_path_parts_double_extension():
    parts = PathTools.get_path_parts('/some/dir/file.tar.gz')
    assert parts.dir_name == '/some/dir'
    assert parts.file_name == 'file.tar'
    assert parts.file_extension == 'gz'


def test_get_path_parts_single_extension():
    parts = PathTools.get_path_parts('/dir/file.txt')
    assert parts.dir_name == '/dir'
    assert parts.file_name == 'file'
    assert parts.file_extension == 'txt'


def test_get_path_parts_no_extension():
    parts = PathTools.get_path_parts('/dir/noext')
    assert parts.dir_name == '/dir'
    assert parts.file_name == 'noext'
    assert parts.file_extension == ''


# --- get_file_exts ----------------------------------------------------------


def test_get_file_exts_double_extension_lowercased():
    assert PathTools.get_file_exts('Archive.TAR.GZ') == ('tar', 'gz')


def test_get_file_exts_single_extension_lowercased():
    assert PathTools.get_file_exts('REPORT.PDF') == (None, 'pdf')


def test_get_file_exts_no_extension():
    assert PathTools.get_file_exts('README') == (None, None)


# --- to_valid_name ----------------------------------------------------------


def test_to_valid_name_none_returns_none():
    PathTools.restricted_filenames = False
    assert PathTools.to_valid_name(None, is_file=True) is None
    assert PathTools.to_valid_name(None, is_file=False) is None


def test_to_valid_name_strips_invisible_chars():
    # \n \r \t and \xad (soft hyphen) are all removed/replaced.
    PathTools.restricted_filenames = False
    assert PathTools.to_valid_name('a\nb\rc\td\xade', is_file=False) == 'a b c de'


def test_to_valid_name_collapses_double_spaces():
    PathTools.restricted_filenames = False
    assert PathTools.to_valid_name('a    b', is_file=False) == 'a b'


def test_to_valid_name_unwraps_html_entities():
    # &amp; &lt; &gt; &quot; &#39; should all be unescaped.
    PathTools.restricted_filenames = False
    out = PathTools.to_valid_name('Tom &amp; Jerry &lt;3', is_file=False)
    assert 'Tom' in out and 'Jerry' in out
    # The '<' character is illegal; even after unescape the result has no '<'.
    assert '<' not in out


def test_to_valid_name_strips_html_badge_markup():
    # The known HTML tags (span, badge, etc.) are replaced with a single space.
    PathTools.restricted_filenames = False
    out = PathTools.to_valid_name('Lecture <span class="badge bg-success">Core!</span>', is_file=False)
    assert 'Core!' in out
    assert 'span' not in out
    assert 'badge' not in out
    assert 'class' not in out


def test_to_valid_name_truncates_when_exceeding_max_length():
    PathTools.restricted_filenames = False
    long = 'a' * 300
    out = PathTools.to_valid_name(long, is_file=False, max_length=50)
    assert len(out) <= 50


def test_to_valid_name_preserves_short_extension_for_files():
    PathTools.restricted_filenames = False
    long_stem = 'a' * 250
    out = PathTools.to_valid_name(f'{long_stem}.pdf', is_file=True, max_length=60)
    # Must still end with the extension, and the stem should be truncated.
    assert out.endswith('.pdf')


def test_to_valid_name_strips_leading_and_trailing_dots_and_spaces():
    PathTools.restricted_filenames = False
    out = PathTools.to_valid_name('  .hidden.  ', is_file=False)
    assert not out.startswith('.')
    assert not out.startswith(' ')
    assert not out.endswith(' ')
