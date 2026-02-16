# -*- coding: utf-8 -*-
"""
失败文件追踪关键路径（Strong Robust Equivalence Class）。
"""

import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.types import File, MoodleDlOpts


class TestFailedTrackingStrongRobustEC(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "moodle_state.db")

        self.config = MagicMock(spec=ConfigHelper)
        self.config.get_misc_files_path.return_value = self.temp_dir
        self.opts = MagicMock(spec=MoodleDlOpts)

        self.db = StateRecorder(self.config, self.opts)
        self.course_id = 42
        self.course_name = "EC Course"

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    @staticmethod
    def _make_file(module_id: int, url: str, saved_to: str = "/tmp/x.pdf") -> File:
        return File(
            module_id=module_id,
            section_name="Week 1",
            section_id=1,
            module_name="Resource",
            content_filepath="/",
            content_filename=f"f_{module_id}.pdf",
            content_fileurl=url,
            content_filesize=123,
            content_timemodified=int(time.time()),
            module_modname="resource",
            content_type="pdf",
            content_isexternalfile=False,
            saved_to=saved_to,
        )

    def test_save_failed_file_error_message_equivalence_classes(self):
        # EC classes:
        # E1 valid normal message
        # E2 robust invalid empty string -> NULL
        # E3 robust invalid None -> NULL
        # E4 boundary > 500 chars -> truncated to 500
        cases = [
            {"name": "normal", "msg": "network timeout", "expect": "network timeout"},
            {"name": "empty", "msg": "", "expect": None},
            {"name": "none", "msg": None, "expect": None},
            {"name": "too-long", "msg": "x" * 700, "expect": "x" * 500},
        ]

        for idx, case in enumerate(cases, start=1):
            with self.subTest(case=case["name"]):
                file_obj = self._make_file(idx, f"https://example.com/{idx}.pdf")
                self.db.save_failed_file(file_obj, self.course_id, self.course_name, case["msg"])

                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT last_failed_reason, download_status, download_attempts, consecutive_failures
                    FROM files WHERE module_id = ? AND content_fileurl = ?
                    """,
                    (file_obj.module_id, file_obj.content_fileurl),
                )
                row = cursor.fetchone()
                conn.close()

                self.assertIsNotNone(row)
                self.assertEqual(row[0], case["expect"])
                self.assertEqual(row[1], "failed")
                self.assertEqual(row[2], 1)
                self.assertEqual(row[3], 1)

    def test_get_failed_files_includes_retrying_even_with_high_min_failures(self):
        failed_file = self._make_file(100, "https://example.com/a.pdf")
        retrying_file = self._make_file(101, "https://example.com/b.pdf")

        self.db.save_failed_file(failed_file, self.course_id, self.course_name, "err1")
        self.db.save_failed_file(retrying_file, self.course_id, self.course_name, "err2")
        self.db.reset_failed_file_for_retry(retrying_file, self.course_id)

        # Strong robust class:
        # min_failures very high should filter failed entries, but retrying must still be included.
        result = self.db.get_failed_files(min_failures=999)
        urls = {f.content_fileurl for f in result}

        self.assertIn(retrying_file.content_fileurl, urls)
        self.assertNotIn(failed_file.content_fileurl, urls)

    def test_mark_download_success_without_existing_record_is_safe(self):
        # Robust invalid class: mark success for non-existing record should not raise.
        new_file = self._make_file(777, "https://example.com/not-exist.pdf")
        self.db.mark_download_success(new_file, self.course_id)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM files WHERE module_id = ? AND content_fileurl = ?",
            (new_file.module_id, new_file.content_fileurl),
        )
        count = cursor.fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
