# -*- coding: utf-8 -*-
"""
main.py 单元测试

测试命令行参数解析、任务选择逻辑、下载服务初始化等核心功能
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from moodle_dl.main import (
    choose_task,
    connect_sentry,
    get_parser,
    main,
    post_process_opts,
    refresh_cookies_only,
    run_main,
)
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

    @patch('moodle_dl.main.PT.win_max_path_length_workaround', return_value='\\\\?\\C:\\moodle')
    def test_max_path_length_workaround_updates_path(self, mock_workaround):
        """测试启用 Windows 路径长度 workaround 时会更新 path"""
        opts = MoodleDlOpts()
        opts.path = 'C:\\moodle'
        opts.max_path_length_workaround = True

        result = post_process_opts(opts)

        mock_workaround.assert_called_once_with('C:\\moodle')
        self.assertEqual(result.path, '\\\\?\\C:\\moodle')


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
            '--retry-failed', '--resume', '--verbose', '--quiet'
        ]

        for flag in optional_flags:
            # 尝试解析（会失败如果参数格式错误，但我们只检查是否存在）
            try:
                parser.parse_args([flag])
            except SystemExit:
                pass  # argparse 会调用 sys.exit

    def test_parser_parses_real_flags_and_values(self):
        """测试常用 CLI 参数会解析到 MoodleDlOpts 字段"""
        parser = get_parser()

        with tempfile.TemporaryDirectory() as temp_dir:
            opts = parser.parse_args([
                '--refresh-cookies',
                '--sso',
                '--username', 'student',
                '--password', 'secret',
                '--token', 'token-value',
                '--path', temp_dir,
                '--max-parallel-api-calls', '3',
                '--max-parallel-downloads', '4',
                '--max-parallel-yt-dlp', '2',
                '--download-chunk-size', '4096',
                '--ignore-ytdl-errors',
                '--without-downloading-files',
                '--allow-insecure-ssl',
                '--use-all-ciphers',
                '--skip-cert-verify',
                '--verbose',
                '--log-to-file',
                '--log-file-path', temp_dir,
            ])

        self.assertTrue(opts.refresh_cookies)
        self.assertTrue(opts.sso)
        self.assertEqual(opts.username, 'student')
        self.assertEqual(opts.password, 'secret')
        self.assertEqual(opts.token, 'token-value')
        self.assertEqual(opts.max_parallel_api_calls, 3)
        self.assertEqual(opts.max_parallel_downloads, 4)
        self.assertEqual(opts.max_parallel_yt_dlp, 2)
        self.assertEqual(opts.download_chunk_size, 4096)
        self.assertTrue(opts.ignore_ytdl_errors)
        self.assertTrue(opts.without_downloading_files)
        self.assertTrue(opts.allow_insecure_ssl)
        self.assertTrue(opts.use_all_ciphers)
        self.assertTrue(opts.skip_cert_verify)
        self.assertTrue(opts.verbose)
        self.assertTrue(opts.log_to_file)

    def test_parser_parses_resume_flag(self):
        """测试 --resume 会解析到 MoodleDlOpts 字段"""
        parser = get_parser()

        opts = parser.parse_args(['--resume'])

        self.assertTrue(opts.resume)

    def test_parser_rejects_invalid_path(self):
        """测试不存在的路径会被 argparse 拒绝"""
        parser = get_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(['--path', '/path/that/does/not/exist'])

    def test_parser_relative_path_uses_home_when_cwd_is_missing(self):
        """测试当前目录被删除时相对路径回退到 home 目录解析"""
        parser = get_parser()

        with (
            patch('moodle_dl.main.os.getcwd', side_effect=FileNotFoundError),
            patch('moodle_dl.main.os.path.expanduser', return_value='/home/tester'),
            patch('moodle_dl.main.os.path.isdir', return_value=True),
        ):
            opts = parser.parse_args(['--path', 'downloads'])

        self.assertEqual(opts.path, '/home/tester/downloads')


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

    @patch('moodle_dl.main.resume_downloads')
    def test_choose_task_resume_flag(self, mock_resume):
        """测试 --resume 标志"""
        self.opts.resume = True
        choose_task(self.config, self.opts)
        mock_resume.assert_called_once_with(self.config, self.opts)

    @patch('moodle_dl.main.DatabaseManager')
    def test_choose_task_delete_old_files_flag(self, mock_db_class):
        """测试 --delete-old-files 标志"""
        self.opts.delete_old_files = True

        # 创建 mock 实例
        mock_db = MagicMock()
        mock_db_class.return_value = mock_db

        choose_task(self.config, self.opts)
        mock_db.delete_old_files.assert_called_once()

    @patch('moodle_dl.main.ConfigWizard')
    def test_choose_task_add_all_visible_courses_flag(self, mock_wizard_class):
        """测试 --add-all-visible-courses 标志"""
        self.opts.add_all_visible_courses = True

        choose_task(self.config, self.opts)

        mock_wizard_class.assert_called_once_with(self.config, self.opts)
        mock_wizard_class.return_value.interactively_add_all_visible_courses.assert_called_once()

    @patch('moodle_dl.main.NotificationsWizard')
    def test_choose_task_notification_flags(self, mock_wizard_class):
        """测试通知配置分支会路由到对应 wizard 方法"""
        flag_to_method = [
            ('change_notification_mail', 'interactively_configure_mail'),
            ('change_notification_telegram', 'interactively_configure_telegram'),
            ('change_notification_discord', 'interactively_configure_discord'),
            ('change_notification_ntfy', 'interactively_configure_ntfy'),
            ('change_notification_xmpp', 'interactively_configure_xmpp'),
        ]

        for flag, method_name in flag_to_method:
            with self.subTest(flag=flag):
                opts = MoodleDlOpts()
                setattr(opts, flag, True)
                mock_wizard_class.reset_mock()

                choose_task(self.config, opts)

                mock_wizard_class.assert_called_once_with(self.config, opts)
                getattr(mock_wizard_class.return_value, method_name).assert_called_once()

    @patch('moodle_dl.main.DatabaseManager')
    def test_choose_task_reset_downloaded_files_cn_flag(self, mock_db_class):
        """测试中文重置下载状态标志"""
        self.opts.reset_downloaded_files_cn = True

        choose_task(self.config, self.opts)

        mock_db_class.assert_called_once_with(self.config, self.opts)
        mock_db_class.return_value.reset_all_downloaded_files.assert_called_once()

    @patch('moodle_dl.main.DatabaseManager')
    def test_choose_task_reset_downloaded_files_flag(self, mock_db_class):
        """测试英文重置下载状态标志"""
        self.opts.reset_downloaded_files = True

        choose_task(self.config, self.opts)

        mock_db_class.assert_called_once_with(self.config, self.opts)
        mock_db_class.return_value.reset_all_downloaded_files.assert_called_once()


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

    def test_load_incomplete_download_files_as_courses(self):
        """测试加载未完成下载返回课程列表"""
        database = MagicMock()
        resumable_file = MagicMock(file_id=42)
        database.get_incomplete_files_with_course_info.return_value = {
            1: {
                'course_fullname': 'Course 1',
                'files': [resumable_file],
            }
        }

        from moodle_dl.main import _load_incomplete_download_files_as_courses

        result = _load_incomplete_download_files_as_courses(database)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, 1)
        self.assertEqual(result[0].files, [resumable_file])

    def test_merge_course_file_lists_deduplicates_by_file_id(self):
        """测试续传队列合并时不会重复同一个文件"""
        from moodle_dl.main import _merge_course_file_lists

        primary_file = MagicMock(file_id=42)
        duplicate_file = MagicMock(file_id=42)
        extra_file = MagicMock(file_id=43)

        merged = _merge_course_file_lists(
            [Course(1, 'Course', [primary_file])],
            [Course(1, 'Course', [duplicate_file, extra_file])],
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].files, [primary_file, extra_file])


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
        mock_ds_class.assert_called_once_with(courses, config, opts, mock_db, network_throttle=None)

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


class TestConnectSentry(unittest.TestCase):
    """Sentry 连接逻辑测试"""

    @patch('moodle_dl.main.sentry_sdk.init')
    def test_connect_sentry_initializes_when_dsn_exists(self, mock_init):
        """测试配置了 DSN 时初始化 Sentry"""
        config = MagicMock(spec=ConfigHelper)
        config.get_property.return_value = 'https://public@sentry.example/1'

        self.assertTrue(connect_sentry(config))

        mock_init.assert_called_once_with('https://public@sentry.example/1')

    @patch('moodle_dl.main.sentry_sdk.init', side_effect=ValueError)
    def test_connect_sentry_returns_false_on_invalid_dsn(self, mock_init):
        """测试无效 DSN 不会中断主流程"""
        config = MagicMock(spec=ConfigHelper)
        config.get_property.return_value = 'invalid'

        self.assertFalse(connect_sentry(config))

        mock_init.assert_called_once_with('invalid')

    @patch('moodle_dl.main.sentry_sdk.init')
    def test_connect_sentry_returns_false_without_dsn(self, mock_init):
        """测试未配置 DSN 时不初始化 Sentry"""
        config = MagicMock(spec=ConfigHelper)
        config.get_property.return_value = ''

        self.assertFalse(connect_sentry(config))
        mock_init.assert_not_called()


class TestRunMain(unittest.TestCase):
    """run_main 主流程测试"""

    def setUp(self):
        self.config = MagicMock(spec=ConfigHelper)
        self.opts = MoodleDlOpts()

    @patch('moodle_dl.main.DownloadService')
    @patch('moodle_dl.main.StateRecorder')
    @patch('moodle_dl.main.MoodleService')
    @patch('moodle_dl.main.get_all_notify_services', return_value=[])
    @patch('moodle_dl.main.connect_sentry', return_value=False)
    def test_run_main_log_responses_stops_before_download(
        self,
        mock_connect_sentry,
        mock_get_services,
        mock_moodle_class,
        mock_db_class,
        mock_download_class,
    ):
        """测试 --log-responses 获取 Moodle 状态后不进入下载阶段"""
        self.opts.log_responses = True
        changed_courses = [Course(1, 'Course 1')]
        mock_moodle_class.return_value.fetch_state = AsyncMock(return_value=changed_courses)

        run_main(self.config, self.opts)

        mock_connect_sentry.assert_called_once_with(self.config)
        mock_get_services.assert_called_once_with(self.config)
        mock_moodle_class.assert_called_once_with(self.config, self.opts, network_throttle=ANY)
        mock_db_class.assert_called_once_with(self.config, self.opts)
        mock_download_class.assert_not_called()

    @patch('moodle_dl.main.FakeDownloadService')
    @patch('moodle_dl.main.StateRecorder')
    @patch('moodle_dl.main.MoodleService')
    @patch('moodle_dl.main.get_all_notify_services')
    @patch('moodle_dl.main.connect_sentry', return_value=False)
    def test_run_main_without_downloading_notifies_changes_and_failures(
        self,
        mock_connect_sentry,
        mock_get_services,
        mock_moodle_class,
        mock_db_class,
        mock_fake_download_class,
    ):
        """测试不下载文件模式下仍会通知 Moodle 变化和失败任务"""
        self.opts.without_downloading_files = True
        changed_courses = [Course(1, 'Changed course')]
        changed_courses_to_notify = [Course(2, 'Notify course')]
        failed_tasks = [MagicMock()]
        notify_service = MagicMock()

        mock_get_services.return_value = [notify_service]
        mock_moodle_class.return_value.fetch_state = AsyncMock(return_value=changed_courses)
        mock_db_class.return_value.changes_to_notify.return_value = changed_courses_to_notify
        mock_fake_download_class.return_value.get_failed_tasks.return_value = failed_tasks

        run_main(self.config, self.opts)

        mock_connect_sentry.assert_called_once_with(self.config)
        mock_fake_download_class.assert_called_once_with(
            changed_courses, self.config, self.opts, mock_db_class.return_value
        )
        mock_fake_download_class.return_value.run.assert_called_once()
        notify_service.notify_about_changes_in_moodle.assert_called_once_with(changed_courses_to_notify)
        mock_db_class.return_value.notified.assert_called_once_with(changed_courses_to_notify)
        notify_service.notify_about_failed_downloads.assert_called_once_with(failed_tasks)

    @patch('moodle_dl.main.DownloadService')
    @patch('moodle_dl.main.StateRecorder')
    @patch('moodle_dl.main.MoodleService')
    @patch('moodle_dl.main.get_all_notify_services')
    @patch('moodle_dl.main.connect_sentry', return_value=False)
    def test_run_main_logs_when_there_are_no_changes(
        self,
        mock_connect_sentry,
        mock_get_services,
        mock_moodle_class,
        mock_db_class,
        mock_download_class,
    ):
        """测试没有可通知变化时不会调用通知服务"""
        notify_service = MagicMock()
        mock_get_services.return_value = [notify_service]
        mock_moodle_class.return_value.fetch_state = AsyncMock(return_value=[])
        mock_db_class.return_value.changes_to_notify.return_value = []
        mock_download_class.return_value.get_failed_tasks.return_value = []

        run_main(self.config, self.opts)

        mock_connect_sentry.assert_called_once_with(self.config)
        mock_download_class.assert_called_once_with(
            [], self.config, self.opts, mock_db_class.return_value, network_throttle=ANY
        )
        notify_service.notify_about_changes_in_moodle.assert_not_called()
        notify_service.notify_about_failed_downloads.assert_not_called()

    @patch('moodle_dl.main.sentry_sdk.capture_exception')
    @patch('moodle_dl.main.StateRecorder')
    @patch('moodle_dl.main.MoodleService')
    @patch('moodle_dl.main.get_all_notify_services')
    @patch('moodle_dl.main.connect_sentry', return_value=True)
    def test_run_main_reports_errors_to_sentry_and_notifiers(
        self,
        mock_connect_sentry,
        mock_get_services,
        mock_moodle_class,
        mock_db_class,
        mock_capture_exception,
    ):
        """测试主流程异常会发送到 Sentry 和通知服务后重新抛出"""
        error = RuntimeError('fetch failed')
        notify_service = MagicMock()
        mock_get_services.return_value = [notify_service]
        mock_moodle_class.return_value.fetch_state = AsyncMock(side_effect=error)

        with self.assertRaises(RuntimeError):
            run_main(self.config, self.opts)

        mock_connect_sentry.assert_called_once_with(self.config)
        mock_db_class.assert_called_once_with(self.config, self.opts)
        mock_capture_exception.assert_called_once_with(error)
        notify_service.notify_about_error.assert_called_once_with('fetch failed')

    @patch('moodle_dl.main.sentry_sdk.capture_exception')
    @patch('moodle_dl.main.StateRecorder')
    @patch('moodle_dl.main.MoodleService')
    @patch('moodle_dl.main.get_all_notify_services')
    @patch('moodle_dl.main.connect_sentry', return_value=False)
    def test_run_main_formats_blank_error_text(
        self,
        mock_connect_sentry,
        mock_get_services,
        mock_moodle_class,
        mock_db_class,
        mock_capture_exception,
    ):
        """测试异常文本为空时使用 traceback 摘要通知"""
        notify_service = MagicMock()
        mock_get_services.return_value = [notify_service]
        mock_moodle_class.return_value.fetch_state = AsyncMock(side_effect=RuntimeError(' '))

        with self.assertRaises(RuntimeError):
            run_main(self.config, self.opts)

        mock_connect_sentry.assert_called_once_with(self.config)
        mock_db_class.assert_called_once_with(self.config, self.opts)
        mock_capture_exception.assert_not_called()
        notified_error = notify_service.notify_about_error.call_args.args[0]
        self.assertIn('RuntimeError', notified_error)

    @patch('moodle_dl.main.DownloadService')
    @patch('moodle_dl.main._load_incomplete_download_files_as_courses')
    @patch('moodle_dl.main.StateRecorder')
    @patch('moodle_dl.main.MoodleService')
    @patch('moodle_dl.main.get_all_notify_services', return_value=[])
    @patch('moodle_dl.main.connect_sentry', return_value=False)
    def test_run_main_resume_merges_incomplete_downloads(
        self,
        mock_connect_sentry,
        mock_get_services,
        mock_moodle_class,
        mock_db_class,
        mock_load_incomplete,
        mock_download_class,
    ):
        """测试 --resume 会把数据库中的未完成下载加入下载队列"""
        self.opts.resume = True
        scanned_file = MagicMock(file_id=1)
        duplicate_file = MagicMock(file_id=1)
        incomplete_file = MagicMock(file_id=2)
        scanned_course = Course(10, 'Course', [scanned_file])
        incomplete_course = Course(10, 'Course', [duplicate_file, incomplete_file])
        mock_moodle_class.return_value.fetch_state = AsyncMock(return_value=[scanned_course])
        mock_load_incomplete.return_value = [incomplete_course]
        mock_db_class.return_value.changes_to_notify.return_value = []
        mock_download_class.return_value.get_failed_tasks.return_value = []

        run_main(self.config, self.opts)

        queued_courses = mock_download_class.call_args.args[0]
        self.assertEqual(len(queued_courses), 1)
        self.assertEqual(queued_courses[0].files, [scanned_file, incomplete_file])
        mock_load_incomplete.assert_called_once_with(mock_db_class.return_value)


class TestRefreshCookiesOnly(unittest.TestCase):
    """refresh_cookies_only 分支测试"""

    def setUp(self):
        self.config = MagicMock(spec=ConfigHelper)
        self.opts = MoodleDlOpts()
        self.config.get_misc_files_path.return_value = '/tmp/moodle'
        self.log_patchers = [
            patch('moodle_dl.utils.Log.info'),
            patch('moodle_dl.utils.Log.success'),
            patch('moodle_dl.utils.Log.error'),
        ]
        for patcher in self.log_patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.log_patchers):
            patcher.stop()

    def _fake_export_module(self, export_result=True, test_result=True):
        return SimpleNamespace(
            export_cookies_from_browser=MagicMock(return_value=export_result),
            export_cookies_interactive=MagicMock(return_value=export_result),
            test_cookies=MagicMock(return_value=test_result),
        )

    def _fake_import_loader(self):
        return SimpleNamespace(loader=SimpleNamespace(exec_module=MagicMock()))

    def test_refresh_cookies_only_returns_when_moodle_url_is_missing(self):
        """测试未配置 Moodle URL 时直接返回"""
        self.config.get_moodle_URL.return_value = None

        refresh_cookies_only(self.config, self.opts)

        self.config.get_misc_files_path.assert_not_called()

    def test_refresh_cookies_only_missing_export_script_returns(self):
        """测试找不到导出脚本时直接返回"""
        self.config.get_moodle_URL.return_value = SimpleNamespace(domain='keats.kcl.ac.uk')

        with (
            patch('moodle_dl.utils.Cutie.select', return_value=1),
            patch('moodle_dl.main.os.path.exists', return_value=False),
        ):
            refresh_cookies_only(self.config, self.opts)

        self.config.set_property.assert_not_called()

    def test_refresh_cookies_only_selected_browser_success_saves_preference(self):
        """测试指定浏览器导出成功后保存浏览器偏好"""
        self.config.get_moodle_URL.return_value = SimpleNamespace(domain='keats.kcl.ac.uk')
        self.config.get_property.return_value = 'firefox'
        export_module = self._fake_export_module(export_result=True, test_result=True)

        with (
            patch('moodle_dl.utils.Cutie.select', return_value=1),
            patch('moodle_dl.main.os.path.exists', return_value=True),
            patch('importlib.util.spec_from_file_location', return_value=self._fake_import_loader()),
            patch('importlib.util.module_from_spec', return_value=export_module),
        ):
            refresh_cookies_only(self.config, self.opts)

        export_module.export_cookies_from_browser.assert_called_once_with(
            domain='keats.kcl.ac.uk',
            output_file='/tmp/moodle/Cookies.txt',
            browser_name='firefox',
        )
        export_module.test_cookies.assert_called_once_with('keats.kcl.ac.uk', '/tmp/moodle/Cookies.txt')
        self.config.set_property.assert_called_once_with('preferred_browser', 'firefox')

    def test_refresh_cookies_only_selected_browser_failed_cookie_test(self):
        """测试指定浏览器导出后验证失败不会保存偏好"""
        self.config.get_moodle_URL.return_value = SimpleNamespace(domain='keats.kcl.ac.uk')
        self.config.get_property.side_effect = KeyError
        export_module = self._fake_export_module(export_result=True, test_result=False)

        with (
            patch('moodle_dl.utils.Cutie.select', return_value=1),
            patch('moodle_dl.main.os.path.exists', return_value=True),
            patch('importlib.util.spec_from_file_location', return_value=self._fake_import_loader()),
            patch('importlib.util.module_from_spec', return_value=export_module),
        ):
            refresh_cookies_only(self.config, self.opts)

        export_module.export_cookies_from_browser.assert_called_once()
        export_module.test_cookies.assert_called_once()
        self.config.set_property.assert_not_called()

    def test_refresh_cookies_only_auto_detect_uses_interactive_export(self):
        """测试自动检测分支调用交互式导出"""
        self.config.get_moodle_URL.return_value = SimpleNamespace(domain='keats.kcl.ac.uk')
        self.config.get_property.side_effect = KeyError
        export_module = self._fake_export_module(export_result=False)

        with (
            patch('moodle_dl.utils.Cutie.select', return_value=8),
            patch('moodle_dl.main.os.path.exists', return_value=True),
            patch('importlib.util.spec_from_file_location', return_value=self._fake_import_loader()),
            patch('importlib.util.module_from_spec', return_value=export_module),
        ):
            refresh_cookies_only(self.config, self.opts)

        export_module.export_cookies_interactive.assert_called_once_with(
            domain='keats.kcl.ac.uk',
            output_file='/tmp/moodle/Cookies.txt',
            ask_browser=False,
            auto_get_token=False,
        )
        export_module.export_cookies_from_browser.assert_not_called()
        self.config.set_property.assert_not_called()

    def test_refresh_cookies_only_logs_import_error(self):
        """测试 browser-cookie3 导入错误会被吞掉并记录"""
        self.config.get_moodle_URL.return_value = SimpleNamespace(domain='keats.kcl.ac.uk')
        fake_spec = SimpleNamespace(loader=SimpleNamespace(exec_module=MagicMock(side_effect=ImportError('missing'))))

        with (
            patch('moodle_dl.utils.Cutie.select', return_value=1),
            patch('moodle_dl.main.os.path.exists', return_value=True),
            patch('importlib.util.spec_from_file_location', return_value=fake_spec),
            patch('importlib.util.module_from_spec', return_value=SimpleNamespace()),
        ):
            refresh_cookies_only(self.config, self.opts)

        self.config.set_property.assert_not_called()


class TestMainEntrypoint(unittest.TestCase):
    """main() 入口函数测试"""

    def _base_patches(self):
        return (
            patch('moodle_dl.main.just_fix_windows_console'),
            patch('moodle_dl.main.setup_logger'),
        )

    def test_main_init_runs_init_config_and_exits(self):
        """测试 --init 直接进入初始化流程并退出"""
        with tempfile.TemporaryDirectory() as temp_dir:
            console_patcher, logger_patcher = self._base_patches()
            with (
                console_patcher,
                logger_patcher,
                patch('moodle_dl.main.init_config') as mock_init_config,
                patch('moodle_dl.main.sys.exit', side_effect=SystemExit(0)) as mock_exit,
            ):
                with self.assertRaises(SystemExit):
                    main(['--init', '--path', temp_dir])

        mock_init_config.assert_called_once()
        self.assertTrue(mock_init_config.call_args.args[1].init)
        mock_exit.assert_called_once_with(0)

    def test_main_missing_config_exits_with_warning(self):
        """测试缺少配置时提示用户并以 -1 退出"""
        with tempfile.TemporaryDirectory() as temp_dir:
            console_patcher, logger_patcher = self._base_patches()
            with (
                console_patcher,
                logger_patcher,
                patch('moodle_dl.main.ConfigHelper.load', side_effect=ConfigHelper.NoConfigError('missing')),
                patch('moodle_dl.main.sys.exit', side_effect=SystemExit(-1)) as mock_exit,
            ):
                with self.assertRaises(SystemExit):
                    main(['--path', temp_dir])

        mock_exit.assert_called_once_with(-1)

    @patch('moodle_dl.main.choose_task')
    @patch('moodle_dl.main.ProcessLock.unlock')
    @patch('moodle_dl.main.ProcessLock.lock')
    @patch('moodle_dl.main.check_debug', return_value=False)
    @patch('moodle_dl.main.ConfigHelper.load', return_value=None)
    @patch('moodle_dl.main.ConfigHelper.get_misc_files_path')
    def test_main_success_locks_runs_task_and_unlocks(
        self,
        mock_misc_path,
        mock_load,
        mock_check_debug,
        mock_lock,
        mock_unlock,
        mock_choose_task,
    ):
        """测试正常主流程会加锁、执行任务并解锁"""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_misc_path.return_value = temp_dir
            console_patcher, logger_patcher = self._base_patches()
            with console_patcher, logger_patcher:
                main(['--path', temp_dir])

        mock_load.assert_called_once()
        mock_check_debug.assert_called_once()
        mock_lock.assert_called_once_with(temp_dir)
        mock_choose_task.assert_called_once()
        mock_unlock.assert_called_once_with(temp_dir)

    @patch('moodle_dl.main.choose_task')
    @patch('moodle_dl.main.ProcessLock.unlock')
    @patch('moodle_dl.main.ProcessLock.lock')
    @patch('moodle_dl.main.check_debug', return_value=True)
    @patch('moodle_dl.main.ConfigHelper.load', return_value=None)
    @patch('moodle_dl.main.ConfigHelper.get_misc_files_path')
    def test_main_debug_mode_skips_process_lock(
        self,
        mock_misc_path,
        mock_load,
        mock_check_debug,
        mock_lock,
        mock_unlock,
        mock_choose_task,
    ):
        """测试 debug 模式下不创建进程锁"""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_misc_path.return_value = temp_dir
            console_patcher, logger_patcher = self._base_patches()
            with console_patcher, logger_patcher:
                main(['--path', temp_dir])

        mock_load.assert_called_once()
        mock_check_debug.assert_called_once()
        mock_lock.assert_not_called()
        mock_choose_task.assert_called_once()
        mock_unlock.assert_called_once_with(temp_dir)

    @patch('moodle_dl.main.choose_task', side_effect=RuntimeError('boom'))
    @patch('moodle_dl.main.ProcessLock.unlock')
    @patch('moodle_dl.main.ProcessLock.lock')
    @patch('moodle_dl.main.check_debug', return_value=False)
    @patch('moodle_dl.main.ConfigHelper.load', return_value=None)
    @patch('moodle_dl.main.ConfigHelper.get_misc_files_path')
    def test_main_unlocks_and_exits_when_task_fails(
        self,
        mock_misc_path,
        mock_load,
        mock_check_debug,
        mock_lock,
        mock_unlock,
        mock_choose_task,
    ):
        """测试任务异常时会解锁并以 1 退出"""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_misc_path.return_value = temp_dir
            console_patcher, logger_patcher = self._base_patches()
            with (
                console_patcher,
                logger_patcher,
                patch('moodle_dl.main.sys.exit', side_effect=SystemExit(1)) as mock_exit,
            ):
                with self.assertRaises(SystemExit):
                    main(['--path', temp_dir])

        mock_load.assert_called_once()
        self.assertEqual(mock_check_debug.call_count, 2)
        mock_lock.assert_called_once_with(temp_dir)
        mock_choose_task.assert_called_once()
        mock_unlock.assert_called_once_with(temp_dir)
        mock_exit.assert_called_once_with(1)

    @patch('moodle_dl.main.logging.error')
    @patch('moodle_dl.main.choose_task', side_effect=RuntimeError('boom'))
    @patch('moodle_dl.main.ProcessLock.unlock')
    @patch('moodle_dl.main.ProcessLock.lock')
    @patch('moodle_dl.main.check_debug', return_value=False)
    @patch('moodle_dl.main.ConfigHelper.load', return_value=None)
    @patch('moodle_dl.main.ConfigHelper.get_misc_files_path')
    def test_main_verbose_failure_logs_traceback(
        self,
        mock_misc_path,
        mock_load,
        mock_check_debug,
        mock_lock,
        mock_unlock,
        mock_choose_task,
        mock_log_error,
    ):
        """测试 verbose 模式下异常处理记录 traceback"""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_misc_path.return_value = temp_dir
            console_patcher, logger_patcher = self._base_patches()
            with (
                console_patcher,
                logger_patcher,
                patch('moodle_dl.main.sys.exit', side_effect=SystemExit(1)),
            ):
                with self.assertRaises(SystemExit):
                    main(['--verbose', '--path', temp_dir])

        mock_load.assert_called_once()
        mock_check_debug.assert_called_once()
        mock_lock.assert_called_once_with(temp_dir)
        mock_unlock.assert_called_once_with(temp_dir)
        self.assertIn('Traceback', mock_log_error.call_args.args[0])

    @patch('moodle_dl.main.choose_task')
    @patch('moodle_dl.main.ProcessLock.unlock')
    @patch('moodle_dl.main.ProcessLock.lock')
    @patch('moodle_dl.main.check_debug', return_value=False)
    @patch('moodle_dl.main.ConfigHelper.load', return_value=None)
    @patch('moodle_dl.main.ConfigHelper.get_misc_files_path')
    def test_main_lock_error_exits_without_unlocking(
        self,
        mock_misc_path,
        mock_load,
        mock_check_debug,
        mock_lock,
        mock_unlock,
        mock_choose_task,
    ):
        """测试加锁失败时不会尝试解锁别的进程锁"""
        from moodle_dl.utils import ProcessLock

        with tempfile.TemporaryDirectory() as temp_dir:
            mock_misc_path.return_value = temp_dir
            mock_lock.side_effect = ProcessLock.LockError('locked')
            console_patcher, logger_patcher = self._base_patches()
            with (
                console_patcher,
                logger_patcher,
                patch('moodle_dl.main.sys.exit', side_effect=SystemExit(1)) as mock_exit,
            ):
                with self.assertRaises(SystemExit):
                    main(['--path', temp_dir])

        mock_load.assert_called_once()
        self.assertEqual(mock_check_debug.call_count, 2)
        mock_lock.assert_called_once_with(temp_dir)
        mock_choose_task.assert_not_called()
        mock_unlock.assert_not_called()
        mock_exit.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
