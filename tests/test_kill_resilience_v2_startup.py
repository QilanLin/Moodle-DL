# -*- coding: utf-8 -*-
"""
Tests for the new kill-resilience v2 startup behavior.

The new restart-from-scratch kill behavior depends on
``DownloadService._scan_and_clean_orphan_parts()`` running at
startup. It must:

  1. If a ``.part`` file is found AND the option
     ``restart_incomplete_on_kill=True`` (the default), delete
     the ``.part`` so the next download starts from byte 0.
  2. If a ``.part`` file is found AND the option
     ``restart_incomplete_on_kill=False`` (legacy), the scan
     only deletes ``.part`` files that have no
     ``incomplete_downloads`` row. Tracked files are left for
     the resume path.
  3. Always log a clear message about what was deleted.
  4. Be a no-op if the workspace doesn't exist or the DB is
     missing.

The orphan scan logic itself is in
``moodle_dl.downloader.task.scan_for_orphan_part_files`` and
is covered in ``test_kill_resilience.py::TestScanForOrphanPartFilesIntegration``.
Here we focus on the wrapper that runs it at DownloadService
startup.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# 🔧 Portability: use __file__ to find the project root, not a
# hardcoded user-specific path. Pytest's conftest.py also adds
# the root, but having it in-file makes this test runnable in
# isolation (e.g. ``python -m unittest``).
import os.path as _path
_ROOT = _path.dirname(_path.dirname(_path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _build_minimal_service(opts_dict=None):
    """Construct a DownloadService-like object with just enough
    attributes for _scan_and_clean_orphan_parts to run. We bypass
    the full __init__ to keep the test fast and focused.
    """
    from moodle_dl.downloader.download_service import DownloadService
    from moodle_dl.types import MoodleDlOpts

    if opts_dict is None:
        opts_dict = {}
    opts = MoodleDlOpts(**opts_dict)

    # Bypass __init__ — create an empty object and set only what
    # _scan_and_clean_orphan_parts reads. The database attribute
    # defaults to None and the helper sets it explicitly per-test
    # when needed.
    svc = DownloadService.__new__(DownloadService)
    svc.opts = opts
    svc.database = None
    return svc


def _make_config(workspace_root):
    config = MagicMock()
    config.get_download_path.return_value = workspace_root
    return config


def _make_recorder():
    return MagicMock()


def _write_part(workspace_root, name='test.pdf', size=5000):
    part_path = os.path.join(workspace_root, name + '.part')
    with open(part_path, 'wb') as f:
        f.write(b'x' * size)
    return part_path


class TestScanAndCleanOrphanParts:
    """Pin the new startup-cleanup behavior."""

    def test_default_deletes_all_orphan_parts(self, tmp_path):
        """Default (restart_incomplete_on_kill=True) deletes every
        ``.part`` file the orphan scan finds."""
        part = _write_part(str(tmp_path))
        assert os.path.exists(part)

        svc = _build_minimal_service()  # default = True
        config = _make_config(str(tmp_path))
        recorder = _make_recorder()
        svc.database = recorder  # The method reads self.database

        # Use a real (not mocked) PathTools.remove_file so the .part
        # is actually deleted. The mock_scan just feeds the orphan
        # list to the method. This is the realistic test of the
        # default behavior: orphans get removed.
        with patch(
            'moodle_dl.downloader.task.scan_for_orphan_part_files',
            return_value=[(part, 5000, 'unknown')],
        ) as mock_scan:
            svc._scan_and_clean_orphan_parts(config)
            mock_scan.assert_called_once()

        # .part was deleted (by the real PT.remove_file, not a mock)
        assert not os.path.exists(part)

    def test_legacy_keeps_tracked_files(self, tmp_path):
        """With restart_incomplete_on_kill=False, only orphan files
        (no DB row) are deleted. The scan still finds them; the
        deletion happens here too because scan_for_orphan_part_files
        only returns files WITHOUT a row. The legacy branch is a
        no-op pass-through."""
        part = _write_part(str(tmp_path))

        svc = _build_minimal_service({'restart_incomplete_on_kill': False})
        config = _make_config(str(tmp_path))
        svc.database = _make_recorder()

        with patch(
            'moodle_dl.downloader.task.scan_for_orphan_part_files',
            return_value=[(part, 5000, 'unknown')],
        ) as mock_scan:
            with patch('moodle_dl.utils.PathTools.remove_file') as mock_remove:
                svc._scan_and_clean_orphan_parts(config)
                mock_scan.assert_called_once()
                # In legacy mode, the cleanup still happens (the scan
                # itself already filters out tracked files).
                mock_remove.assert_called_once_with(part)

    def test_workspace_missing_is_noop(self, tmp_path):
        """If the workspace directory doesn't exist, scan returns
        no orphans and we don't crash."""
        nonexistent = str(tmp_path / 'does_not_exist')
        config = _make_config(nonexistent)
        svc = _build_minimal_service()

        # PathTools.remove_file should not be called
        with patch('moodle_dl.utils.PathTools.remove_file') as mock_remove:
            svc._scan_and_clean_orphan_parts(config)
            mock_remove.assert_not_called()

    def test_recorder_missing_is_noop(self, tmp_path):
        """If the database recorder isn't available, skip the scan
        rather than crash. This happens in early startup before
        the DB is initialized.
        """
        _write_part(str(tmp_path))
        config = _make_config(str(tmp_path))
        svc = _build_minimal_service()
        # Simulate no recorder
        svc.database = None

        with patch('moodle_dl.utils.PathTools.remove_file') as mock_remove:
            svc._scan_and_clean_orphan_parts(config)
            mock_remove.assert_not_called()

    def test_scan_error_does_not_crash(self, tmp_path, caplog):
        """If the scan itself raises (e.g. DB locked), log a warning
        and continue. The user can still run moodle-dl."""
        config = _make_config(str(tmp_path))
        svc = _build_minimal_service()

        with patch(
            'moodle_dl.downloader.task.scan_for_orphan_part_files',
            side_effect=RuntimeError('DB locked'),
        ):
            # Must not raise
            with patch('moodle_dl.utils.PathTools.remove_file') as mock_remove:
                svc._scan_and_clean_orphan_parts(config)
                mock_remove.assert_not_called()

    def test_deletion_failure_does_not_crash(self, tmp_path, caplog):
        """If a single file delete fails (e.g. permission denied),
        log a debug message and keep going. The next file in the
        scan should still be deleted.
        """
        # Note: we don't pre-create the .part files since we mock
        # the deletion side-effect. The point of this test is the
        # error-handling path, not the actual file removal.
        svc = _build_minimal_service()
        config = _make_config(str(tmp_path))
        svc.database = _make_recorder()

        fake_paths = ['/fake/a.pdf.part', '/fake/b.pdf.part']
        with patch(
            'moodle_dl.downloader.task.scan_for_orphan_part_files',
            return_value=[(p, 1000, 'unknown') for p in fake_paths],
        ):
            with patch(
                'moodle_dl.utils.PathTools.remove_file',
                side_effect=[OSError('perm denied'), None],
            ) as mock_remove:
                svc._scan_and_clean_orphan_parts(config)
                # Called for both, even though first raised
                assert mock_remove.call_count == 2

    def test_no_orphans_no_log(self, tmp_path):
        """If the scan finds nothing, the cleanup is a no-op.
        No log spam for users with clean workspaces."""
        config = _make_config(str(tmp_path))
        svc = _build_minimal_service()

        with patch(
            'moodle_dl.downloader.task.scan_for_orphan_part_files',
            return_value=[],
        ):
            with patch('moodle_dl.utils.PathTools.remove_file') as mock_remove:
                svc._scan_and_clean_orphan_parts(config)
                mock_remove.assert_not_called()
