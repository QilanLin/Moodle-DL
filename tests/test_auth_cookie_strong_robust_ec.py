# -*- coding: utf-8 -*-
"""
Auth/Cookie 关键路径测试（Strong Robust Equivalence Class）。
"""

import unittest
from unittest.mock import MagicMock, patch

from moodle_dl.cookie_manager import CookieManager
from moodle_dl.moodle.cookie_handler import CookieHandler
from moodle_dl.types import MoodleDlOpts


class TestCookieHandlerStrongRobustEC(unittest.TestCase):
    def setUp(self):
        self.request_helper = MagicMock()
        self.request_helper.url_base = "https://moodle.example.com"
        self.request_helper.moodle_url = MagicMock()
        self.request_helper.moodle_url.domain = "moodle.example.com"
        self.config = MagicMock()
        self.opts = MagicMock(spec=MoodleDlOpts)
        self.handler = CookieHandler(self.request_helper, 2016120500, self.config, self.opts)

    def test_test_cookies_strong_robust_classes(self):
        # Strong robust classes for test_cookies:
        # E1 response has logout marker -> valid
        # E2 redirect to login/enrol -> invalid
        # E3 has moodle marker -> valid
        # E4 has error marker -> invalid
        # E5 very short response -> invalid
        # E6 uncertain but long response -> valid(default)
        cases = [
            {
                "name": "valid-logout-marker",
                "text": '<a href="/login/logout.php">Logout</a>',
                "url": "https://moodle.example.com/",
                "expected": True,
            },
            {
                "name": "invalid-login-redirect",
                "text": "Please login",
                "url": "https://moodle.example.com/login/index.php",
                "expected": False,
            },
            {
                "name": "valid-moodle-marker",
                "text": "Welcome to Moodle dashboard",
                "url": "https://moodle.example.com/",
                "expected": True,
            },
            {
                "name": "invalid-error-marker",
                "text": "User is not logged in to this site",
                "url": "https://moodle.example.com/",
                "expected": False,
            },
            {
                "name": "invalid-short-response",
                "text": "short",
                "url": "https://moodle.example.com/",
                "expected": False,
            },
            {
                "name": "valid-default-long-uncertain",
                "text": "x" * 150,
                "url": "https://moodle.example.com/course/view.php?id=2",
                "expected": True,
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                response = MagicMock()
                response.text = case["text"]
                response.url = case["url"]
                self.request_helper.get_URL.return_value = (response, None)
                self.assertEqual(self.handler.test_cookies(), case["expected"])

    @patch.object(CookieHandler, "_try_refresh_from_browser")
    @patch.object(CookieHandler, "fetch_autologin_key")
    @patch.object(CookieHandler, "test_cookies")
    def test_check_and_fetch_cookies_robust_paths(self, mock_test, mock_fetch_key, mock_browser):
        # Equivalent classes for check_and_fetch_cookies:
        # P1 cookies valid -> return True directly
        # P2 cookies invalid + no private token -> browser fallback
        # P3 cookies invalid + has token + no autologin key -> browser fallback
        # P4 cookies invalid + autologin key + post_URL + retest pass -> True

        # P1
        mock_test.return_value = True
        self.assertTrue(self.handler.check_and_fetch_cookies("token", "user1"))

        # P2
        mock_test.return_value = False
        mock_browser.return_value = True
        self.assertTrue(self.handler.check_and_fetch_cookies(None, "user1"))

        # P3
        mock_fetch_key.return_value = None
        mock_browser.return_value = True
        self.assertTrue(self.handler.check_and_fetch_cookies("token", "user1"))

        # P4
        mock_fetch_key.return_value = {"key": "k", "autologinurl": "https://example.com/auto"}
        mock_test.side_effect = [False, True]
        self.request_helper.post_URL.return_value = (MagicMock(url="https://moodle.example.com"), None)
        self.assertTrue(self.handler.check_and_fetch_cookies("token", "user1"))


class TestCookieManagerStrongRobustEC(unittest.TestCase):
    def setUp(self):
        self.config = MagicMock()
        self.domain = "keats.kcl.ac.uk"
        self.cookies_path = "/tmp/cookies.txt"

    def test_cookie_manager_db_vs_non_db_paths(self):
        manager_no_db = CookieManager(self.config, self.domain, self.cookies_path)
        self.assertIsNone(manager_no_db.get_cookies_from_db())
        self.assertIsNone(manager_no_db.save_cookies_to_db([{"name": "a"}]))

        with patch("moodle_dl.auth_session_manager.AuthSessionManager", create=True) as mock_auth_cls:
            mock_auth = MagicMock()
            mock_auth_cls.return_value = mock_auth
            manager_db = CookieManager(
                self.config, self.domain, self.cookies_path, db_file="moodle_state.db"
            )

            # valid class: has active session
            mock_auth.get_valid_session.return_value = {"session_id": "s1"}
            mock_auth.get_session_cookies.return_value = [{"name": "MoodleSession"}]
            self.assertEqual(manager_db.get_cookies_from_db(), [{"name": "MoodleSession"}])

            # invalid class: no active session
            mock_auth.get_valid_session.return_value = None
            self.assertIsNone(manager_db.get_cookies_from_db())

    def test_cookie_manager_refresh_session_robust_paths(self):
        with patch("moodle_dl.auth_session_manager.AuthSessionManager", create=True) as mock_auth_cls:
            mock_auth = MagicMock()
            mock_auth_cls.return_value = mock_auth
            manager = CookieManager(self.config, self.domain, self.cookies_path, db_file="moodle_state.db")

            # valid class: refresh existing session
            mock_auth.get_valid_session.return_value = {"session_id": "old"}
            mock_auth.refresh_session.return_value = "new"
            self.assertEqual(manager.refresh_session_with_new_cookies([{"name": "x"}]), "new")

            # valid class: no old session -> create
            mock_auth.get_valid_session.return_value = None
            mock_auth.create_session.return_value = "created"
            self.assertEqual(manager.refresh_session_with_new_cookies([{"name": "x"}]), "created")

            # robust invalid class: refresh raises exception -> None
            mock_auth.get_valid_session.return_value = {"session_id": "old2"}
            mock_auth.refresh_session.side_effect = RuntimeError("db error")
            self.assertIsNone(manager.refresh_session_with_new_cookies([{"name": "x"}]))

    def test_get_client_ip_fallback_on_socket_error(self):
        with patch("socket.gethostbyname", side_effect=OSError("network down")):
            self.assertEqual(CookieManager._get_client_ip(), "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
