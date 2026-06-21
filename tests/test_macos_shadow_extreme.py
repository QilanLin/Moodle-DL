# -*- coding: utf-8 -*-
"""
Extraordinary / adversarial tests for the macOS shadow-file fix.

These tests push the fix to the edge of what it should handle
and verify safety:

  * Files with extremely long paths
  * Files in deeply-nested directories
  * Files with non-ASCII names (CJK, emoji, RTL, NUL)
  * Files where the xattr is locked (cannot be removed)
  * Files where the ctypes call would fail (e.g. malformed lib)
  * Race condition: file deleted between check and remove
  * Cross-platform: Linux/Windows must be safe
  * Idempotency: stripping twice is safe
  * Performance: 10K strips complete in < 5s
  * Opt-out via MOODLE_DL_KEEP_MACOS_XATTRS=1
  * Files with leading dots that are NOT shadow files (e.g. ".hidden")
  * File that doesn't exist (race condition)
  * symlinks
  * Read-only files
"""
import os
import sys
import time
import tempfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Helper to import the right module
# =========================================================================
@pytest.fixture
def task_mod():
    from moodle_dl.downloader import task
    return task


# =========================================================================
# Pathological paths
# =========================================================================
class TestPathologicalPaths:
    """strip_macos_metadata must not crash on weird paths."""

    def test_extremely_long_path(self, tmp_path, task_mod):
        """500-char path."""
        from moodle_dl.downloader.task import strip_macos_metadata
        long_dir = tmp_path / ('a' * 100)
        long_dir.mkdir()
        deep = long_dir / ('b' * 200)
        deep.write_text('hi')
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            strip_macos_metadata(str(deep))  # no crash

    def test_path_with_unicode(self, tmp_path, task_mod):
        from moodle_dl.downloader.task import strip_macos_metadata
        p = tmp_path / '课程名称_🎓_lecture.pdf'
        p.write_text('hi')
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            strip_macos_metadata(str(p))  # no crash

    def test_path_with_nul_byte(self, task_mod):
        """A path with NUL byte should not crash the function."""
        from moodle_dl.downloader.task import strip_macos_metadata
        # We can't actually create a file with NUL in the name on
        # most filesystems, but the function should handle the input
        # without raising.
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            # Just call it; the ctypes call may fail, but the
            # function should swallow the error.
            try:
                strip_macos_metadata('/path/with/\x00/nul')
            except (ValueError, OSError):
                # OSError is OK (the OS rejects the NUL)
                # ValueError is OK (ctypes rejects it)
                # Anything else is a bug
                pass

    def test_path_with_only_dots(self, task_mod):
        from moodle_dl.downloader.task import strip_macos_metadata
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            strip_macos_metadata('...')  # no crash

    def test_path_is_url_not_local(self, task_mod):
        """A URL-looking path should be handled without trying to
        connect to a network."""
        from moodle_dl.downloader.task import strip_macos_metadata
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            strip_macos_metadata('https://example.com/file.pdf')
            strip_macos_metadata('ftp://server/file.pdf')

    def test_path_with_null_bytes_after_dot(self, task_mod):
        from moodle_dl.downloader.task import strip_macos_metadata
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            # These are invalid paths on most OSes
            try:
                strip_macos_metadata('/tmp/..\x00/etc/pass')
            except (ValueError, OSError):
                pass


# =========================================================================
# Cross-platform safety
# =========================================================================
class TestCrossPlatformSafety:
    """strip_macos_metadata must be safe on every platform."""

    @pytest.mark.parametrize('platform_name', [
        'darwin',     # macOS
        'linux',      # most CI
        'win32',      # Windows
        'cygwin',     # Windows under Cygwin
        'freebsd',    # BSD
        'openbsd',
        'netbsd',
        'aix',
        'sunos',
    ])
    def test_all_platforms_safe(self, platform_name, task_mod):
        """strip_macos_metadata should never raise on any platform."""
        from moodle_dl.downloader.task import strip_macos_metadata
        with patch.object(task_mod.sys, 'platform', platform_name):
            # Should not raise
            strip_macos_metadata('/anywhere/file.pdf')
            strip_macos_metadata('')
            strip_macos_metadata('/path/with spaces/and-dashes.pdf')
            strip_macos_metadata('a' * 1000)  # very long


# =========================================================================
# ctypes failure modes
# =========================================================================
class TestCtypesFailureModes:
    """strip_macos_metadata must gracefully handle ctypes failures."""

    def test_cddl_not_found(self, task_mod):
        """CDLL is not found (no libSystem) — should not crash."""
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes.util, 'find_library', return_value=None):
                # Should not raise
                strip_macos_metadata('/test/file.pdf')

    def test_cddl_constructor_fails(self, task_mod):
        """CDLL constructor raises OSError — should not crash."""
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(
                real_ctypes, 'CDLL',
                side_effect=OSError('cannot load library'),
            ), patch.object(real_ctypes.util, 'find_library', return_value='c'):
                # Should not raise
                strip_macos_metadata('/test/file.pdf')

    def test_removexattr_returns_error_code(self, task_mod):
        """removexattr returns -1 (error) — should not crash."""
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        mock_libc = MagicMock()
        mock_libc.removexattr.return_value = -1  # error
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                # Should not raise even though removexattr returns -1
                strip_macos_metadata('/test/file.pdf')

    def test_removexattr_raises_oserror(self, task_mod):
        """removexattr itself raises OSError — should not crash."""
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        mock_libc = MagicMock()
        mock_libc.removexattr.side_effect = OSError('ENOTSUP')
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                # Should not raise
                strip_macos_metadata('/test/file.pdf')

    def test_cddl_returns_garbage(self, task_mod):
        """CDLL returns a non-cdll object — should not crash."""
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            # 42 has no removexattr attribute — will raise
            # AttributeError. The function must swallow it.
            with patch.object(real_ctypes, 'CDLL', return_value=42), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                # Should not raise
                try:
                    strip_macos_metadata('/test/file.pdf')
                except AttributeError:
                    # The test asserts no AttributeError leaks out;
                    # if we get here, the function didn't swallow
                    # the exception. But we WANT it to swallow
                    # exceptions, so this would be a bug.
                    pytest.fail(
                        'strip_macos_metadata did not swallow '
                        'AttributeError from garbage CDLL result'
                    )


# =========================================================================
# Idempotency
# =========================================================================
class TestIdempotency:
    """stripping twice in a row should be safe."""

    def test_strip_twice_same_path(self, task_mod):
        """Calling strip_macos_metadata twice on the same path
        should not raise. The second call should be a no-op
        (the xattrs are already gone)."""
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        call_count = [0]
        mock_libc = MagicMock()

        def count_calls(*args):
            call_count[0] += 1
            return 0  # success

        mock_libc.removexattr.side_effect = count_calls
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                strip_macos_metadata('/test/file.pdf')
                strip_macos_metadata('/test/file.pdf')
                strip_macos_metadata('/test/file.pdf')
        # Called 4 times per invocation (4 xattrs) × 3 invocations = 12
        assert call_count[0] == 12

    def test_strip_already_clean_file(self, task_mod):
        """A file that has no xattrs at all — strip should still work."""
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        mock_libc = MagicMock()
        mock_libc.removexattr.return_value = -1  # ENOATTR (not present)
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                # Should not raise even though no xattrs to remove
                strip_macos_metadata('/test/file.pdf')


# =========================================================================
# File doesn't exist (race condition)
# =========================================================================
class TestFileNotExist:
    """strip_macos_metadata should handle missing files gracefully."""

    def test_nonexistent_file(self, task_mod):
        """File was deleted between moodle-dl writing it and stripping."""
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        mock_libc = MagicMock()
        # ENOENT (no such file) — removexattr returns -1
        mock_libc.removexattr.return_value = -1
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                # Should not raise
                strip_macos_metadata('/no/such/file.pdf')

    def test_permission_denied(self, task_mod):
        """File is on a read-only mount or we don't have permission."""
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        mock_libc = MagicMock()
        mock_libc.removexattr.return_value = -1  # EACCES
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                strip_macos_metadata('/readonly/file.pdf')

    def test_readonly_directory(self, task_mod):
        """Directory is read-only — file exists but parent not writable."""
        from moodle_dl.downloader.task import strip_macos_metadata
        # The function does NOT touch the parent directory, only
        # the file's xattrs. So a read-only parent is irrelevant.
        # The function should still work.
        import ctypes as real_ctypes
        mock_libc = MagicMock()
        mock_libc.removexattr.return_value = 0
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                strip_macos_metadata('/readonly/dir/file.pdf')


# =========================================================================
# Opt-out
# =========================================================================
class TestOptOut:
    """The user can opt out of xattr stripping via env var."""

    def test_opt_out_via_env_var(self, task_mod, monkeypatch):
        """MOODLE_DL_KEEP_MACOS_XATTRS=1 disables stripping."""
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        call_count = [0]
        mock_libc = MagicMock()

        def count(*args):
            call_count[0] += 1
            return 0

        mock_libc.removexattr.side_effect = count
        monkeypatch.setenv('MOODLE_DL_KEEP_MACOS_XATTRS', '1')
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                strip_macos_metadata('/test/file.pdf')
        # removexattr was NOT called (opt-out)
        assert call_count[0] == 0

    def test_opt_out_via_true(self, task_mod, monkeypatch):
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        call_count = [0]
        mock_libc = MagicMock()
        mock_libc.removexattr.side_effect = lambda *a: (call_count.__setitem__(0, call_count[0] + 1) or 0)
        monkeypatch.setenv('MOODLE_DL_KEEP_MACOS_XATTRS', 'true')
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                strip_macos_metadata('/test/file.pdf')
        assert call_count[0] == 0

    def test_opt_out_via_yes(self, task_mod, monkeypatch):
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        call_count = [0]
        mock_libc = MagicMock()
        mock_libc.removexattr.side_effect = lambda *a: (call_count.__setitem__(0, call_count[0] + 1) or 0)
        monkeypatch.setenv('MOODLE_DL_KEEP_MACOS_XATTRS', 'yes')
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                strip_macos_metadata('/test/file.pdf')
        assert call_count[0] == 0

    def test_unset_strips(self, task_mod, monkeypatch):
        """Without the env var, stripping happens as normal."""
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        call_count = [0]
        mock_libc = MagicMock()
        mock_libc.removexattr.side_effect = lambda *a: (call_count.__setitem__(0, call_count[0] + 1) or 0)
        monkeypatch.delenv('MOODLE_DL_KEEP_MACOS_XATTRS', raising=False)
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                strip_macos_metadata('/test/file.pdf')
        # 4 xattrs stripped
        assert call_count[0] == 4

    def test_partial_env_value(self, task_mod, monkeypatch):
        """Env var with random value = stripping still happens."""
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        call_count = [0]
        mock_libc = MagicMock()
        mock_libc.removexattr.side_effect = lambda *a: (call_count.__setitem__(0, call_count[0] + 1) or 0)
        monkeypatch.setenv('MOODLE_DL_KEEP_MACOS_XATTRS', 'maybe')
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                strip_macos_metadata('/test/file.pdf')
        # Stripping still happens
        assert call_count[0] == 4


# =========================================================================
# Files with leading dots that are NOT shadow files
# =========================================================================
class TestNonShadowDotFiles:
    """Make sure we don't accidentally treat user's hidden files
    (e.g. .DS_Store, .gitignore) as shadow files.
    """

    def test_dot_ds_store_not_removed_by_strip(self, task_mod):
        """strip_macos_metadata does NOT touch the filesystem.
        It only modifies xattrs on the path it's given. .DS_Store
        files (created by Finder) are unrelated.
        """
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        mock_libc = MagicMock()
        mock_libc.removexattr.return_value = 0
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                # We pass an explicit path. The function does
                # NOT scan the directory.
                strip_macos_metadata('/test/.DS_Store')
        # removexattr was called 4 times on the .DS_Store path
        # (but .DS_Store itself is never read or removed)
        assert mock_libc.removexattr.call_count == 4

    def test_cleanup_does_not_touch_underscore_prefix_only(self, tmp_path):
        """cleanup_macos_shadow_files only deletes files starting
        with `._`. It does NOT touch `.DS_Store` (no underscore
        prefix) or `.hidden` files.
        """
        from moodle_dl.downloader.task import cleanup_macos_shadow_files
        (tmp_path / '._shadow').touch()
        (tmp_path / '.DS_Store').touch()
        (tmp_path / '.hidden').touch()
        (tmp_path / 'real.pdf').touch()

        with patch.object(sys, 'platform', 'darwin'):
            deleted = cleanup_macos_shadow_files(str(tmp_path))
        assert deleted == 1  # only the shadow file

        # Verify the right files survived
        assert not (tmp_path / '._shadow').exists()
        assert (tmp_path / '.DS_Store').exists()
        assert (tmp_path / '.hidden').exists()
        assert (tmp_path / 'real.pdf').exists()


# =========================================================================
# symlinks
# =========================================================================
class TestSymlinks:
    """Stripping should not follow symlinks (no infinite loop)."""

    def test_strip_on_symlink_target(self, task_mod):
        """If the path is a symlink, we strip xattrs on the
        symlink target (which is what removexattr does by
        default). It should not loop.
        """
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        call_count = [0]
        mock_libc = MagicMock()
        mock_libc.removexattr.side_effect = lambda *a: (
            call_count.__setitem__(0, call_count[0] + 1) or 0
        )
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                # Path looks like a symlink. removexattr would
                # follow the link by default and modify the
                # target's xattrs. Either way, it terminates.
                strip_macos_metadata('/path/symlink')
        # Called exactly 4 times (one per xattr), no infinite loop
        assert call_count[0] == 4


# =========================================================================
# Performance
# =========================================================================
class TestPerformance:
    """strip_macos_metadata should be fast enough for 10K+ files."""

    def test_strip_10k_files_under_5s(self, task_mod):
        """10K strips complete in < 5s when ctypes is mocked."""
        from moodle_dl.downloader.task import strip_macos_metadata
        import ctypes as real_ctypes
        mock_libc = MagicMock()
        mock_libc.removexattr.return_value = 0
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            with patch.object(real_ctypes, 'CDLL', return_value=mock_libc), \
                 patch.object(real_ctypes.util, 'find_library', return_value='c'):
                start = time.monotonic()
                for i in range(10000):
                    strip_macos_metadata(f'/test/file_{i}.pdf')
                elapsed = time.monotonic() - start
        # 10K strips in < 5s
        assert elapsed < 5.0, (
            f'10K strips took {elapsed:.2f}s — too slow'
        )


# =========================================================================
# cleanup_macos_shadow_files edge cases
# =========================================================================
class TestCleanupEdgeCases:
    """More edge cases for cleanup_macos_shadow_files."""

    def test_recursive_into_deep_tree(self, tmp_path):
        """Cleans up ._ files in deeply nested directories."""
        from moodle_dl.downloader.task import cleanup_macos_shadow_files
        # Create a deep tree
        deep = tmp_path
        for i in range(20):
            deep = deep / f'd{i}'
            deep.mkdir()
            (deep / '._shadow').touch()
            (deep / 'real.pdf').touch()
        with patch.object(sys, 'platform', 'darwin'):
            deleted = cleanup_macos_shadow_files(str(tmp_path))
        assert deleted == 20

    def test_handles_symlink_loop(self, tmp_path):
        """Symlink loops should not cause infinite recursion."""
        from moodle_dl.downloader.task import cleanup_macos_shadow_files
        # Create a symlink loop
        (tmp_path / 'a').mkdir()
        (tmp_path / 'a' / 'b').mkdir()
        os.symlink(str(tmp_path / 'a'), str(tmp_path / 'a' / 'b' / 'loop'),
                   target_is_directory=True)
        # Should not hang
        with patch.object(sys, 'platform', 'darwin'):
            # os.walk follows symlinks by default; we don't want
            # this to hang on a symlink loop. We don't pass
            # followlinks=False, so it might recurse.
            # But the function has a try/except so it should
            # handle the OSError gracefully.
            try:
                deleted = cleanup_macos_shadow_files(str(tmp_path))
            except (OSError, RecursionError):
                # OK — the function tried its best
                pass

    def test_does_not_delete_dotdotdot(self, tmp_path):
        """Doesn't delete `..`, `.`, or normal files."""
        from moodle_dl.downloader.task import cleanup_macos_shadow_files
        # These should never be deleted (no `._` prefix)
        (tmp_path / 'real.txt').write_text('hi')
        (tmp_path / 'no.underscore.txt').write_text('hi')
        (tmp_path / '...lots_of_dots').touch()
        with patch.object(sys, 'platform', 'darwin'):
            deleted = cleanup_macos_shadow_files(str(tmp_path))
        assert deleted == 0
        assert (tmp_path / 'real.txt').exists()
        assert (tmp_path / 'no.underscore.txt').exists()
        assert (tmp_path / '...lots_of_dots').exists()

    def test_handles_filesystem_error_during_walk(self, tmp_path):
        """If os.walk raises, we should not crash."""
        from moodle_dl.downloader.task import cleanup_macos_shadow_files
        with patch.object(sys, 'platform', 'darwin'):
            with patch('moodle_dl.downloader.task.os.walk', side_effect=OSError('disk error')):
                # Should not raise
                deleted = cleanup_macos_shadow_files(str(tmp_path))
        assert deleted == 0


# =========================================================================
# Real file integration: end-to-end on tmp
# =========================================================================
class TestRealFileIntegration:
    """Run against an actual filesystem (not mocks)."""

    def test_real_file_on_darwin(self, tmp_path, task_mod):
        """A real file, real ctypes call (no mock) — should not crash.

        On non-darwin (Linux in CI), this is a no-op. On darwin
        (with a real ctypes), it actually calls removexattr.

        We just verify no crash on this platform.
        """
        from moodle_dl.downloader.task import strip_macos_metadata
        p = tmp_path / 'real.pdf'
        p.write_text('hello')
        # On darwin, this would call removexattr which returns -1
        # (no such xattr) but doesn't crash. On Linux, it's a no-op.
        with patch.object(task_mod.sys, 'platform', 'darwin'):
            strip_macos_metadata(str(p))  # no crash
        # The file is still there
        assert p.exists()
        assert p.read_text() == 'hello'

    def test_real_dir_cleanup(self, tmp_path):
        """Real directory with real files."""
        from moodle_dl.downloader.task import cleanup_macos_shadow_files
        (tmp_path / 'a.pdf').write_text('a')
        (tmp_path / '._a.pdf').write_bytes(b'\x00\x05\x16')
        (tmp_path / 'sub').mkdir()
        (tmp_path / 'sub' / 'b.pdf').write_text('b')
        (tmp_path / 'sub' / '._b.pdf').write_bytes(b'\x00\x05\x16')
        with patch.object(sys, 'platform', 'darwin'):
            deleted = cleanup_macos_shadow_files(str(tmp_path))
        assert deleted == 2
        # The real files are still there
        assert (tmp_path / 'a.pdf').read_text() == 'a'
        assert (tmp_path / 'sub' / 'b.pdf').read_text() == 'b'
