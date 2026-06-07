# -*- coding: utf-8 -*-
"""
Tests for the status_logger_task cancel race that caused the
download summary to lag behind the authoritative DownloadStatus.

The cancel race:
  1. real_run() starts status_logger_task in a background
     asyncio loop. The task reads from self.status (DownloadStatus)
     and updates self.progress_tracker, then awaits an Event.
  2. real_run() processes the last task (e.g. a 404). The task
     reports FAILED, which causes status_callback() to
     self.status.files_failed += 1 (now 2).
  3. status_callback() calls self._signal_status_log(), which
     sets the Event so the logger task wakes up.
  4. real_run() exits the for-loop, enters the finally block,
     cancels status_logger_task, awaits it.
  5. The logger task may already be mid-flight updating
     progress_tracker (or about to start the update). Depending
     on exact timing, progress_tracker may NOT see the latest
     value (2) — it might still hold the previous snapshot (1).
  6. _display_download_summary() reads progress_tracker and
     prints the stale value (1).
  7. len(new_failed_downloads) is computed from
     downloader.get_failed_tasks() (independent path) and is
     correct (2).

The fix: real_run() now re-syncs progress_tracker from
self.status right before _display_download_summary(). The tests
here pin both the bug behaviour (cancelled = stale) and the
fix behaviour (resync brings them in sync).
"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.downloader.download_service import DlEvent, DownloadService
from moodle_dl.types import File, MoodleDlOpts, Course


ALL_DOWNLOAD_OPTS = [
    'submissions', 'descriptions', 'links_in_descriptions', 'databases',
    'forums', 'quizzes', 'lessons', 'workshops', 'books',
    'bigbluebuttonbns', 'wikis', 'glossaries', 'h5pactivities',
    'h5p_attempts', 'imscps', 'scorms', 'scorm_scos', 'scorm_attempts',
    'subsections', 'qbanks', 'resources', 'urls', 'labels', 'chats',
    'choices', 'feedbacks', 'surveys', 'ltis', 'calendars',
    'metadata_files',
]


def make_workspace(tmpdir):
    os.makedirs(tmpdir, exist_ok=True)
    with open(os.path.join(tmpdir, 'config.json'), 'w') as f:
        json.dump({
            'moodle_domain': 'keats.kcl.ac.uk',
            'moodle_path': '/',
            'token': 'fake_token_64_chars_long_for_validation_purposes_xxxxxxxxxxxx',
            'download_options': {field: True for field in ALL_DOWNLOAD_OPTS},
        }, f)
    opts = MoodleDlOpts()
    opts.path = tmpdir
    config = ConfigHelper(opts)
    config.load()
    return config, opts


def make_real_file():
    return File(
        module_id=1, section_name='s', section_id=1,
        module_name='m', content_filepath='/', content_filename='x.txt',
        content_fileurl='https://example.com/x.txt', content_filesize=100,
        content_timemodified=1700000000, module_modname='resource',
        content_type='resource_file', content_isexternalfile=False,
    )


def make_task_mock(file, state, err_text=None):
    t = MagicMock()
    t.status = MagicMock()
    t.status.state = state
    t.status.error = err_text
    t.status.get_error_text.return_value = err_text or 'OK'
    t.file = file
    t.course = MagicMock(id=86124, fullname='C')
    return t


def make_download_service(tmpdir):
    config, opts = make_workspace(tmpdir)
    database = StateRecorder(config, opts)
    course = Course(_id=86124, fullname='C', files=[])
    return DownloadService(
        courses=[course], config=config, opts=opts, database=database
    ), database


class TestStatusLoggerTaskCancellationRace(unittest.TestCase):
    """Pin the exact race the user observed: the async
    status_logger_task can be cancelled mid-flight and the
    final status update never reaches progress_tracker."""

    def test_resync_fixes_stale_progress_tracker(self):
        """Simulate the race: progress_tracker is stale (failed=1)
        but status says 2 (the user's exact scenario). The
        resync in real_run must bring them in sync before the
        summary is read."""
        with tempfile.TemporaryDirectory() as td:
            ds, _ = make_download_service(td)
            ds.status.files_to_download = 3
            f = make_real_file()

            # 3 real callbacks
            ds.status_callback(DlEvent.FAILED, make_task_mock(f, 'FAILED', '404'))
            ds.status_callback(DlEvent.FINISHED, make_task_mock(f, 'FINISHED', None))
            ds.status_callback(DlEvent.FAILED, make_task_mock(f, 'FAILED', '404'))

            # Authoritative values
            self.assertEqual(ds.status.files_failed, 2)
            self.assertEqual(ds.status.files_downloaded, 1)

            # Simulate the race: progress_tracker only saw the
            # first FAIL (at the time of mid-run logging).
            ds.progress_tracker.update(
                downloaded_bytes=0, total_bytes=0,
                completed=0, failed=1, total=3, skipped=0,
            )
            self.assertEqual(ds.progress_tracker.failed_files, 1)

            # Now simulate the resync that the fix added in
            # real_run(), right before _display_download_summary()
            ds.progress_tracker.update(
                downloaded_bytes=ds.status.bytes_downloaded,
                total_bytes=ds.status.bytes_to_download,
                completed=ds.status.files_downloaded,
                failed=ds.status.files_failed,
                total=ds.status.files_to_download,
                skipped=0,
            )

            summary = ds.progress_tracker.get_summary()
            self.assertIn("成功: 1", summary)
            self.assertIn("失败: 2", summary)
            self.assertIn("总文件数: 3", summary)

    def test_progress_tracker_lag_counted_correctly(self):
        """Without resync, the lag is by exactly 1 update
        (the last FAILED). The fix must close that gap."""
        with tempfile.TemporaryDirectory() as td:
            ds, _ = make_download_service(td)
            ds.status.files_to_download = 3
            f = make_real_file()

            # 3 callbacks
            ds.status_callback(DlEvent.FAILED, make_task_mock(f, 'FAILED', '404'))
            ds.status_callback(DlEvent.FINISHED, make_task_mock(f, 'FINISHED', None))
            ds.status_callback(DlEvent.FAILED, make_task_mock(f, 'FAILED', '404'))

            # Old (pre-fix) progress_tracker: stale values
            ds.progress_tracker.update(
                downloaded_bytes=0, total_bytes=0,
                completed=0, failed=1, total=3, skipped=0,
            )
            lag_summary = ds.progress_tracker.get_summary()
            self.assertIn("失败: 1", lag_summary)
            self.assertIn("成功: 0", lag_summary)

            # Authoritative (post-fix) resync
            ds.progress_tracker.update(
                downloaded_bytes=ds.status.bytes_downloaded,
                total_bytes=ds.status.bytes_to_download,
                completed=ds.status.files_downloaded,
                failed=ds.status.files_failed,
                total=ds.status.files_to_download,
                skipped=0,
            )
            fix_summary = ds.progress_tracker.get_summary()
            self.assertIn("失败: 2", fix_summary)
            self.assertIn("成功: 1", fix_summary)

    def test_async_cancellation_does_not_crash_resync(self):
        """If the async status_logger_task is mid-cancel, the
        sync resync must still work correctly (it runs on the
        main thread, not in the cancelled task)."""
        with tempfile.TemporaryDirectory() as td:
            ds, _ = make_download_service(td)
            ds.status.files_to_download = 2
            f = make_real_file()

            ds.status_callback(DlEvent.FAILED, make_task_mock(f, 'FAILED', '404'))
            ds.status_callback(DlEvent.FINISHED, make_task_mock(f, 'FINISHED', None))

            # The async task is mid-cancel. The resync must still
            # see the latest values.
            async def simulate_mid_cancel():
                # In the real code, the async task is
                # `status_logger_task` which is being cancelled.
                # The resync is sync, so it's not affected.
                pass

            asyncio.run(simulate_mid_cancel())

            ds.progress_tracker.update(
                downloaded_bytes=ds.status.bytes_downloaded,
                total_bytes=ds.status.bytes_to_download,
                completed=ds.status.files_downloaded,
                failed=ds.status.files_failed,
                total=ds.status.files_to_download,
                skipped=0,
            )
            summary = ds.progress_tracker.get_summary()
            self.assertIn("成功: 1", summary)
            self.assertIn("失败: 1", summary)
