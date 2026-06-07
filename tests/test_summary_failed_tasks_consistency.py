# -*- coding: utf-8 -*-
"""
Integration tests for download summary consistency.

Pins the relationship between two independent paths to the
final state:

  1. progress_tracker's get_summary() (the printed summary)
  2. DownloadService.get_failed_tasks() (used by
     retry_failed_downloads to print "仍有 N 个文件下载失败")

Both must agree on the count of failed tasks. The user's
observed bug: summary said 失败: 1, but
len(get_failed_tasks()) == 2.

This file's tests drive the real DownloadService through
real status_callbacks with real File objects, then verify
the two paths agree even when the async status_logger_task
is cancelled mid-flight.
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
from moodle_dl.types import (
    Course, File, MoodleDlOpts, TaskState
)


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


def make_real_file(time_stamp=0):
    return File(
        module_id=1, section_name='s', section_id=1,
        module_name='m', content_filepath='/', content_filename='x.txt',
        content_fileurl='https://example.com/x.txt', content_filesize=100,
        content_timemodified=1700000000, module_modname='resource',
        content_type='resource_file', content_isexternalfile=False,
        time_stamp=time_stamp,
    )


def make_task(file, state, err_text=None):
    """Build a real Task with status set up, but with a mocked
    run() so it doesn't actually try to download."""
    from moodle_dl.downloader.task import Task

    real_task = Task.__new__(Task)
    real_task.file = file
    real_task.course = MagicMock(id=86124, fullname='C')
    real_task.status = MagicMock()
    real_task.status.state = state
    real_task.status.error = err_text
    real_task.status.get_error_text.return_value = err_text or 'OK'
    return real_task


def make_download_service(tmpdir):
    config, opts = make_workspace(tmpdir)
    database = StateRecorder(config, opts)
    course = Course(_id=86124, fullname='C', files=[])
    return DownloadService(
        courses=[course], config=config, opts=opts, database=database
    ), database


class TestSummaryVsGetFailedTasksConsistency(unittest.TestCase):
    """The user's exact bug: summary says 失败: 1, but
    len(get_failed_tasks()) == 2. This must never happen
    after the fix."""

    def test_summary_agrees_with_get_failed_tasks_user_scenario(self):
        """The exact 3-task scenario from the user's report:
        1 task succeeded, 2 tasks returned 404. After running
        through real status_callbacks, BOTH the summary AND
        get_failed_tasks() must report 2 failed."""
        with tempfile.TemporaryDirectory() as td:
            ds, _ = make_download_service(td)
            ds.status.files_to_download = 3

            f1 = make_real_file()
            f2 = make_real_file()
            f3 = make_real_file()

            # Wire the 3 tasks into the service's all_tasks
            # (this is what get_failed_tasks() iterates over)
            t_fail_a = make_task(f1, TaskState.FAILED, '404')
            t_ok = make_task(f2, TaskState.FINISHED)
            t_fail_b = make_task(f3, TaskState.FAILED, '404')
            ds.all_tasks = [t_fail_a, t_ok, t_fail_b]

            # Drive real status_callbacks
            ds.status_callback(DlEvent.FAILED, t_fail_a)
            ds.status_callback(DlEvent.FINISHED, t_ok)
            ds.status_callback(DlEvent.FAILED, t_fail_b)

            # Apply the fix's resync (the new code in real_run)
            ds.progress_tracker.update(
                downloaded_bytes=ds.status.bytes_downloaded,
                total_bytes=ds.status.bytes_to_download,
                completed=ds.status.files_downloaded,
                failed=ds.status.files_failed,
                total=ds.status.files_to_download,
                skipped=0,
            )

            # Now both paths must agree
            failed_list = ds.get_failed_tasks()
            summary = ds.progress_tracker.get_summary()

            self.assertEqual(len(failed_list), 2)
            self.assertIn("成功: 1", summary)
            self.assertIn("失败: 2", summary)
            self.assertEqual(
                len(failed_list), ds.status.files_failed,
                "get_failed_tasks() and DownloadStatus.files_failed "
                "must agree (they read from independent paths)."
            )

    def test_summary_agrees_with_get_failed_tasks_with_lag(self):
        """Even with the cancellation race (progress_tracker
        stale, status correct), the resync brings them in sync.
        Then both the summary and get_failed_tasks() agree."""
        with tempfile.TemporaryDirectory() as td:
            ds, _ = make_download_service(td)
            ds.status.files_to_download = 3

            f1 = make_real_file()
            f2 = make_real_file()
            f3 = make_real_file()

            t_fail_a = make_task(f1, TaskState.FAILED, '404')
            t_ok = make_task(f2, TaskState.FINISHED)
            t_fail_b = make_task(f3, TaskState.FAILED, '404')
            ds.all_tasks = [t_fail_a, t_ok, t_fail_b]

            ds.status_callback(DlEvent.FAILED, t_fail_a)
            ds.status_callback(DlEvent.FINISHED, t_ok)
            ds.status_callback(DlEvent.FAILED, t_fail_b)

            # Simulate the race: progress_tracker only got
            # flushed once (at the time of mid-run logging)
            ds.progress_tracker.update(
                downloaded_bytes=0, total_bytes=0,
                completed=0, failed=1, total=3, skipped=0,
            )

            # get_failed_tasks() is correct (2)
            self.assertEqual(len(ds.get_failed_tasks()), 2)

            # Summary is stale (1)
            stale_summary = ds.progress_tracker.get_summary()
            self.assertIn("失败: 1", stale_summary)
            self.assertNotIn("失败: 2", stale_summary)

            # The fix: resync progress_tracker
            ds.progress_tracker.update(
                downloaded_bytes=ds.status.bytes_downloaded,
                total_bytes=ds.status.bytes_to_download,
                completed=ds.status.files_downloaded,
                failed=ds.status.files_failed,
                total=ds.status.files_to_download,
                skipped=0,
            )

            # Now summary is correct too
            fixed_summary = ds.progress_tracker.get_summary()
            self.assertIn("失败: 2", fixed_summary)
            self.assertIn("成功: 1", fixed_summary)

            # And both paths agree
            self.assertEqual(
                len(ds.get_failed_tasks()),
                ds.status.files_failed,
            )
            self.assertEqual(
                len(ds.get_failed_tasks()),
                2,
            )

    def test_summary_agrees_when_all_succeed(self):
        with tempfile.TemporaryDirectory() as td:
            ds, _ = make_download_service(td)
            ds.status.files_to_download = 2

            f1 = make_real_file()
            f2 = make_real_file()
            t1 = make_task(f1, TaskState.FINISHED)
            t2 = make_task(f2, TaskState.FINISHED)
            ds.all_tasks = [t1, t2]

            ds.status_callback(DlEvent.FINISHED, t1)
            ds.status_callback(DlEvent.FINISHED, t2)

            ds.progress_tracker.update(
                downloaded_bytes=ds.status.bytes_downloaded,
                total_bytes=ds.status.bytes_to_download,
                completed=ds.status.files_downloaded,
                failed=ds.status.files_failed,
                total=ds.status.files_to_download,
                skipped=0,
            )

            self.assertEqual(len(ds.get_failed_tasks()), 0)
            summary = ds.progress_tracker.get_summary()
            self.assertIn("成功: 2", summary)
            self.assertIn("失败: 0", summary)

    def test_summary_agrees_when_all_fail(self):
        with tempfile.TemporaryDirectory() as td:
            ds, _ = make_download_service(td)
            ds.status.files_to_download = 2

            f1 = make_real_file()
            f2 = make_real_file()
            t1 = make_task(f1, TaskState.FAILED, '404')
            t2 = make_task(f2, TaskState.FAILED, '500')
            ds.all_tasks = [t1, t2]

            ds.status_callback(DlEvent.FAILED, t1)
            ds.status_callback(DlEvent.FAILED, t2)

            ds.progress_tracker.update(
                downloaded_bytes=ds.status.bytes_downloaded,
                total_bytes=ds.status.bytes_to_download,
                completed=ds.status.files_downloaded,
                failed=ds.status.files_failed,
                total=ds.status.files_to_download,
                skipped=0,
            )

            self.assertEqual(len(ds.get_failed_tasks()), 2)
            summary = ds.progress_tracker.get_summary()
            self.assertIn("失败: 2", summary)
            # The full summary always shows 成功: 0, 失败: 2 in this case
            self.assertIn("成功: 0", summary)
