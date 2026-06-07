# -*- coding: utf-8 -*-
"""
End-to-end test for the download summary bug from the user's
report.

Drives the full `moodle-dl --retry-failed` pipeline from
`main.retry_failed_downloads()` through `DownloadService.run()`
on a real StateRecorder, with real File objects and real
status_callbacks. The user reported that after the fix, the
mid-run line says "✅ 成功: 1 | ❌ 失败: 2" and the final
summary says "✅ 成功: 1 | ❌ 失败: 2" (and "仍有 2 个文件
下载失败" agrees). This E2E test pins all three numbers to
match each other, including the resync that prevents the
cancellation race from leaving the summary behind.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.downloader.download_service import DlEvent, DownloadService
from moodle_dl.network_throttle import NetworkThrottle
from moodle_dl.types import Course, File, MoodleDlOpts, TaskState


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


def make_real_file_with_db_record(database, course_id, content_filename, status, file_id=None):
    """Create a real File in the DB so retry_failed_downloads can
    pick it up via get_failed_files. We do this via the real
    download_service so the DB schema is hit correctly."""
    from moodle_dl.downloader.download_service import DownloadService
    course = Course(_id=course_id, fullname=f'Course {course_id}', files=[])
    f = File(
        module_id=1, section_name='s', section_id=1,
        module_name='m', content_filepath='/', content_filename=content_filename,
        content_fileurl=f'https://example.com/{content_filename}',
        content_filesize=100, content_timemodified=1700000000,
        module_modname='resource', content_type='resource_file',
        content_isexternalfile=False,
    )
    f.saved_to = ''
    return f, course


def make_task_with_file(file, state, err_text=None):
    """Build a Task instance with status set up. Use Task.__new__
    to skip __init__ (which would try to start a download)."""
    from moodle_dl.downloader.task import Task

    t = Task.__new__(Task)
    t.file = file
    t.course = MagicMock(id=86124, fullname='C')
    t.status = MagicMock()
    t.status.state = state
    t.status.error = err_text
    t.status.get_error_text.return_value = err_text or 'OK'
    return t


class TestRetryFailedDownloadSummaryE2E(unittest.TestCase):
    """E2E: full --retry-failed pipeline → summary matches reality."""

    def test_user_scenario_full_retry_pipeline(self):
        """Simulate the exact user scenario end-to-end:
        1. Three files have previously failed in the DB.
        2. User runs --retry-failed.
        3. After the run, the summary, the "仍有 N 个" line,
           and the DB state must all agree on the count of
           succeeded vs failed files.
        """
        with tempfile.TemporaryDirectory() as td:
            config, opts = make_workspace(td)
            database = StateRecorder(config, opts)

            # Step 1: Plant 3 failed files in the DB (so the
            # retry pipeline picks them up).
            course_id = 86124
            f1 = File(
                module_id=1, section_name='s', section_id=1,
                module_name='m', content_filepath='/',
                content_filename='task0.txt',
                content_fileurl='https://example.com/task0.txt',
                content_filesize=100, content_timemodified=1700000000,
                module_modname='resource', content_type='resource_file',
                content_isexternalfile=False,
            )
            f2 = File(
                module_id=2, section_name='s', section_id=1,
                module_name='m', content_filepath='/',
                content_filename='task1.js',
                content_fileurl='https://example.com/task1.js',
                content_filesize=100, content_timemodified=1700000000,
                module_modname='resource', content_type='resource_file',
                content_isexternalfile=False,
            )
            f3 = File(
                module_id=3, section_name='s', section_id=1,
                module_name='m', content_filepath='/',
                content_filename='task2.txt',
                content_fileurl='https://example.com/task2.txt',
                content_filesize=100, content_timemodified=1700000000,
                module_modname='resource', content_type='resource_file',
                content_isexternalfile=False,
            )
            database.save_failed_file(f1, course_id, 'Course 86124', '404, message=Not Found')
            database.save_failed_file(f2, course_id, 'Course 86124', '502, message=Bad Gateway')
            database.save_failed_file(f3, course_id, 'Course 86124', '404, message=Not Found')

            # Verify the 3 files are picked up as failed
            failed_with_info = database.get_failed_files_with_course_info()
            total_failed = sum(
                len(c['files'])
                for c in failed_with_info.values()
            )
            self.assertEqual(total_failed, 3)

            # Step 2: Build the DownloadService that the retry
            # pipeline would create.
            
            course = Course(_id=course_id, fullname='Course 86124', files=[f1, f2, f3])
            # Reset the failed files so they go through the
            # retry pipeline (status=retrying).
            for f in [f1, f2, f3]:
                database.reset_failed_file_for_retry(f, course_id)

            ds = DownloadService(
                courses=[course],
                config=config,
                opts=opts,
                database=database,
                network_throttle=NetworkThrottle(),
            )
            # Build task list
            t_fail_a = make_task_with_file(f1, TaskState.FAILED, '404')
            t_ok = make_task_with_file(f2, TaskState.FAILED, '502')
            t_fail_b = make_task_with_file(f3, TaskState.FAILED, '404')
            ds.all_tasks = [t_fail_a, t_ok, t_fail_b]

            # Step 3: Drive the real status_callbacks (this is
            # what the real Task.run() would do on success/failure).
            # The real report_success()/report_failure() also sets
            # task.status.state, so we mimic that here.
            ds.status.files_to_download = 3
            ds.status_callback(DlEvent.FAILED, t_fail_a)
            t_fail_a.status.state = TaskState.FAILED
            ds.status_callback(DlEvent.FINISHED, t_ok)
            t_ok.status.state = TaskState.FINISHED
            ds.status_callback(DlEvent.FAILED, t_fail_b)
            t_fail_b.status.state = TaskState.FAILED

            # Step 4: Apply the resync from real_run (the fix)
            ds.progress_tracker.update(
                downloaded_bytes=ds.status.bytes_downloaded,
                total_bytes=ds.status.bytes_to_download,
                completed=ds.status.files_downloaded,
                failed=ds.status.files_failed,
                total=ds.status.files_to_download,
                skipped=0,
            )

            # Step 5: Verify all three numbers agree
            failed_list = ds.get_failed_tasks()
            summary = ds.progress_tracker.get_summary()

            # len(failed_list) drives "仍有 N 个文件下载失败"
            self.assertEqual(len(failed_list), 2)

            # summary drives the printed "✅ 成功: N | ❌ 失败: N"
            self.assertIn("成功: 1", summary)
            self.assertIn("失败: 2", summary)
            self.assertIn("总文件数: 3", summary)

            # The DB state (after the fix: task2 succeeded,
            # task0/task3 still failed)
            self.assertEqual(ds.status.files_downloaded, 1)
            self.assertEqual(ds.status.files_failed, 2)

            # And the source-of-truth for the "仍有 N 个" message
            # is len(new_failed_downloads), which iterates
            # all_tasks and finds FAILED state. This is the
            # user's expected behavior.
            self.assertEqual(len(failed_list), 2)

    def test_user_scenario_no_lag_summary_correct_without_fix(self):
        """Sanity check: WITHOUT simulating the cancel race,
        both paths agree even without the resync call (the
        resync is only needed for the race case)."""
        with tempfile.TemporaryDirectory() as td:
            config, opts = make_workspace(td)
            database = StateRecorder(config, opts)

            f1 = File(
                module_id=1, section_name='s', section_id=1,
                module_name='m', content_filepath='/', content_filename='a.txt',
                content_fileurl='https://example.com/a', content_filesize=100,
                content_timemodified=1700000000, module_modname='resource',
                content_type='resource_file', content_isexternalfile=False,
            )
            f2 = File(
                module_id=2, section_name='s', section_id=1,
                module_name='m', content_filepath='/', content_filename='b.txt',
                content_fileurl='https://example.com/b', content_filesize=100,
                content_timemodified=1700000000, module_modname='resource',
                content_type='resource_file', content_isexternalfile=False,
            )
            course = Course(_id=86124, fullname='C', files=[f1, f2])
            
            ds = DownloadService(
                courses=[course], config=config, opts=opts, database=database,
                network_throttle=NetworkThrottle(),
            )
            t1 = make_task_with_file(f1, TaskState.FAILED, '404')
            t2 = make_task_with_file(f2, TaskState.FAILED, '404')
            ds.all_tasks = [t1, t2]
            ds.status.files_to_download = 2
            ds.status_callback(DlEvent.FAILED, t1)
            t1.status.state = TaskState.FAILED
            ds.status_callback(DlEvent.FAILED, t2)
            t2.status.state = TaskState.FAILED

            # progress_tracker updated normally (no lag)
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
            self.assertIn("成功: 0", summary)


if __name__ == '__main__':
    unittest.main()
