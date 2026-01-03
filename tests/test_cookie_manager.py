# -*- coding: utf-8 -*-
"""CookieManager 的核心行为测试。"""

import unittest
from unittest.mock import MagicMock, patch

from moodle_dl.cookie_manager import CookieManager


class TestCookieManager(unittest.TestCase):
    """验证 CookieManager 在有/无数据库场景下的逻辑分支。"""

    def setUp(self):
        self.config = MagicMock()
        self.moodle_domain = 'keats.kcl.ac.uk'
        self.cookies_path = '/tmp/cookies.txt'
        self.auth_patcher = patch('moodle_dl.auth_session_manager.AuthSessionManager', create=True)
        self.mock_auth_cls = self.auth_patcher.start()
        self.addCleanup(self.auth_patcher.stop)

    def test_get_cookies_without_db_returns_none(self):
        """不提供数据库时应直接返回 None。"""
        manager = CookieManager(self.config, self.moodle_domain, self.cookies_path)
        self.assertIsNone(manager.get_cookies_from_db())

    def test_save_cookies_calls_create_session(self):
        """save_cookies_to_db 应调用 AuthSessionManager.create_session。"""
        mock_auth = MagicMock()
        self.mock_auth_cls.return_value = mock_auth
        manager = CookieManager(
            self.config, self.moodle_domain, self.cookies_path, db_file='moodle_state.db'
        )

        cookies = [{'name': 'MoodleSession', 'value': 'abc'}]
        mock_auth.create_session.return_value = 'session123'

        session_id = manager.save_cookies_to_db(cookies, source='auto')

        mock_auth.create_session.assert_called_once()
        self.assertEqual(session_id, 'session123')

    def test_get_cookies_from_db_fetches_session(self):
        """get_cookies_from_db 应优先使用有效 session 并返回 cookies。"""
        mock_auth = MagicMock()
        self.mock_auth_cls.return_value = mock_auth
        manager = CookieManager(
            self.config, self.moodle_domain, self.cookies_path, db_file='moodle_state.db'
        )

        mock_auth.get_valid_session.return_value = {'session_id': 'session123'}
        mock_auth.get_session_cookies.return_value = [{'name': 'MoodleSession'}]

        cookies = manager.get_cookies_from_db()

        mock_auth.get_session_cookies.assert_called_once_with('session123')
        self.assertEqual(cookies, [{'name': 'MoodleSession'}])

    def test_refresh_session_creates_new_version(self):
        """refresh_session_with_new_cookies 有旧 session 时应调用 refresh_session。"""
        mock_auth = MagicMock()
        self.mock_auth_cls.return_value = mock_auth
        manager = CookieManager(
            self.config, self.moodle_domain, self.cookies_path, db_file='moodle_state.db'
        )

        mock_auth.get_valid_session.return_value = {'session_id': 'session123'}
        mock_auth.refresh_session.return_value = 'session124'

        result = manager.refresh_session_with_new_cookies([{'name': 'test'}])

        mock_auth.refresh_session.assert_called_once()
        self.assertEqual(result, 'session124')

    def test_refresh_session_without_existing_session_falls_back_to_save(self):
        """没有旧 session 时 refresh 应退回到 save 流程。"""
        mock_auth = MagicMock()
        self.mock_auth_cls.return_value = mock_auth
        manager = CookieManager(
            self.config, self.moodle_domain, self.cookies_path, db_file='moodle_state.db'
        )

        mock_auth.get_valid_session.return_value = None
        mock_auth.create_session.return_value = 'session200'

        result = manager.refresh_session_with_new_cookies([{'name': 'test'}])

        mock_auth.create_session.assert_called_once()
        self.assertEqual(result, 'session200')


if __name__ == '__main__':
    unittest.main()
