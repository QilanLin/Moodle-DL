# -*- coding: utf-8 -*-
"""
Regression tests for the download status counter accuracy bug.

The user observed that a `--retry-failed` run on 3 failed files
(1 succeeded after internal retry, 2 returned 404) printed:

  ✅ 成功: 1
  ❌ 失败: 1   <-- BUG: should be 2
  ...
  重试完成，仍有 2 个文件下载失败。  <-- this was correct

The discrepancy was caused by the status_logger_task (an async
background task) being cancelled in the finally block of
real_run BEFORE it had a chance to flush the final counter
values into progress_tracker. The summary was then read from
progress_tracker.failed_files, which lagged by one update.

The fix re-syncs progress_tracker with the authoritative
DownloadStatus values right before reading the summary.

Pin points:
  1. 3 tasks (1 success, 2 fail) → summary shows 成功: 1, 失败: 2
  2. 1 task (1 success) → summary shows 成功: 1, 失败: 0
  3. 1 task (1 fail) → summary shows 成功: 0, 失败: 1
  4. The cancellation race must not leave the summary behind
     the true DownloadStatus.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock

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
    cfg_path = os.path.join(tmpdir, "config.json")
    with open(cfg_path, "w") as f:
        json.dump({
            "moodle_domain": "keats.kcl.ac.uk",
            "moodle_path": "/",
            "token": "fake_token_64_chars_long_for_validation_purposes_xxxxxxxxxxxx",
            "download_options": {field: True for field in ALL_DOWNLOAD_OPTS},
        }, f)
    opts = MoodleDlOpts()
    opts.path = tmpdir
    config = ConfigHelper(opts)  # default validate_db=True
    config.load()
    return config, opts


def make_real_file():
    """A real File with all fields set, so save_file can succeed
    (no :time_stamp binding error)."""
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


class TestSummaryCounterAccuracy(unittest.TestCase):
    """Direct status_callback counter tests with REAL File objects."""

    def test_one_failed_task_increments_files_failed(self):
        with tempfile.TemporaryDirectory() as td:
            ds, _ = make_download_service(td)
            ds.status.files_to_download = 1
            f = make_real_file()
            t = make_task_mock(f, 'FAILED', '404')
            ds.status_callback(DlEvent.FAILED, t)
            self.assertEqual(ds.status.files_failed, 1)
            self.assertEqual(ds.status.files_downloaded, 0)

    def test_one_finished_task_increments_files_downloaded(self):
        with tempfile.TemporaryDirectory() as td:
            ds, _ = make_download_service(td)
            ds.status.files_to_download = 1
            f = make_real_file()
            t = make_task_mock(f, 'FINISHED', None)
            ds.status_callback(DlEvent.FINISHED, t)
            self.assertEqual(ds.status.files_downloaded, 1)
            self.assertEqual(ds.status.files_failed, 0)

    def test_three_tasks_2_fail_1_succeed(self):
        """The exact user scenario: 1 task FINISHED, 2 FAILED.
        After all 3 callbacks, status and summary must show
        files_downloaded=1, files_failed=2."""
        with tempfile.TemporaryDirectory() as td:
            ds, _ = make_download_service(td)
            ds.status.files_to_download = 3
            f = make_real_file()

            ds.status_callback(DlEvent.FAILED, make_task_mock(f, 'FAILED', '404'))
            ds.status_callback(DlEvent.FINISHED, make_task_mock(f, 'FINISHED', None))
            ds.status_callback(DlEvent.FAILED, make_task_mock(f, 'FAILED', '404'))

            # Authoritative source
            self.assertEqual(ds.status.files_failed, 2)
            self.assertEqual(ds.status.files_downloaded, 1)

            # Now simulate the bug: progress_tracker has stale values
            # (only the first FAIL was flushed to it)
            ds.progress_tracker.update(
                downloaded_bytes=0, total_bytes=0,
                completed=0, failed=1, total=3, skipped=0,
            )

            # Without the fix, get_summary() would show 失败: 1
            stale_summary = ds.progress_tracker.get_summary()
            self.assertIn("失败: 1", stale_summary)

            # With the fix (the update() call we just made right
            # before _display_download_summary in download_service),
            # the values are now correct.
            ds.progress_tracker.update(
                downloaded_bytes=ds.status.bytes_downloaded,
                total_bytes=ds.status.bytes_to_download,
                completed=ds.status.files_downloaded,
                failed=ds.status.files_failed,
                total=ds.status.files_to_download,
                skipped=0,
            )
            synced_summary = ds.progress_tracker.get_summary()
            self.assertIn("成功: 1", synced_summary)
            self.assertIn("失败: 2", synced_summary)
            self.assertIn("总文件数: 3", synced_summary)


class TestSummaryLagsBehindStatus(unittest.TestCase):
    """Reproduces the user's exact bug: status.failed=2 but
    progress_tracker.failed=1 (the value at the last successful
    update before cancel).

    The fix is to re-sync progress_tracker with status
    immediately before reading the summary.
    """

    def test_summary_consistent_with_status_after_lag(self):
        with tempfile.TemporaryDirectory() as td:
            ds, _ = make_download_service(td)
            ds.status.files_to_download = 3
            f = make_real_file()

            # 3 callbacks
            ds.status_callback(DlEvent.FAILED, make_task_mock(f, 'FAILED', '404'))
            ds.status_callback(DlEvent.FINISHED, make_task_mock(f, 'FINISHED', None))
            ds.status_callback(DlEvent.FAILED, make_task_mock(f, 'FAILED', '404'))

            # Simulate: status_logger_task only flushed the first
            # fail (at the time of mid-run logging).
            ds.progress_tracker.update(
                downloaded_bytes=0, total_bytes=0,
                completed=0, failed=1, total=3, skipped=0,
            )

            # The status (authoritative) is 2 failed, 1 success.
            # The progress_tracker (cached) shows 1 failed, 0 success.
            self.assertEqual(ds.status.files_failed, 2)
            self.assertEqual(ds.status.files_downloaded, 1)
            self.assertEqual(ds.progress_tracker.failed_files, 1)
            self.assertEqual(ds.progress_tracker.completed_files, 0)

            # Before the fix: the summary would lag.
            # After the fix: the resync call (now part of
            # _display_download_summary) brings them in sync.
            ds.progress_tracker.update(
                downloaded_bytes=ds.status.bytes_downloaded,
                total_bytes=ds.status.bytes_to_download,
                completed=ds.status.files_downloaded,
                failed=ds.status.files_failed,
                total=ds.status.files_to_download,
                skipped=0,
            )
            final_summary = ds.progress_tracker.get_summary()
            self.assertIn("成功: 1", final_summary)
            self.assertIn("失败: 2", final_summary)


if __name__ == '__main__':
    unittest.main()
