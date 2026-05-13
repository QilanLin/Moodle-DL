# -*- coding: utf-8 -*-
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from moodle_dl.config import ConfigHelper
from moodle_dl.types import MoodleDlOpts


class TestConfigHelperInit(unittest.TestCase):
    def test_database_init_failure_aborts_immediately(self):
        opts = MoodleDlOpts()

        with tempfile.TemporaryDirectory() as temp_dir:
            opts.path = temp_dir

            with patch('moodle_dl.database.StateRecorder', side_effect=RuntimeError('attempt to write a readonly database')):
                with patch('moodle_dl.auth_session_manager.AuthSessionManager') as mock_auth_manager:
                    with self.assertRaises(RuntimeError) as context:
                        ConfigHelper(opts)

        error_text = str(context.exception)
        self.assertIn('数据库初始化失败', error_text)
        self.assertIn('attempt to write a readonly database', error_text)
        self.assertIn('moodle_state.db', error_text)
        self.assertIn('这不是浏览器类型问题', error_text)
        mock_auth_manager.assert_not_called()

    def test_database_init_success_continues_to_auth_manager(self):
        opts = MoodleDlOpts()

        with tempfile.TemporaryDirectory() as temp_dir:
            opts.path = temp_dir

            with patch('moodle_dl.database.StateRecorder', return_value=MagicMock()):
                with patch('moodle_dl.auth_session_manager.AuthSessionManager', return_value=MagicMock()) as mock_auth_manager:
                    config = ConfigHelper(opts)

        self.assertIsNotNone(config)
        mock_auth_manager.assert_called_once()

    def test_auth_manager_init_failure_is_reported(self):
        opts = MoodleDlOpts()

        with tempfile.TemporaryDirectory() as temp_dir:
            opts.path = temp_dir

            with patch('moodle_dl.database.StateRecorder', return_value=MagicMock()):
                with patch('moodle_dl.auth_session_manager.AuthSessionManager', return_value=None):
                    with self.assertRaises(RuntimeError) as context:
                        ConfigHelper(opts)

        self.assertIn('认证管理器初始化失败', str(context.exception))


if __name__ == '__main__':
    unittest.main()
