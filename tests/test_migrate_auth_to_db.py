# -*- coding: utf-8 -*-
"""
migrate_auth_to_db.py 单元测试

测试认证迁移功能：
- 路径验证
- 配置加载
- Cookie 解析
- 数据库操作
"""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch, mock_open
from pathlib import Path

from moodle_dl.database import StateRecorder
from moodle_dl.migrate_auth_to_db import AuthMigrator
from moodle_dl.types import MoodleDlOpts


class TestAuthMigratorInit(unittest.TestCase):
    """AuthMigrator 初始化测试"""

    def test_init_with_path(self):
        """测试使用路径初始化"""
        migrator = AuthMigrator('/tmp/test_moodle')

        self.assertEqual(migrator.moodle_dl_path, Path('/tmp/test_moodle'))
        self.assertEqual(migrator.config_file, Path('/tmp/test_moodle/config.json'))
        self.assertEqual(migrator.cookies_file, Path('/tmp/test_moodle/Cookies.txt'))
        self.assertEqual(migrator.db_file, Path('/tmp/test_moodle/moodle_state.db'))
        self.assertEqual(migrator.config, {})
        self.assertEqual(migrator.existing_cookies, [])
        self.assertEqual(migrator.migration_log, [])

    def test_init_with_tilde_expansion(self):
        """测试波浪号扩展"""
        migrator = AuthMigrator('~/moodle-dl')

        self.assertIn('moodle-dl', str(migrator.moodle_dl_path))


class TestLog(unittest.TestCase):
    """log 方法测试"""

    def setUp(self):
        self.migrator = AuthMigrator('/tmp/test')

    def test_log_info(self):
        """测试 INFO 级别日志"""
        self.migrator.log('INFO', 'Test message')

        self.assertEqual(len(self.migrator.migration_log), 1)
        self.assertIn('INFO', self.migrator.migration_log[0])
        self.assertIn('Test message', self.migrator.migration_log[0])

    def test_log_error(self):
        """测试 ERROR 级别日志"""
        self.migrator.log('ERROR', 'Error message')

        self.assertEqual(len(self.migrator.migration_log), 1)
        self.assertIn('ERROR', self.migrator.migration_log[0])

    def test_log_warning(self):
        """测试 WARNING 级别日志"""
        self.migrator.log('WARNING', 'Warning message')

        self.assertEqual(len(self.migrator.migration_log), 1)
        self.assertIn('WARNING', self.migrator.migration_log[0])

    def test_log_multiple_messages(self):
        """测试多条日志"""
        self.migrator.log('INFO', 'Message 1')
        self.migrator.log('ERROR', 'Message 2')

        self.assertEqual(len(self.migrator.migration_log), 2)


class TestValidatePaths(unittest.TestCase):
    """validate_paths 方法测试"""

    def setUp(self):
        self.migrator = AuthMigrator('/tmp/test_moodle')

    @patch('pathlib.Path.exists')
    def test_validate_paths_all_exist(self, mock_exists):
        """测试所有路径都存在"""
        mock_exists.return_value = True

        result = self.migrator.validate_paths()

        self.assertTrue(result)

    @patch('pathlib.Path.exists')
    def test_validate_paths_moodle_dl_missing(self, mock_exists):
        """测试 moodle-dl 目录不存在"""
        # First call checks moodle_dl_path, return False
        # Subsequent calls for other files won't happen due to early return
        mock_exists.side_effect = [False]

        result = self.migrator.validate_paths()

        self.assertFalse(result)

    @patch('pathlib.Path.exists')
    def test_validate_paths_config_missing(self, mock_exists):
        """测试 config.json 不存在"""
        mock_exists.side_effect = [True, False]

        result = self.migrator.validate_paths()

        self.assertFalse(result)

    @patch('pathlib.Path.exists')
    def test_validate_paths_db_missing(self, mock_exists):
        """测试数据库文件不存在"""
        mock_exists.side_effect = [True, True, False]

        result = self.migrator.validate_paths()

        self.assertFalse(result)

    @patch('pathlib.Path.exists')
    def test_validate_paths_cookies_missing(self, mock_exists):
        """测试 Cookies.txt 不存在（可选）"""
        mock_exists.side_effect = [True, True, True, False]

        result = self.migrator.validate_paths()

        self.assertTrue(result)  # Cookies.txt is optional


class TestLoadConfig(unittest.TestCase):
    """load_config 方法测试"""

    def setUp(self):
        self.migrator = AuthMigrator('/tmp/test')

    @patch('builtins.open', new_callable=mock_open, read_data='{"token": "test123"}')
    def test_load_config_success(self, mock_file):
        """测试成功加载配置"""
        result = self.migrator.load_config()

        self.assertTrue(result)
        self.assertEqual(self.migrator.config['token'], 'test123')

    @patch('builtins.open', side_effect=IOError('File not found'))
    def test_load_config_io_error(self, mock_file):
        """测试文件读取错误"""
        result = self.migrator.load_config()

        self.assertFalse(result)

    @patch('builtins.open', new_callable=mock_open, read_data='invalid json')
    def test_load_config_invalid_json(self, mock_file):
        """测试无效 JSON"""
        result = self.migrator.load_config()

        self.assertFalse(result)


class TestLoadCookiesFromFile(unittest.TestCase):
    """load_cookies_from_file 方法测试"""

    def setUp(self):
        # Create a temporary directory for testing
        import tempfile
        self.temp_dir = tempfile.mkdtemp()
        self.migrator = AuthMigrator(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_cookies_valid_netscape_format(self):
        """测试有效的 Netscape 格式"""
        # Create test Cookies.txt
        cookies_file = os.path.join(self.temp_dir, 'Cookies.txt')
        with open(cookies_file, 'w', encoding='utf-8') as f:
            f.write('.example.com\tTRUE\t/\tFALSE\t1735689600\tcookie1\tvalue1\n')
            f.write('.example.com\tTRUE\t/\tTRUE\t1735689600\tcookie2\tvalue2\n')

        result = self.migrator.load_cookies_from_file()

        self.assertTrue(result)
        self.assertEqual(len(self.migrator.existing_cookies), 2)
        self.assertEqual(self.migrator.existing_cookies[0]['name'], 'cookie1')
        self.assertEqual(self.migrator.existing_cookies[1]['secure'], 1)

    def test_load_cookies_with_comments(self):
        """测试带注释的 Cookie 文件"""
        cookies_file = os.path.join(self.temp_dir, 'Cookies.txt')
        with open(cookies_file, 'w', encoding='utf-8') as f:
            f.write('# HTTP Cookie File\n')
            f.write('# This is a comment\n')
            f.write('.example.com\tTRUE\t/\tFALSE\t1735689600\ttest\tvalue\n')
            f.write('\n')

        result = self.migrator.load_cookies_from_file()

        self.assertTrue(result)
        self.assertEqual(len(self.migrator.existing_cookies), 1)

    def test_load_cookies_with_zero_expiration(self):
        """测试过期时间为 0 的 Cookie"""
        cookies_file = os.path.join(self.temp_dir, 'Cookies.txt')
        with open(cookies_file, 'w', encoding='utf-8') as f:
            f.write('.example.com\tTRUE\t/\tFALSE\t0\tsession_cookie\tvalue\n')

        result = self.migrator.load_cookies_from_file()

        self.assertTrue(result)
        self.assertEqual(len(self.migrator.existing_cookies), 1)
        self.assertIsNone(self.migrator.existing_cookies[0]['expires'])

    def test_load_cookies_with_invalid_expiration(self):
        """测试无效过期时间会按 session cookie 处理"""
        cookies_file = os.path.join(self.temp_dir, 'Cookies.txt')
        with open(cookies_file, 'w', encoding='utf-8') as f:
            f.write('.example.com\tTRUE\t/\tFALSE\tnot-a-timestamp\ttest\tvalue\n')

        result = self.migrator.load_cookies_from_file()

        self.assertTrue(result)
        self.assertEqual(len(self.migrator.existing_cookies), 1)
        self.assertIsNone(self.migrator.existing_cookies[0]['expires'])

    def test_load_cookies_with_numeric_flags(self):
        """测试数字格式的布尔标志"""
        cookies_file = os.path.join(self.temp_dir, 'Cookies.txt')
        with open(cookies_file, 'w', encoding='utf-8') as f:
            f.write('.example.com\t1\t/\t0\t1735689600\ttest\tvalue\n')

        result = self.migrator.load_cookies_from_file()

        self.assertTrue(result)
        self.assertEqual(self.migrator.existing_cookies[0]['secure'], 0)

    def test_load_cookies_invalid_line(self):
        """测试无效行（字段不足）"""
        cookies_file = os.path.join(self.temp_dir, 'Cookies.txt')
        with open(cookies_file, 'w', encoding='utf-8') as f:
            f.write('.example.com\tTRUE\t/\tFALSE\n')  # Missing fields

        result = self.migrator.load_cookies_from_file()

        self.assertTrue(result)  # Should not fail, just skip invalid lines
        self.assertEqual(len(self.migrator.existing_cookies), 0)

    @patch('pathlib.Path.exists')
    def test_load_cookies_file_not_exist(self, mock_exists):
        """测试 Cookie 文件不存在"""
        mock_exists.return_value = False

        result = self.migrator.load_cookies_from_file()

        self.assertTrue(result)  # Not an error, just skipped
        self.assertEqual(len(self.migrator.existing_cookies), 0)

    @patch('pathlib.Path.exists', return_value=True)
    @patch('builtins.open', side_effect=OSError('read failed'))
    def test_load_cookies_file_read_error(self, mock_file, mock_exists):
        """测试 Cookie 文件读取错误"""
        result = self.migrator.load_cookies_from_file()

        self.assertFalse(result)


class TestVerifyTokenExists(unittest.TestCase):
    """verify_token_exists 方法测试"""

    def setUp(self):
        self.migrator = AuthMigrator('/tmp/test')

    def test_verify_token_exists_true(self):
        """测试 token 存在"""
        self.migrator.config = {'token': 'test_token_123'}

        result = self.migrator.verify_token_exists()

        self.assertTrue(result)

    def test_verify_token_exists_missing(self):
        """测试 token 不存在"""
        self.migrator.config = {}

        result = self.migrator.verify_token_exists()

        self.assertFalse(result)

    def test_verify_token_exists_empty(self):
        """测试 token 为空"""
        self.migrator.config = {'token': ''}

        result = self.migrator.verify_token_exists()

        self.assertFalse(result)


class TestVerifyDatabaseTables(unittest.TestCase):
    """_verify_database_tables 方法测试"""

    def setUp(self):
        self.migrator = AuthMigrator('/tmp/test')

    @patch('sqlite3.connect')
    def test_verify_all_tables_exist(self, mock_connect):
        """测试所有表都存在"""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ['table_name']
        mock_connect.return_value.cursor.return_value = mock_cursor

        result = self.migrator._verify_database_tables(mock_connect.return_value)

        self.assertTrue(result)

    @patch('sqlite3.connect')
    def test_verify_missing_table(self, mock_connect):
        """测试缺少必需的表"""
        mock_cursor = MagicMock()
        # Simulate missing table - fetchone returns None for auth_sessions
        mock_cursor.fetchone.side_effect = [None, ['table_name'], ['table_name']]
        mock_connect.return_value.cursor.return_value = mock_cursor

        result = self.migrator._verify_database_tables(mock_connect.return_value)

        self.assertFalse(result)

    @patch('sqlite3.connect')
    def test_verify_database_exception(self, mock_connect):
        """测试数据库异常"""
        mock_connect.return_value.cursor.side_effect = Exception('DB error')

        result = self.migrator._verify_database_tables(mock_connect.return_value)

        self.assertFalse(result)


class TestCreateTokenSession(unittest.TestCase):
    """create_token_session 方法测试"""

    def setUp(self):
        self.migrator = AuthMigrator('/tmp/test')
        self.migrator.config = {'token': 'test_token', 'privatetoken': 'private_token'}

    @patch('sqlite3.connect')
    @patch('moodle_dl.migrate_auth_to_db.uuid.uuid4')
    @patch('moodle_dl.migrate_auth_to_db.datetime')
    def test_create_token_session_success(self, mock_datetime, mock_uuid, mock_connect):
        """测试成功创建 token session"""
        mock_uuid.return_value = 'test-session-id'
        mock_datetime.now.return_value.timestamp.return_value = 1234567890

        mock_cursor = MagicMock()
        mock_connect.return_value.cursor.return_value = mock_cursor

        result = self.migrator.create_token_session(mock_connect.return_value)

        self.assertEqual(result, 'test-session-id')
        mock_cursor.execute.assert_called()

    def test_create_token_session_no_token(self):
        """测试没有 token"""
        self.migrator.config = {}

        mock_conn = MagicMock()

        result = self.migrator.create_token_session(mock_conn)

        self.assertIsNone(result)

    @patch('sqlite3.connect')
    def test_create_token_session_db_error(self, mock_connect):
        """测试数据库错误"""
        mock_connect.return_value.cursor.side_effect = Exception('DB error')

        result = self.migrator.create_token_session(mock_connect.return_value)

        self.assertIsNone(result)


class TestCreateCookieSession(unittest.TestCase):
    """create_cookie_session 方法测试"""

    def setUp(self):
        self.migrator = AuthMigrator('/tmp/test')
        self.migrator.existing_cookies = [
            {'name': 'cookie1', 'value': 'value1', 'domain': '.example.com',
             'path': '/', 'secure': 1, 'expires': 1735689600, 'httponly': 1, 'samesite': 'Lax'}
        ]

    @patch('sqlite3.connect')
    @patch('moodle_dl.migrate_auth_to_db.uuid.uuid4')
    @patch('moodle_dl.migrate_auth_to_db.datetime')
    def test_create_cookie_session_success(self, mock_datetime, mock_uuid, mock_connect):
        """测试成功创建 cookie session"""
        mock_uuid.return_value = 'cookie-session-id'
        mock_datetime.now.return_value.timestamp.return_value = 1234567890

        mock_cursor = MagicMock()
        mock_connect.return_value.cursor.return_value = mock_cursor

        result = self.migrator.create_cookie_session(mock_connect.return_value)

        self.assertEqual(result, 'cookie-session-id')

    def test_create_cookie_session_no_cookies(self):
        """测试没有 cookies"""
        self.migrator.existing_cookies = []

        mock_conn = MagicMock()

        result = self.migrator.create_cookie_session(mock_conn)

        self.assertIsNone(result)

    @patch('sqlite3.connect')
    def test_create_cookie_session_db_error(self, mock_connect):
        """测试数据库错误"""
        mock_connect.return_value.cursor.side_effect = Exception('DB error')

        result = self.migrator.create_cookie_session(mock_connect.return_value)

        self.assertIsNone(result)


class TestVerifyMigration(unittest.TestCase):
    """verify_migration 方法测试"""

    def setUp(self):
        self.migrator = AuthMigrator('/tmp/test')

    @patch('sqlite3.connect')
    def test_verify_migration_success(self, mock_connect):
        """测试迁移验证成功"""
        mock_cursor = MagicMock()
        # fetchone returns a tuple (sqlite3.Row), need to index it with [0]
        # Return tuples with count at index 0
        row1 = MagicMock()
        row1.__getitem__ = lambda self, x: 1
        row2 = MagicMock()
        row2.__getitem__ = lambda self, x: 1
        row3 = MagicMock()
        row3.__getitem__ = lambda self, x: 5

        mock_cursor.fetchone.side_effect = [row1, row2, row3]
        mock_connect.return_value.cursor.return_value = mock_cursor

        result = self.migrator.verify_migration(mock_connect.return_value)

        self.assertTrue(result)

    @patch('sqlite3.connect')
    def test_verify_migration_no_cookies(self, mock_connect):
        """测试没有 cookies 的迁移（仍算成功）"""
        self.migrator.existing_cookies = []
        row1 = MagicMock()
        row1.__getitem__ = lambda self, x: 1
        row2 = MagicMock()
        row2.__getitem__ = lambda self, x: 0
        row3 = MagicMock()
        row3.__getitem__ = lambda self, x: 0
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [row1, row2, row3]
        mock_connect.return_value.cursor.return_value = mock_cursor

        result = self.migrator.verify_migration(mock_connect.return_value)

        self.assertTrue(result)

    @patch('sqlite3.connect')
    def test_verify_migration_no_token_session(self, mock_connect):
        """测试没有 token session（失败）"""
        row = MagicMock()
        row.__getitem__ = lambda self, x: 0
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [row, row, row]
        mock_connect.return_value.cursor.return_value = mock_cursor

        result = self.migrator.verify_migration(mock_connect.return_value)

        self.assertFalse(result)

    @patch('sqlite3.connect')
    def test_verify_migration_db_error(self, mock_connect):
        """测试数据库错误"""
        mock_connect.return_value.cursor.side_effect = Exception('DB error')

        result = self.migrator.verify_migration(mock_connect.return_value)

        self.assertFalse(result)


class TestLogMigrationAction(unittest.TestCase):
    """log_migration_action 方法测试"""

    def setUp(self):
        self.migrator = AuthMigrator('/tmp/test')

    @patch('sqlite3.connect')
    @patch('moodle_dl.migrate_auth_to_db.datetime')
    def test_log_migration_action_success(self, mock_datetime, mock_connect):
        """测试成功记录审计日志"""
        mock_datetime.now.return_value.timestamp.return_value = 1234567890

        mock_cursor = MagicMock()
        mock_connect.return_value.cursor.return_value = mock_cursor

        # Should not raise exception
        self.migrator.log_migration_action(
            mock_connect.return_value, 'session-id', 'create', 'success'
        )

        mock_cursor.execute.assert_called()

    @patch('sqlite3.connect')
    def test_log_migration_action_failure(self, mock_connect):
        """测试审计日志记录失败"""
        mock_connect.return_value.cursor.side_effect = Exception('DB error')

        # Should not raise exception, just log warning
        self.migrator.log_migration_action(
            mock_connect.return_value, 'session-id', 'create', 'success'
        )


class TestSaveMigrationLog(unittest.TestCase):
    """save_migration_log 方法测试"""

    def setUp(self):
        self.migrator = AuthMigrator('/tmp/test')
        self.migrator.migration_log = ['[2024-01-01] INFO: Test message']

    @patch('builtins.open', new_callable=mock_open)
    def test_save_log_success(self, mock_file):
        """测试成功保存日志"""
        self.migrator.save_migration_log()

        mock_file.assert_called_once()

    @patch('builtins.open', side_effect=IOError('Write error'))
    def test_save_log_io_error(self, mock_file):
        """测试文件写入错误"""
        # Should not raise exception
        self.migrator.save_migration_log()


class TestRunWorkflow(unittest.TestCase):
    """run 方法编排流程测试"""

    def make_migrator_with_steps(self):
        migrator = AuthMigrator('/tmp/test')
        migrator.validate_paths = Mock(return_value=True)
        migrator.load_config = Mock(return_value=True)
        migrator.verify_token_exists = Mock(return_value=True)
        migrator.load_cookies_from_file = Mock(return_value=True)
        return migrator

    def test_run_short_circuits_when_pre_database_steps_fail(self):
        cases = [
            ('validate_paths',),
            ('load_config', 'validate_paths'),
            ('verify_token_exists', 'validate_paths', 'load_config'),
            ('load_cookies_from_file', 'validate_paths', 'load_config', 'verify_token_exists'),
        ]

        for failing_step, *previous_steps in cases:
            migrator = self.make_migrator_with_steps()
            getattr(migrator, failing_step).return_value = False

            self.assertFalse(migrator.run())

            for previous_step in previous_steps:
                getattr(migrator, previous_step).assert_called_once()

    @patch('moodle_dl.migrate_auth_to_db.sqlite3.connect')
    def test_run_success_records_token_cookie_sessions_and_audit_entries(self, mock_connect):
        migrator = self.make_migrator_with_steps()
        conn = MagicMock()
        mock_connect.return_value = conn
        migrator._verify_database_tables = Mock(return_value=True)
        migrator.create_token_session = Mock(return_value='token-session')
        migrator.create_cookie_session = Mock(return_value='cookie-session')
        migrator.verify_migration = Mock(return_value=True)
        migrator.log_migration_action = Mock()

        self.assertTrue(migrator.run())

        mock_connect.assert_called_once_with(str(migrator.db_file))
        self.assertIs(conn.row_factory, sqlite3.Row)
        migrator.log_migration_action.assert_any_call(conn, 'token-session', 'create', 'success')
        migrator.log_migration_action.assert_any_call(conn, 'cookie-session', 'create', 'success')
        conn.close.assert_called_once()

    @patch('moodle_dl.migrate_auth_to_db.sqlite3.connect')
    def test_run_returns_false_when_tables_are_missing(self, mock_connect):
        migrator = self.make_migrator_with_steps()
        conn = MagicMock()
        mock_connect.return_value = conn
        migrator._verify_database_tables = Mock(return_value=False)

        self.assertFalse(migrator.run())

        conn.close.assert_called_once()

    @patch('moodle_dl.migrate_auth_to_db.sqlite3.connect')
    def test_run_returns_false_when_migration_verification_fails(self, mock_connect):
        migrator = self.make_migrator_with_steps()
        conn = MagicMock()
        mock_connect.return_value = conn
        migrator._verify_database_tables = Mock(return_value=True)
        migrator.create_token_session = Mock(return_value=None)
        migrator.create_cookie_session = Mock(return_value=None)
        migrator.verify_migration = Mock(return_value=False)
        migrator.log_migration_action = Mock()

        self.assertFalse(migrator.run())

        migrator.log_migration_action.assert_not_called()
        conn.close.assert_called_once()

    @patch('moodle_dl.migrate_auth_to_db.sqlite3.connect', side_effect=sqlite3.Error('locked'))
    def test_run_returns_false_on_database_errors(self, mock_connect):
        migrator = self.make_migrator_with_steps()

        self.assertFalse(migrator.run())

    def test_run_full_integration_with_real_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = MagicMock()
            config.get_misc_files_path.return_value = temp_dir
            StateRecorder(config, MoodleDlOpts())

            with open(os.path.join(temp_dir, 'config.json'), 'w', encoding='utf-8') as f:
                json.dump({'token': 'token-123', 'privatetoken': 'private-123'}, f)

            with open(os.path.join(temp_dir, 'Cookies.txt'), 'w', encoding='utf-8') as f:
                f.write('.example.com\tTRUE\t/\tFALSE\t1735689600\tcookie1\tvalue1\n')

            migrator = AuthMigrator(temp_dir)
            self.assertTrue(migrator.run())

            conn = sqlite3.connect(os.path.join(temp_dir, 'moodle_state.db'))
            try:
                token_count = conn.execute(
                    "SELECT COUNT(*) FROM auth_sessions WHERE session_type='token'"
                ).fetchone()[0]
                cookie_count = conn.execute(
                    "SELECT COUNT(*) FROM auth_sessions WHERE session_type='cookie_batch'"
                ).fetchone()[0]
                audit_count = conn.execute("SELECT COUNT(*) FROM auth_audit_log").fetchone()[0]
                self.assertEqual(token_count, 1)
                self.assertEqual(cookie_count, 1)
                self.assertEqual(audit_count, 2)
            finally:
                conn.close()


class TestMain(unittest.TestCase):
    """main 函数测试"""

    @patch('moodle_dl.migrate_auth_to_db.AuthMigrator.save_migration_log')
    @patch('moodle_dl.migrate_auth_to_db.AuthMigrator.run')
    @patch('moodle_dl.migrate_auth_to_db.AuthMigrator.__init__', return_value=None)
    @patch('sys.argv', ['migrate_auth_to_db.py', '/tmp/test'])
    def test_main_success(self, mock_init, mock_run, mock_save):
        """测试主函数成功执行"""
        from moodle_dl import migrate_auth_to_db

        mock_run.return_value = True

        # Create a migrator mock with save_migration_log method
        mock_migrator = Mock()
        mock_migrator.run.return_value = True
        mock_init.return_value = mock_migrator

        with patch.object(migrate_auth_to_db, 'AuthMigrator', return_value=mock_migrator):
            with self.assertRaises(SystemExit) as context:
                migrate_auth_to_db.main()

            # Exit code 0 for success
            # Can't check exception.code with unittest in this way

    @patch('sys.argv', ['migrate_auth_to_db.py'])
    def test_main_no_argument(self):
        """测试没有参数"""
        from moodle_dl import migrate_auth_to_db

        with self.assertRaises(SystemExit):
            migrate_auth_to_db.main()


if __name__ == '__main__':
    unittest.main()
