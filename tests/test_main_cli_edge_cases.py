# -*- coding: utf-8 -*-
"""
Edge case / 边界 测试 for moodle_dl/main.py CLI dispatch.

This module focuses on areas not covered by tests/test_main.py:

  1) Mutually-exclusive flag handling at the argparse layer
  2) --path validation behaviour (existing / non-existing / relative)
  3) Unknown CLI arguments => argparse SystemExit(2)
  4) Missing config.json (first-run scenario)
  5) Sentry init failure modes (caught vs uncaught exceptions)
  6) Sub-task routing: retry / resume / refresh-cookies dispatching
  7) AuthMigrator rollback semantics + first-run skip

These tests intentionally exercise the dispatcher / entry-point
without performing real I/O. Where the real implementations touch
the filesystem, network, or a SQLite database, we patch / mock them
out so that the test is fast and deterministic.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import sentry_sdk

from moodle_dl.config import ConfigHelper
from moodle_dl.main import choose_task, connect_sentry, get_parser, main
from moodle_dl.migrate_auth_to_db import AuthMigrator
from moodle_dl.types import MoodleDlOpts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 1) Mutually-exclusive flags
# ---------------------------------------------------------------------------

class TestMutuallyExclusiveFlags(unittest.TestCase):
    """argparse 自身已经处理互斥 flag；它们在 choose_task 之前就会
    触发 SystemExit(2)。这里的测试固定这一行为，防止被意外改回
    "第一个 if 胜出" 的语义。"""

    # These pairs come from the same add_mutually_exclusive_group() block
    # inside get_parser(). Combining any two of them should fail fast.
    MUTEX_PAIRS = [
        (['--init', '--config'], '--config'),
        (['--config', '--new-token'], '--new-token'),
        (['--config', '--change-notification-mail'], '--change-notification-mail'),
        (['--new-token', '--change-notification-discord'], '--change-notification-discord'),
        (['--reset-downloaded-files', '--重置下载文件'], '--重置下载文件'),
        (['--retry-failed', '--resume'], '--resume'),
        (['--init', '--retry-failed'], '--retry-failed'),
        (['--refresh-cookies', '--config'], '--config'),
        (['--add-all-visible-courses', '--new-token'], '--new-token'),
    ]

    def test_mutex_pairs_trigger_systemexit_2(self):
        """两个互斥 flag 同时出现：argparse 以 SystemExit(2) 终止。"""
        parser = get_parser()
        for argv, _expected_loser in self.MUTEX_PAIRS:
            with self.subTest(argv=argv):
                with tempfile.TemporaryDirectory() as tmp:
                    # --path 必须给，否则会被 _dir_path 拦截而不是互斥错误
                    with self.assertRaises(SystemExit) as ctx:
                        parser.parse_args(argv + ['--path', tmp])
                self.assertEqual(ctx.exception.code, 2)

    def test_first_or_wins_does_not_apply_to_choose_task(self):
        """``choose_task`` 永远不会收到两个互斥 flag 都为 True 的 opts。

        验证方式：手动构造这样的 opts 时，第一个 if-分支胜出。
        这锁定当前 dispatch 顺序：add_all_visible_courses 优先于
        change_notification_mail，后者优先于 config，依次类推。
        """
        config = MagicMock(spec=ConfigHelper)
        config.get_misc_files_path.return_value = tempfile.gettempdir()

        # --add-all-visible-courses 在最前
        opts = MoodleDlOpts()
        opts.add_all_visible_courses = True
        opts.change_notification_mail = True  # 同时为 True
        opts.config = True
        opts.new_token = True
        with patch('moodle_dl.main.ConfigWizard') as mock_wiz:
            choose_task(config, opts)
            mock_wiz.assert_called_once_with(config, opts)
            mock_wiz.return_value.interactively_add_all_visible_courses.assert_called_once()

        # change_notification_mail 胜出 config / new_token
        opts = MoodleDlOpts()
        opts.change_notification_mail = True
        opts.config = True
        opts.new_token = True
        with patch('moodle_dl.main.NotificationsWizard') as mock_wiz:
            choose_task(config, opts)
            mock_wiz.return_value.interactively_configure_mail.assert_called_once()

        # config 胜出 new_token
        opts = MoodleDlOpts()
        opts.config = True
        opts.new_token = True
        with patch('moodle_dl.main.ConfigWizard') as mock_wiz:
            choose_task(config, opts)
            mock_wiz.assert_called_once_with(config, opts)
            mock_wiz.return_value.interactively_acquire_config.assert_called_once()

        # new_token 是 reset 之后才检查的：reset 优先
        opts = MoodleDlOpts()
        opts.reset_downloaded_files = True
        opts.new_token = True
        with patch('moodle_dl.main.DatabaseManager') as mock_db:
            choose_task(config, opts)
            mock_db.return_value.reset_all_downloaded_files.assert_called_once()

    def test_reset_files_both_via_or_falls_through_to_same_handler(self):
        """``--reset-downloaded-files`` 与 ``--重置下载文件`` 都映射到
        同一个 DatabaseManager.reset_all_downloaded_files。"""
        config = MagicMock(spec=ConfigHelper)
        config.get_misc_files_path.return_value = tempfile.gettempdir()

        for attr in ('reset_downloaded_files', 'reset_downloaded_files_cn'):
            with self.subTest(attr=attr):
                opts = MoodleDlOpts()
                setattr(opts, attr, True)
                with patch('moodle_dl.main.DatabaseManager') as mock_db:
                    choose_task(config, opts)
                    mock_db.return_value.reset_all_downloaded_files.assert_called_once()


# ---------------------------------------------------------------------------
# 2) --path handling
# ---------------------------------------------------------------------------

class TestPathArg(unittest.TestCase):
    """:py:meth:`moodle_dl.main.get_parser` 中 --path 的边界行为。"""

    def test_path_to_existing_directory_accepted(self):
        """--path 指向一个已存在目录时被接受，且值会原样回填到 opts.path。"""
        with tempfile.TemporaryDirectory() as tmp:
            opts = get_parser().parse_args(['--path', tmp])
        self.assertEqual(opts.path, tmp)

    def test_path_to_missing_directory_rejected(self):
        """不存在的目录会触发 argparse 错误（_dir_path 内部抛 ArgumentTypeError）。"""
        with self.assertRaises(SystemExit) as ctx:
            get_parser().parse_args(['--path', '/this/path/does/not/exist/xyz'])
        # argparse 把 ArgumentTypeError 翻译成 usage 错误并以 2 退出
        self.assertEqual(ctx.exception.code, 2)

    def test_path_to_file_instead_of_dir_rejected(self):
        """路径存在但不是目录时也应被拒绝。"""
        with tempfile.NamedTemporaryFile() as tmp_file:
            with self.assertRaises(SystemExit) as ctx:
                get_parser().parse_args(['--path', tmp_file.name])
            self.assertEqual(ctx.exception.code, 2)

    def test_post_process_opts_fills_log_file_path_when_none(self):
        """``post_process_opts`` 不会因为 path 是相对路径而崩溃，且
        ``log_file_path is None`` 时会被填充为 ``opts.path``。"""
        opts = MoodleDlOpts()
        opts.path = '/tmp/moodle-dl'
        opts.log_file_path = None
        from moodle_dl.main import post_process_opts

        result = post_process_opts(opts)
        self.assertEqual(result.log_file_path, '/tmp/moodle-dl')


# ---------------------------------------------------------------------------
# 3) Unknown arguments => SystemExit(2)
# ---------------------------------------------------------------------------

class TestUnknownArgs(unittest.TestCase):
    """argparse 默认会拒绝未知参数，固定此行为。"""

    def test_unknown_long_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                get_parser().parse_args(['--unknown-flag', '--path', tmp])
        self.assertEqual(ctx.exception.code, 2)

    def test_unknown_flag_with_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                get_parser().parse_args(['--init', '--not-a-real-flag', '--path', tmp])
        self.assertEqual(ctx.exception.code, 2)

    def test_unknown_flag_alone_no_path(self):
        with self.assertRaises(SystemExit) as ctx:
            get_parser().parse_args(['--bogus'])
        self.assertEqual(ctx.exception.code, 2)


# ---------------------------------------------------------------------------
# 4) Missing config / first run
# ---------------------------------------------------------------------------

class TestMissingConfig(unittest.TestCase):
    """第一次运行：config.json 不存在时的处理路径。"""

    def test_no_init_no_config_exits_minus_one(self):
        """不带 --init 且 config 不存在：main() 走 NoConfigError 分支并 exit(-1)。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 不在 tmp 下放 config.json，强制 NoConfigError
            with (
                patch('moodle_dl.main.just_fix_windows_console'),
                patch('moodle_dl.main.setup_logger'),
                patch('moodle_dl.main.ConfigHelper.load',
                      side_effect=ConfigHelper.NoConfigError('no config')),
                patch('moodle_dl.main.sys.exit', side_effect=SystemExit(-1)) as mock_exit,
            ):
                with self.assertRaises(SystemExit):
                    main(['--path', tmp])

        mock_exit.assert_called_once_with(-1)

    def test_init_bypasses_config_load(self):
        """--init 路径不走 config.load()，所以 config 不存在也没关系。"""
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch('moodle_dl.main.just_fix_windows_console'),
                patch('moodle_dl.main.setup_logger'),
                patch('moodle_dl.main.ConfigHelper.load') as mock_load,
                patch('moodle_dl.main.init_config') as mock_init,
                patch('moodle_dl.main.sys.exit', side_effect=SystemExit(0)) as mock_exit,
            ):
                with self.assertRaises(SystemExit):
                    main(['--init', '--path', tmp])

        # config.load 不被调用，init_config 被调用
        mock_load.assert_not_called()
        mock_init.assert_called_once()
        mock_exit.assert_called_once_with(0)


# ---------------------------------------------------------------------------
# 5) sentry_sdk.init failure handling
# ---------------------------------------------------------------------------

class TestSentryInitFailures(unittest.TestCase):
    """``connect_sentry`` 只捕获 (ValueError, BadDsn, ServerlessTimeoutWarning)；
    任何其它异常都会向上传播。固定这一行为。"""

    def test_invalid_dsn_valueerror_is_swallowed(self):
        """DSN 非法（ValueError）→ connect_sentry 静默返回 False。"""
        config = MagicMock(spec=ConfigHelper)
        config.get_property.return_value = 'not-a-valid-dsn'

        with patch('moodle_dl.main.sentry_sdk.init',
                   side_effect=ValueError('bad DSN')) as mock_init:
            result = connect_sentry(config)

        self.assertFalse(result)
        mock_init.assert_called_once_with('not-a-valid-dsn')

    def test_bad_dsn_class_is_swallowed(self):
        """sentry_sdk.utils.BadDsn 异常也被吞掉，返回 False。"""
        config = MagicMock(spec=ConfigHelper)
        config.get_property.return_value = 'https://broken@x/1'

        with patch('moodle_dl.main.sentry_sdk.init',
                   side_effect=sentry_sdk.utils.BadDsn('bad')):
            self.assertFalse(connect_sentry(config))

    def test_runtime_error_propagates(self):
        """sentry_sdk.init 抛 RuntimeError 等未被列在 except 元组中的异常时
        会向上传播（当前实现不捕获）。锁定此行为，提醒后续维护者注意：
        ``connect_sentry`` 不具备对未知异常的降级能力。"""
        config = MagicMock(spec=ConfigHelper)
        config.get_property.return_value = 'https://abc@x/1'

        with patch('moodle_dl.main.sentry_sdk.init',
                   side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                connect_sentry(config)

    def test_no_dsn_does_not_call_init(self):
        """未配置 sentry_dsn 时不应调用 sentry_sdk.init。"""
        config = MagicMock(spec=ConfigHelper)
        config.get_property.return_value = ''  # 模拟空 DSN

        with patch('moodle_dl.main.sentry_sdk.init') as mock_init:
            result = connect_sentry(config)

        self.assertFalse(result)
        mock_init.assert_not_called()


# ---------------------------------------------------------------------------
# 6) Sub-task routing
# ---------------------------------------------------------------------------

class TestSubTaskRouting(unittest.TestCase):
    """``choose_task`` 把 --retry-failed / --resume / --refresh-cookies
    分派到对应的 handler，且不会调用默认的 run_main。"""

    def setUp(self):
        self.config = MagicMock(spec=ConfigHelper)
        self.config.get_misc_files_path.return_value = tempfile.gettempdir()

    @patch('moodle_dl.main.run_main')
    @patch('moodle_dl.main.retry_failed_downloads')
    def test_retry_failed_routes_to_handler(self, mock_retry, mock_run_main):
        """--retry-failed → 调用 retry_failed_downloads，不调用 run_main。"""
        opts = MoodleDlOpts()
        opts.retry_failed = True
        choose_task(self.config, opts)

        mock_retry.assert_called_once_with(self.config, opts)
        mock_run_main.assert_not_called()

    @patch('moodle_dl.main.run_main')
    @patch('moodle_dl.main.refresh_cookies_only')
    def test_refresh_cookies_routes_to_handler(self, mock_refresh, mock_run_main):
        """--refresh-cookies → 调用 refresh_cookies_only，不调用 run_main。"""
        opts = MoodleDlOpts()
        opts.refresh_cookies = True
        choose_task(self.config, opts)

        mock_refresh.assert_called_once_with(self.config, opts)
        mock_run_main.assert_not_called()

    @patch('moodle_dl.main.run_main')
    @patch('moodle_dl.main.resume_downloads')
    def test_resume_routes_to_handler(self, mock_resume, mock_run_main):
        """--resume → 调用 resume_downloads；run_main 由 resume_downloads 内部
        触发，choose_task 自己不直接调用。"""
        opts = MoodleDlOpts()
        opts.resume = True
        choose_task(self.config, opts)

        mock_resume.assert_called_once_with(self.config, opts)
        # resume_downloads 内部会再调 run_main；但 choose_task 自身不会
        mock_run_main.assert_not_called()

    @patch('moodle_dl.main.run_main')
    def test_default_routes_to_run_main(self, mock_run_main):
        """没有任何标志时 fallback 到 run_main。"""
        opts = MoodleDlOpts()
        choose_task(self.config, opts)
        mock_run_main.assert_called_once_with(self.config, opts)

    @patch('moodle_dl.main.retry_failed_downloads')
    @patch('moodle_dl.main.resume_downloads')
    @patch('moodle_dl.main.refresh_cookies_only')
    @patch('moodle_dl.main.run_main')
    def test_refresh_cookies_takes_priority_over_resume_and_retry(
        self, mock_run_main, mock_refresh, mock_resume, mock_retry,
    ):
        """``choose_task`` 的 if-elif 顺序：refresh_cookies 排在 resume 和
        retry_failed 之前。同时设置三个时，refresh_cookies 胜出。"""
        opts = MoodleDlOpts()
        opts.retry_failed = True
        opts.resume = True
        opts.refresh_cookies = True
        choose_task(self.config, opts)

        mock_refresh.assert_called_once()
        mock_resume.assert_not_called()
        mock_retry.assert_not_called()
        mock_run_main.assert_not_called()


# ---------------------------------------------------------------------------
# 7) AuthMigrator rollback / first-run
# ---------------------------------------------------------------------------

class TestAuthMigratorRollback(unittest.TestCase):
    """``migrate_auth_to_db.AuthMigrator`` 在 DB 操作失败时应执行 rollback；
    第一次运行（无 config.json）时应在 validate_paths 阶段直接跳过。"""

    def test_validate_paths_skips_when_no_config(self):
        """首次运行：config.json 不存在 → validate_paths 返回 False，run() 因此
        不会触碰数据库。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 不要在 tmp 下放 config.json
            migrator = AuthMigrator(tmp)
            self.assertFalse(migrator.validate_paths())

    def test_run_returns_false_when_no_config(self):
        """无 config.json 时 run() 直接返回 False，不打开数据库连接。"""
        with tempfile.TemporaryDirectory() as tmp:
            migrator = AuthMigrator(tmp)
            with patch('sqlite3.connect') as mock_connect:
                result = migrator.run()

        self.assertFalse(result)
        mock_connect.assert_not_called()

    def test_create_token_session_rollback_on_db_error(self):
        """create_token_session 在 INSERT 失败时应 conn.rollback()，并返回 None。"""
        with tempfile.TemporaryDirectory() as tmp:
            migrator = AuthMigrator(tmp)
            migrator.config = {'token': 'abc123', 'privatetoken': 'xyz'}

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.execute.side_effect = sqlite3.OperationalError('disk full')
            mock_conn.cursor.return_value = mock_cursor

            result = migrator.create_token_session(mock_conn)

            self.assertIsNone(result)
            mock_conn.rollback.assert_called_once()
            # commit 不应被调用，因为 execute 在 commit 之前就抛了
            mock_conn.commit.assert_not_called()

    def test_create_cookie_session_rollback_on_db_error(self):
        """create_cookie_session 在执行过程中遇到异常时应 conn.rollback()。"""
        with tempfile.TemporaryDirectory() as tmp:
            migrator = AuthMigrator(tmp)
            migrator.existing_cookies = [
                {'name': 'c1', 'value': 'v1', 'domain': '.ex.com',
                 'path': '/', 'secure': 1, 'expires': None, 'httponly': 1, 'samesite': 'Lax'}
            ]

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            # 让第二次 execute（第一次成功插 session，第二次在 cookie insert 时炸）
            mock_cursor.execute.side_effect = [
                MagicMock(),  # INSERT INTO auth_sessions 成功
                sqlite3.OperationalError('boom'),  # 插入 cookie_store 时失败
            ]
            mock_conn.cursor.return_value = mock_cursor

            result = migrator.create_cookie_session(mock_conn)

            self.assertIsNone(result)
            mock_conn.rollback.assert_called_once()

    def test_create_token_session_rollback_when_no_token(self):
        """config 中无 token 时不应走 DB 写入；保持"无副作用"语义。"""
        with tempfile.TemporaryDirectory() as tmp:
            migrator = AuthMigrator(tmp)
            migrator.config = {}  # 无 token

            mock_conn = MagicMock()
            result = migrator.create_token_session(mock_conn)

            self.assertIsNone(result)
            mock_conn.cursor.assert_not_called()
            mock_conn.commit.assert_not_called()
            mock_conn.rollback.assert_not_called()

    def test_run_with_missing_db_returns_false_and_skips_migration(self):
        """config.json 存在但 db 文件不存在：validate_paths 失败，run() 不动 DB。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 放 config.json 但不放 db
            with open(os.path.join(tmp, 'config.json'), 'w') as f:
                f.write('{"token": "x"}')
            migrator = AuthMigrator(tmp)

            with patch('sqlite3.connect') as mock_connect:
                result = migrator.run()

            self.assertFalse(result)
            mock_connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
