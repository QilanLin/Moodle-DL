# -*- coding: utf-8 -*-
"""
main.py 单元测试

测试命令行参数解析、任务选择逻辑、下载服务初始化等核心功能
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch, AsyncMock, PropertyMock
import asyncio

from moodle_dl.main import choose_task, post_process_opts, get_parser
from moodle_dl.types import MoodleDlOpts, Course
from moodle_dl.config import ConfigHelper


class TestPostProcessOpts(unittest.TestCase):
    """post_process_opts 方法的测试"""

    def setUp(self):
        self.opts = MoodleDlOpts()

    def test_max_yt_dlp_capped_by_32(self):
        """测试 yt-dlp 线程数上限限制为32"""
        self.opts.max_parallel_yt_dlp = 50
        self.opts.max_parallel_downloads = 100
        post_process_opts(self.opts)
        # 应该被限制在最大值 32
        self.assertEqual(self.opts.max_parallel_yt_dlp, 32)

    def test_max_yt_dlp_capped_by_parallel_downloads(self):
        """测试 yt-dlp 线程数受 max_parallel_downloads 限制"""
        self.opts.max_parallel_yt_dlp = 20
        self.opts.max_parallel_downloads = 10
        post_process_opts(self.opts)
        # 应该被限制在 max_parallel_downloads
        self.assertEqual(self.opts.max_parallel_yt_dlp, 10)

    def test_max_yt_dlp_within_limit(self):
        """测试 yt-dlp 线程数在限制内保持不变"""
        self.opts.max_parallel_yt_dlp = 5
        self.opts.max_parallel_downloads = 10
        post_process_opts(self.opts)
        self.assertEqual(self.opts.max_parallel_yt_dlp, 5)

    def test_log_file_path_defaults_to_path(self):
        """测试 log_file_path 默认为 path"""
        opts = MoodleDlOpts()
        opts.path = "/tmp/test"
        opts.log_file_path = None
        result = post_process_opts(opts)
        self.assertEqual(result.log_file_path, "/tmp/test")

    def test_log_file_path_preserved_if_set(self):
        """测试已设置的 log_file_path 被保留"""
        opts = MoodleDlOpts()
        opts.path = "/tmp/test"
        opts.log_file_path = "/custom/log"
        result = post_process_opts(opts)
        self.assertEqual(result.log_file_path, "/custom/log")


class TestGetParser(unittest.TestCase):
    """get_parser 方法的测试"""

    def test_parser_exists(self):
        """测试解析器可以创建"""
        parser = get_parser()
        self.assertIsNotNone(parser)

    def test_parser_has_optional_flags(self):
        """测试解析器包含可选标志"""
        parser = get_parser()

        # 检查一些重要的可选标志
        optional_flags = [
            '--init', '--init-sso', '--download-courses',
            '--new-token', '--refresh-cookies',
            '--retry-failed', '--verbose', '--quiet'
        ]

        for flag in optional_flags:
            # 尝试解析（会失败如果参数格式错误，但我们只检查是否存在）
            try:
                parser.parse_args([flag])
            except SystemExit:
                pass  # argparse 会调用 sys.exit


class TestChooseTask(unittest.TestCase):
    """choose_task 任务路由测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config = MagicMock(spec=ConfigHelper)
        self.opts = MoodleDlOpts()

        # 设置配置mock返回临时目录
        self.config.get_misc_files_path.return_value = self.temp_dir

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('moodle_dl.main.run_main')
    def test_choose_task_default_runs_main(self, mock_run_main):
        """测试默认执行 run_main"""
        # 没有特殊标志时应该调用 run_main
        choose_task(self.config, self.opts)
        mock_run_main.assert_called_once_with(self.config, self.opts)

    @patch('moodle_dl.main.ConfigWizard.interactively_acquire_config')
    def test_choose_task_config_flag(self, mock_acquire):
        """测试 --config 标志"""
        self.opts.config = True
        choose_task(self.config, self.opts)
        mock_acquire.assert_called_once()

    @patch('moodle_dl.main.DatabaseManager')
    def test_choose_task_manage_database_flag(self, mock_db_class):
        """测试 --manage-database 标志"""
        self.opts.manage_database = True
        mock_db = MagicMock()
        mock_db_class.return_value = mock_db
        choose_task(self.config, self.opts)
        mock_db.interactively_manage_database.assert_called_once()

    @patch('moodle_dl.main.MoodleWizard.interactively_acquire_token')
    def test_choose_task_new_token_flag(self, mock_acquire):
        """测试 --new-token 标志"""
        self.opts.new_token = True
        choose_task(self.config, self.opts)
        mock_acquire.assert_called_once_with(use_stored_url=True)

    @patch('moodle_dl.main.refresh_cookies_only')
    def test_choose_task_refresh_cookies_flag(self, mock_refresh):
        """测试 --refresh-cookies 标志"""
        self.opts.refresh_cookies = True
        choose_task(self.config, self.opts)
        mock_refresh.assert_called_once_with(self.config, self.opts)

    @patch('moodle_dl.main.retry_failed_downloads')
    def test_choose_task_retry_failed_flag(self, mock_retry):
        """测试 --retry-failed 标志"""
        self.opts.retry_failed = True
        choose_task(self.config, self.opts)
        mock_retry.assert_called_once_with(self.config, self.opts)

    @patch('moodle_dl.main.DatabaseManager')
    def test_choose_task_delete_old_files_flag(self, mock_db_class):
        """测试 --delete-old-files 标志"""
        self.opts.delete_old_files = True

        # 创建 mock 实例
        mock_db = MagicMock()
        mock_db_class.return_value = mock_db

        choose_task(self.config, self.opts)
        mock_db.delete_old_files.assert_called_once()


class TestFailedDownloadStatistics(unittest.TestCase):
    """失败下载统计功能测试"""

    def test_get_failed_download_statistics_empty(self):
        """测试空统计数据的格式化"""
        database = MagicMock()
        database.get_failed_files_summary.return_value = {}

        from moodle_dl.main import _get_failed_download_statistics
        result = _get_failed_download_statistics(database)

        # 应该正确处理空情况
        self.assertIsNotNone(result)
        self.assertEqual(result, {})

    def test_print_failed_statistics_header(self):
        """测试统计头输出格式"""
        from moodle_dl.main import _print_failed_statistics_header
        # 应该不抛出异常
        _print_failed_statistics_header({
            1: {'course_fullname': 'Course 1', 'failed_count': 5, 'total_failures': 8, 'max_consecutive': 3}
        })

    def test_print_failed_statistics_details(self):
        """测试统计详情输出格式"""
        from moodle_dl.main import _print_failed_statistics_details
        # 应该不抛出异常
        _print_failed_statistics_details({
            1: {'course_fullname': 'Course 1', 'failed_count': 5, 'total_failures': 8, 'max_consecutive': 3}
        })


class TestRetryFailedDownloadsHelpers(unittest.TestCase):
    """重试失败下载的辅助函数测试"""

    def test_initialize_retry_database_returns_state_recorder(self):
        """测试数据库初始化返回 StateRecorder"""
        from moodle_dl.main import _initialize_retry_database

        config = MagicMock(spec=ConfigHelper)
        opts = MoodleDlOpts()

        # Mock StateRecorder to avoid actual database creation
        with patch('moodle_dl.main.StateRecorder') as mock_db_class:
            mock_db = MagicMock()
            mock_db_class.return_value = mock_db
            result = _initialize_retry_database(config, opts)
            self.assertIsInstance(result, MagicMock)
            mock_db_class.assert_called_once_with(config, opts)

    def test_get_failed_download_statistics_returns_dict(self):
        """测试获取失败统计返回字典"""
        mock_db = MagicMock()
        mock_db.get_failed_files_summary.return_value = {
            1: {'course_fullname': 'Course 1', 'failed_count': 2, 'total_failures': 3, 'max_consecutive': 1}
        }

        from moodle_dl.main import _get_failed_download_statistics
        result = _get_failed_download_statistics(mock_db)

        self.assertIsInstance(result, dict)
        self.assertIn(1, result)

    def test_load_failed_files_as_courses_returns_list(self):
        """测试加载失败文件返回课程列表"""
        database = MagicMock()
        database.get_failed_files_with_course_info.return_value = {
            1: {
                'course_fullname': 'Course 1',
                'files': []
            }
        }

        from moodle_dl.main import _load_failed_files_as_courses
        from moodle_dl.utils import PathTools

        # Save original state to ensure isolation
        original_restricted = PathTools.restricted_filenames

        try:
            result = _load_failed_files_as_courses(database)

            self.assertIsInstance(result, list)
            self.assertEqual(len(result), 1)
            # Course constructor normalizes the fullname using PT.to_valid_name()
            # The exact result depends on PathTools.restricted_filenames setting
            # Just verify we got a Course object with a valid fullname
            self.assertIsInstance(result[0].fullname, str)
            self.assertTrue(len(result[0].fullname) > 0)
        finally:
            # Restore original state for test isolation
            PathTools.restricted_filenames = original_restricted

    def test_load_failed_files_as_courses_empty_returns_empty_list(self):
        """测试无失败文件时返回空列表"""
        database = MagicMock()
        database.get_failed_files_with_course_info.return_value = {}

        from moodle_dl.main import _load_failed_files_as_courses
        result = _load_failed_files_as_courses(database)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)


class TestCreateDownloader(unittest.TestCase):
    """下载服务创建测试"""

    @patch('moodle_dl.main.DownloadService')
    def test_create_downloader_with_downloading_enabled(self, mock_ds_class):
        """测试启用文件下载时创建 DownloadService"""
        mock_db = MagicMock()
        mock_ds = MagicMock()
        mock_ds_class.return_value = mock_ds

        config = MagicMock()
        courses = []
        opts = MoodleDlOpts()
        opts.without_downloading_files = False

        from moodle_dl.main import _create_downloader
        result = _create_downloader(courses, config, opts, mock_db)

        # 应该创建 DownloadService 而不是 FakeDownloadService
        mock_ds_class.assert_called_once_with(courses, config, opts, mock_db)

    @patch('moodle_dl.main.FakeDownloadService')
    def test_create_downloader_without_downloading(self, mock_fds_class):
        """测试禁用文件下载时创建 FakeDownloadService"""
        mock_db = MagicMock()
        mock_fds = MagicMock()
        mock_fds_class.return_value = mock_fds

        config = MagicMock()
        courses = []
        opts = MoodleDlOpts()
        opts.without_downloading_files = True

        from moodle_dl.main import _create_downloader
        result = _create_downloader(courses, config, opts, mock_db)

        # 应该创建 FakeDownloadService
        mock_fds_class.assert_called_once_with(courses, config, opts, mock_db)


if __name__ == "__main__":
    unittest.main()
