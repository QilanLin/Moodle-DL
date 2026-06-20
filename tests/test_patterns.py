# -*- coding: utf-8 -*-
"""
Unit tests for moodle_dl.downloader._patterns.

These helpers are used across the downloader subsystem to
consolidate duplicated logic. Pinning their behavior here means
the rest of the codebase can refactor safely.
"""
import os
import sys
import tempfile
from unittest.mock import MagicMock

import pytest

# 🔧 Portability: use __file__ to find the project root, not a
# hardcoded user-specific path. Pytest's conftest.py also adds
# the root, but having it in-file makes this test runnable in
# isolation (e.g. ``python -m unittest``).
import os.path as _path
_ROOT = _path.dirname(_path.dirname(_path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from moodle_dl.downloader._patterns import (
    IncompleteRecord,
    NET_ERRORS,
    cleanup_on_failure,
    ensure_dir,
    ensure_parent_dir,
    part_file_size_or_none,
    query_count,
    safe_remove_part_and_final,
)


# =======================================================================
# safe_remove_part_and_final
# =======================================================================
class TestSafeRemovePartAndFinal:
    def test_removes_both_part_and_final(self, tmp_path):
        """The .part file and the final file are both removed."""
        part = tmp_path / 'foo.pdf.part'
        final = tmp_path / 'foo.pdf'
        part.write_bytes(b'partial data')
        final.write_bytes(b'final data')
        safe_remove_part_and_final(str(final))
        assert not part.exists()
        assert not final.exists()

    def test_works_when_only_part_exists(self, tmp_path):
        """If the final file was never created, only the .part is removed."""
        part = tmp_path / 'foo.pdf.part'
        part.write_bytes(b'partial data')
        safe_remove_part_and_final(str(tmp_path / 'foo.pdf'))
        assert not part.exists()

    def test_works_when_only_final_exists(self, tmp_path):
        """If the .part is missing (already cleaned up), only final removed."""
        final = tmp_path / 'foo.pdf'
        final.write_bytes(b'final')
        safe_remove_part_and_final(str(final))
        assert not final.exists()

    def test_idempotent(self, tmp_path):
        """Calling twice is safe (no exception)."""
        final = tmp_path / 'foo.pdf'
        final.write_bytes(b'x')
        safe_remove_part_and_final(str(final))
        safe_remove_part_and_final(str(final))  # second call
        assert not final.exists()

    def test_uses_injected_remover(self, tmp_path):
        """The remover callable is used (e.g. PT.remove_file)."""
        final = tmp_path / 'foo.pdf'
        final.write_bytes(b'x')
        remover = MagicMock()
        safe_remove_part_and_final(str(final), pt_remove_file=remover)
        # Both paths get passed to the remover
        assert remover.call_count == 2
        paths = [call.args[0] for call in remover.call_args_list]
        assert any(p.endswith('foo.pdf.part') for p in paths)
        assert any(p.endswith('foo.pdf') and not p.endswith('.part') for p in paths)


# =======================================================================
# ensure_parent_dir
# =======================================================================
class TestEnsureParentDir:
    def test_creates_intermediate_dirs(self, tmp_path):
        target = tmp_path / 'a' / 'b' / 'c' / 'file.pdf'
        ensure_parent_dir(str(target))
        assert (tmp_path / 'a' / 'b' / 'c').is_dir()

    def test_idempotent_on_existing(self, tmp_path):
        target = tmp_path / 'a' / 'file.pdf'
        target.parent.mkdir()
        target.parent.mkdir(parents=True, exist_ok=True)
        ensure_parent_dir(str(target))  # should not raise
        assert target.parent.is_dir()

    def test_noop_on_bare_filename(self):
        """A bare filename has no parent — should not raise."""
        ensure_parent_dir('foo.pdf')  # no exception


class TestEnsureDir:
    def test_creates_dir(self, tmp_path):
        target = tmp_path / 'subdir'
        ensure_dir(str(target))
        assert target.is_dir()

    def test_idempotent(self, tmp_path):
        target = tmp_path / 'subdir'
        ensure_dir(str(target))
        ensure_dir(str(target))  # no exception


# =======================================================================
# IncompleteRecord
# =======================================================================
class TestIncompleteRecord:
    def test_construction(self):
        rec = IncompleteRecord(
            file_id=1, file_url='http://x', file_path='/p.pdf',
            downloaded_bytes=100, total_bytes=200,
        )
        assert rec.file_id == 1
        assert rec.file_path == '/p.pdf'
        assert rec.downloaded_bytes == 100
        assert rec.total_bytes == 200
        assert rec.server_supports_range is True  # default

    def test_to_row(self):
        rec = IncompleteRecord(
            file_id=2, file_url='http://x', file_path='/p.pdf',
            downloaded_bytes=42, total_bytes=100,
        )
        row = rec.to_row()
        assert row == {
            'file_id': 2,
            'file_path': '/p.pdf',
            'downloaded_bytes': 42,
            'total_bytes': 100,
        }

    def test_save_calls_recorder(self):
        """save() delegates to the recorder's save_incomplete_download."""
        recorder = MagicMock()
        rec = IncompleteRecord(
            file_id=3, file_url='http://x', file_path='/p.pdf',
            downloaded_bytes=10, total_bytes=20,
        )
        rec.save(recorder)
        recorder.save_incomplete_download.assert_called_once_with(
            file_id=3,
            file_url='http://x',
            file_path='/p.pdf',
            total_bytes=20,
            downloaded_bytes=10,
            server_supports_range=True,
            etag=None,
            last_modified=None,
        )


# =======================================================================
# part_file_size_or_none
# =======================================================================
class TestPartFileSizeOrNone:
    def test_returns_size(self, tmp_path):
        p = tmp_path / 'foo.part'
        p.write_bytes(b'x' * 1000)
        assert part_file_size_or_none(str(p)) == 1000

    def test_returns_none_for_missing(self, tmp_path):
        assert part_file_size_or_none(str(tmp_path / 'missing')) is None

    def test_returns_zero_for_empty(self, tmp_path):
        p = tmp_path / 'empty.part'
        p.write_bytes(b'')
        assert part_file_size_or_none(str(p)) == 0


# =======================================================================
# query_count
# =======================================================================
class TestQueryCount:
    def test_counts_all_rows(self, tmp_db):
        td, recorder = tmp_db
        # No rows yet
        assert query_count(recorder, 'incomplete_downloads') == 0

        # Add a row
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/p.pdf',
            total_bytes=100, downloaded_bytes=50,
        )
        assert query_count(recorder, 'incomplete_downloads') == 1

    def test_where_clause(self, tmp_db):
        td, recorder = tmp_db
        recorder.save_incomplete_download(
            file_id=1, file_url='http://x', file_path='/a.pdf',
            total_bytes=100, downloaded_bytes=50,
        )
        recorder.save_incomplete_download(
            file_id=2, file_url='http://x', file_path='/b.pdf',
            total_bytes=100, downloaded_bytes=50,
        )
        assert query_count(recorder, 'incomplete_downloads', 'file_id = ?', (1,)) == 1
        assert query_count(recorder, 'incomplete_downloads', 'file_id = ?', (2,)) == 1
        assert query_count(recorder, 'incomplete_downloads', 'file_id = ?', (99,)) == 0


# =======================================================================
# cleanup_on_failure
# =======================================================================
class TestCleanupOnFailure:
    def test_normal_exit_no_cleanup(self, tmp_path):
        p = tmp_path / 'foo.part'
        p.write_bytes(b'x')
        with cleanup_on_failure([str(p)]):
            pass
        assert p.exists()  # not cleaned up

    def test_exception_triggers_cleanup(self, tmp_path):
        p = tmp_path / 'foo.part'
        p.write_bytes(b'x')
        with pytest.raises(RuntimeError):
            with cleanup_on_failure([str(p)]):
                raise RuntimeError('simulated')
        assert not p.exists()  # cleaned up

    def test_cleanup_with_injected_remover(self, tmp_path):
        p = tmp_path / 'foo.part'
        p.write_bytes(b'x')
        remover = MagicMock()
        with pytest.raises(ValueError):
            with cleanup_on_failure([str(p)], pt_remove_file=remover):
                raise ValueError('simulated')
        assert remover.call_count == 1
        assert remover.call_args.args[0] == str(p)


# =======================================================================
# NET_ERRORS tuple
# =======================================================================
class TestNetErrors:
    def test_includes_oserror(self):
        """OSError is in NET_ERRORS (network I/O)."""
        assert OSError in NET_ERRORS

    def test_includes_valueerror(self):
        """ValueError is in NET_ERRORS (encoding marker)."""
        assert ValueError in NET_ERRORS
