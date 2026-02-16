# -*- coding: utf-8 -*-
"""
关键路径测试（Strong Robust Equivalence Class）。

目标：
1. 覆盖关键分支的有效等价类与无效等价类。
2. 对 None/空值/类型边界做强健壮校验。
"""

import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

from moodle_dl.downloader.task import Task
from moodle_dl.moodle.result_builder import ResultBuilder
from moodle_dl.types import Course, DownloadOptions, File, MoodleDlOpts, MoodleURL


class TestResultBuilderStrongRobustEC(unittest.TestCase):
    def setUp(self):
        moodle_url = MoodleURL(use_http=False, domain="keats.kcl.ac.uk", path="/")
        self.builder = ResultBuilder(
            moodle_url=moodle_url,
            version=2024010100,
            mod_plurals={},
            token="token_abc",
        )
        self.location = {
            "section_id": 1,
            "section_name": "General",
            "module_id": 100,
            "module_name": "Module Name",
            "module_modname": "book",
        }

    def test_book_structure_normalization_strong_robust_classes(self):
        # Strong robust EC for structure normalization condition:
        # C1: module_modname == "book"
        # C2: filename == "structure"
        # C3: content exists (not None)
        # C4: normalized fileurl == ""
        # C5: content_type != "content"
        cases = [
            {
                "name": "valid-book-structure-with-fileurl-none",
                "module_modname": "book",
                "input": {
                    "type": "file",
                    "filename": "structure",
                    "filepath": "/",
                    "fileurl": None,
                    "content": "[]",
                    "filesize": 0,
                    "timemodified": 1,
                },
                "expected_type": "content",
            },
            {
                "name": "valid-book-structure-with-empty-fileurl",
                "module_modname": "book",
                "input": {
                    "type": "file",
                    "filename": "structure",
                    "filepath": "/",
                    "fileurl": "",
                    "content": "[]",
                    "filesize": 0,
                    "timemodified": 1,
                },
                "expected_type": "content",
            },
            {
                "name": "invalid-not-book-module",
                "module_modname": "resource",
                "input": {
                    "type": "file",
                    "filename": "structure",
                    "filepath": "/",
                    "fileurl": None,
                    "content": "[]",
                    "filesize": 0,
                    "timemodified": 1,
                },
                "expected_type": "file",
            },
            {
                "name": "invalid-not-structure-filename",
                "module_modname": "book",
                "input": {
                    "type": "file",
                    "filename": "index.html",
                    "filepath": "/",
                    "fileurl": None,
                    "content": "[]",
                    "filesize": 0,
                    "timemodified": 1,
                },
                "expected_type": "file",
            },
            {
                "name": "invalid-content-is-none",
                "module_modname": "book",
                "input": {
                    "type": "file",
                    "filename": "structure",
                    "filepath": "/",
                    "fileurl": None,
                    "content": None,
                    "filesize": 0,
                    "timemodified": 1,
                },
                "expected_type": "file",
            },
            {
                "name": "invalid-content-type-already-content",
                "module_modname": "book",
                "input": {
                    "type": "content",
                    "filename": "structure",
                    "filepath": "/",
                    "fileurl": None,
                    "content": "[]",
                    "filesize": 0,
                    "timemodified": 1,
                },
                "expected_type": "content",
            },
            {
                "name": "invalid-fileurl-non-empty",
                "module_modname": "book",
                "input": {
                    "type": "file",
                    "filename": "structure",
                    "filepath": "/",
                    "fileurl": "https://keats.kcl.ac.uk/pluginfile.php/abc",
                    "content": "[]",
                    "filesize": 0,
                    "timemodified": 1,
                },
                "expected_type": "file",
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                location = dict(self.location)
                location["module_modname"] = case["module_modname"]
                files = self.builder._handle_files([case["input"]], **location)
                self.assertEqual(len(files), 1)
                self.assertEqual(files[0].content_type, case["expected_type"])
                # Robustness requirement: None fileurl must be normalized to empty string.
                if case["input"]["fileurl"] is None:
                    self.assertEqual(files[0].content_fileurl, "")

    def test_pluginfile_fix_only_for_pluginfile_urls(self):
        cases = [
            {
                "name": "valid-pluginfile",
                "fileurl": "https://keats.kcl.ac.uk/pluginfile.php/1/mod_resource/content/1/a.pdf",
                "should_call_fix": True,
            },
            {
                "name": "invalid-empty-url",
                "fileurl": "",
                "should_call_fix": False,
            },
            {
                "name": "invalid-none-url",
                "fileurl": None,
                "should_call_fix": False,
            },
            {
                "name": "valid-non-pluginfile-url",
                "fileurl": "https://keats.kcl.ac.uk/mod/resource/view.php?id=1",
                "should_call_fix": False,
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                content = {
                    "type": "file",
                    "filename": "x.pdf",
                    "filepath": "/",
                    "fileurl": case["fileurl"],
                    "content": "",
                    "filesize": 1,
                    "timemodified": 1,
                }
                with patch("moodle_dl.moodle.result_builder.UrlHelper.fix_pluginfile_url") as mock_fix:
                    mock_fix.return_value = "https://keats.kcl.ac.uk/webservice/pluginfile.php/1/x.pdf?token=token_abc&offline=1"
                    self.builder._handle_files([content], **self.location)
                    if case["should_call_fix"]:
                        mock_fix.assert_called_once()
                    else:
                        mock_fix.assert_not_called()


class TestTaskStrongRobustEC(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.thread_pool = ThreadPoolExecutor(max_workers=1)
        self.course = Course(1, "EC Course")
        self.opts = MoodleDlOpts()
        self.download_opts = DownloadOptions(
            token="token_x",
            moodle_url="https://keats.kcl.ac.uk",
            download_linked_files=False,
            download_domains_whitelist=[],
            download_domains_blacklist=[],
            cookies_text="",
            yt_dlp_options={},
            video_passwords={},
            external_file_downloaders={},
            restricted_filenames=False,
            write_links={},
            download_path="/tmp/moodle-dl-tests",
            download_metadata_files=True,
            global_opts=self.opts,
        )

    def tearDown(self):
        self.thread_pool.shutdown(wait=False)

    def _new_task(self, fileurl: str) -> Task:
        file_obj = File(
            module_id=9,
            section_name="Week 1",
            section_id=1,
            module_name="Resource",
            content_filepath="/",
            content_filename="a.pdf",
            content_fileurl=fileurl,
            content_filesize=0,
            content_timemodified=1,
            module_modname="resource",
            content_type="file",
            content_isexternalfile=False,
        )
        return Task(1, file_obj, self.course, self.download_opts, self.thread_pool, lambda *a, **k: None)

    async def test_execute_download_no_url_sets_error_not_exception(self):
        # Invalid EC: content_fileurl == "" for regular file path.
        task = self._new_task("")
        task.download_url = AsyncMock()

        await task._execute_download()

        self.assertEqual(task.status.error, "No URL available for download")
        task.download_url.assert_not_called()

    async def test_execute_download_valid_regular_url_calls_download(self):
        # Valid EC: regular HTTP URL.
        task = self._new_task("https://keats.kcl.ac.uk/pluginfile.php/1/mod_resource/content/1/a.pdf")
        task.download_url = AsyncMock()
        task.add_token_to_url = lambda u: f"{u}?token=token_x"

        await task._execute_download()

        task.download_url.assert_awaited_once()
        called_url = task.download_url.await_args.args[0]
        self.assertIn("token=token_x", called_url)

    async def test_execute_download_data_url_goes_to_data_handler(self):
        # Valid EC: data URL should use create_data_url_file branch.
        task = self._new_task("data:text/plain;base64,SGVsbG8=")
        task.create_data_url_file = AsyncMock()
        task.download_url = AsyncMock()

        await task._execute_download()

        task.create_data_url_file.assert_awaited_once()
        task.download_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
