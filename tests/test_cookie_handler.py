# -*- coding: utf-8 -*-
"""
cookie_handler.py 单元测试

测试 Cookie 处理功能：
- Cookie 验证
- Autologin key 获取
- Cookie 检查和获取
- 浏览器回退方案
"""

import unittest
from unittest.mock import MagicMock, Mock, patch

from moodle_dl.moodle.cookie_handler import CookieHandler
from moodle_dl.moodle.request_helper import RequestHelper
from moodle_dl.config import ConfigHelper
from moodle_dl.types import MoodleDlOpts
from moodle_dl.exceptions import MoodleAPIError


class TestCookieHandlerInit(unittest.TestCase):
    """CookieHandler 初始化测试"""

    def test_init(self):
        """测试初始化"""
        request_helper = Mock()  # Don't use spec to avoid attribute errors
        request_helper.url_base = 'https://moodle.example.com'
        version = 2016120500
        config = Mock(spec=ConfigHelper)
        opts = Mock(spec=MoodleDlOpts)

        handler = CookieHandler(request_helper, version, config, opts)

        self.assertEqual(handler.client, request_helper)
        self.assertEqual(handler.version, version)
        self.assertEqual(handler.config, config)
        self.assertEqual(handler.opts, opts)
        self.assertIsNone(handler.cookies_path)
        self.assertEqual(handler.moodle_test_url, 'https://moodle.example.com')


class TestFetchAutologinKey(unittest.TestCase):
    """fetch_autologin_key 方法测试"""

    def setUp(self):
        self.request_helper = Mock()  # Don't use spec
        self.request_helper.url_base = 'https://moodle.example.com'
        self.version = 2016120500
        self.config = Mock(spec=ConfigHelper)
        self.opts = Mock(spec=MoodleDlOpts)
        self.handler = CookieHandler(self.request_helper, self.version, self.config, self.opts)

    def test_fetch_autologin_key_success(self):
        """测试成功获取 autologin key"""
        self.request_helper.post.return_value = {
            'key': 'test_autologin_key',
            'autologinurl': 'https://example.com/autologin'
        }

        result = self.handler.fetch_autologin_key('private_token_123')

        self.assertIsNotNone(result)
        self.assertEqual(result['key'], 'test_autologin_key')
        self.assertEqual(result['autologinurl'], 'https://example.com/autologin')

    def test_fetch_autologin_key_old_version(self):
        """测试旧版本 Moodle（不支持 autologin key）"""
        self.handler.version = 2015051100  # Moodle 3.1

        result = self.handler.fetch_autologin_key('private_token_123')

        self.assertIsNone(result)
        self.request_helper.post.assert_not_called()

    @patch('moodle_dl.moodle.cookie_handler.logging')
    def test_fetch_autologin_key_request_rejected(self, mock_logging):
        """测试请求被拒绝时返回 None"""
        from moodle_dl.moodle.request_helper import RequestRejectedError
        self.request_helper.post.side_effect = RequestRejectedError('Access denied')

        result = self.handler.fetch_autologin_key('private_token_123')

        self.assertIsNone(result)

    @patch('moodle_dl.moodle.cookie_handler.logging')
    def test_fetch_autologin_key_api_error(self, mock_logging):
        """测试 API 错误时返回 None"""
        self.request_helper.post.side_effect = MoodleAPIError('API error')

        result = self.handler.fetch_autologin_key('private_token_123')

        self.assertIsNone(result)


class TestTestCookies(unittest.TestCase):
    """test_cookies 方法测试"""

    def setUp(self):
        self.request_helper = Mock()  # Don't use spec
        self.request_helper.url_base = 'https://moodle.example.com'
        self.version = 2016120500
        self.config = Mock(spec=ConfigHelper)
        self.opts = Mock(spec=MoodleDlOpts)
        self.handler = CookieHandler(self.request_helper, self.version, self.config, self.opts)
        self.handler.moodle_test_url = 'https://moodle.example.com'

    @patch('moodle_dl.moodle.cookie_handler.logging')
    def test_cookies_valid_logout_link(self, mock_logging):
        """测试 cookies 有效（找到 logout 链接）"""
        mock_response = Mock()
        mock_response.text = '<html><body><a href="/login/logout.php">Logout</a></body></html>'
        mock_response.url = 'https://moodle.example.com/'

        self.request_helper.get_URL.return_value = (mock_response, Mock())

        result = self.handler.test_cookies()

        self.assertTrue(result)

    @patch('moodle_dl.moodle.cookie_handler.logging')
    def test_cookies_invalid_redirected_to_login(self, mock_logging):
        """测试 cookies 无效（重定向到登录页）"""
        mock_response = Mock()
        mock_response.text = 'Please login'
        mock_response.url = 'https://moodle.example.com/login/index.php'

        self.request_helper.get_URL.return_value = (mock_response, Mock())

        result = self.handler.test_cookies()

        self.assertFalse(result)

    @patch('moodle_dl.moodle.cookie_handler.logging')
    def test_cookies_valid_moodle_markers(self, mock_logging):
        """测试 cookies 有效（包含 Moodle 标记）"""
        mock_response = Mock()
        mock_response.text = '<html><body>Welcome to Moodle course dashboard</body></html>'
        mock_response.url = 'https://moodle.example.com/'

        self.request_helper.get_URL.return_value = (mock_response, Mock())

        result = self.handler.test_cookies()

        self.assertTrue(result)

    @patch('moodle_dl.moodle.cookie_handler.logging')
    def test_cookies_invalid_error_markers(self, mock_logging):
        """测试 cookies 无效（包含错误标记）"""
        mock_response = Mock()
        mock_response.text = 'You are not logged in'
        mock_response.url = 'https://moodle.example.com/'

        self.request_helper.get_URL.return_value = (mock_response, Mock())

        result = self.handler.test_cookies()

        self.assertFalse(result)

    @patch('moodle_dl.moodle.cookie_handler.logging')
    def test_cookies_valid_short_response(self, mock_logging):
        """测试短响应（可能无效）"""
        mock_response = Mock()
        mock_response.text = 'Short'
        mock_response.url = 'https://moodle.example.com/'

        self.request_helper.get_URL.return_value = (mock_response, Mock())

        result = self.handler.test_cookies()

        self.assertFalse(result)

    @patch('moodle_dl.moodle.cookie_handler.logging')
    def test_cookies_valid_by_default(self, mock_logging):
        """测试默认认为 cookies 有效（无法判定为过期）"""
        mock_response = Mock()
        # Content long enough, no error markers, no logout link, not redirected
        mock_response.text = 'Some content here that is long enough and has no error markers but also no logout link. It contains course related information.'
        mock_response.url = 'https://moodle.example.com/some/page'

        self.request_helper.get_URL.return_value = (mock_response, None)

        result = self.handler.test_cookies()

        # Should default to True when uncertain (content has 'course' which is a Moodle marker)
        self.assertTrue(result)


class TestCheckAndFetchCookies(unittest.TestCase):
    """check_and_fetch_cookies 方法测试"""

    def setUp(self):
        self.request_helper = Mock()  # Don't use spec
        self.request_helper.url_base = 'https://moodle.example.com'
        self.version = 2016120500
        self.config = Mock(spec=ConfigHelper)
        self.opts = Mock(spec=MoodleDlOpts)
        self.handler = CookieHandler(self.request_helper, self.version, self.config, self.opts)
        self.handler.moodle_test_url = 'https://moodle.example.com'
        self.handler.cookies_path = None  # Using database storage

    @patch('moodle_dl.moodle.cookie_handler.logging')
    @patch.object(CookieHandler, 'test_cookies')
    def test_cookies_valid_no_fetch_needed(self, mock_test_cookies, mock_logging):
        """测试 cookies 已有效，无需重新获取"""
        mock_test_cookies.return_value = True

        result = self.handler.check_and_fetch_cookies('private_token', 'user123')

        self.assertTrue(result)

    @patch('moodle_dl.moodle.cookie_handler.logging')
    @patch.object(CookieHandler, 'test_cookies')
    @patch.object(CookieHandler, '_try_refresh_from_browser')
    def test_no_private_token_fallback_to_browser(self, mock_refresh, mock_test_cookies, mock_logging):
        """测试没有 private token 时回退到浏览器"""
        mock_test_cookies.return_value = False
        mock_refresh.return_value = True

        result = self.handler.check_and_fetch_cookies(None, 'user123')

        self.assertTrue(result)
        mock_refresh.assert_called_once()

    @patch('moodle_dl.moodle.cookie_handler.logging')
    @patch.object(CookieHandler, 'fetch_autologin_key')
    @patch.object(CookieHandler, 'test_cookies')
    @patch.object(CookieHandler, '_try_refresh_from_browser')
    def test_autologin_key_failed_fallback_to_browser(self, mock_refresh, mock_test_cookies, mock_fetch, mock_logging):
        """测试 autologin key 失败时回退到浏览器"""
        mock_test_cookies.return_value = False
        mock_fetch.return_value = None  # Autologin key failed
        mock_refresh.return_value = True

        result = self.handler.check_and_fetch_cookies('private_token', 'user123')

        self.assertTrue(result)
        mock_fetch.assert_called_once()
        mock_refresh.assert_called_once()

    @patch('moodle_dl.moodle.cookie_handler.logging')
    @patch.object(CookieHandler, 'fetch_autologin_key')
    @patch.object(CookieHandler, 'test_cookies')
    def test_successful_cookie_download(self, mock_test_cookies, mock_fetch, mock_logging):
        """测试成功下载 cookies"""
        mock_test_cookies.side_effect = [False, True]  # First check fails, second succeeds
        mock_fetch.return_value = {
            'key': 'autologin_key',
            'autologinurl': 'https://example.com/autologin'
        }

        mock_response = Mock()
        mock_response.url = 'https://moodle.example.com/dashboard'
        # Mock the post_URL method on the instance
        self.request_helper.post_URL.return_value = (mock_response, None)

        result = self.handler.check_and_fetch_cookies('private_token', 'user123')

        self.assertTrue(result)
        self.request_helper.post_URL.assert_called_once()


class TestTryRefreshFromBrowser(unittest.TestCase):
    """_try_refresh_from_browser 方法测试"""

    def setUp(self):
        self.request_helper = Mock()  # Don't use spec
        self.request_helper.url_base = 'https://moodle.example.com'
        self.version = 2016120500
        self.config = Mock(spec=ConfigHelper)
        self.opts = Mock(spec=MoodleDlOpts)
        self.handler = CookieHandler(self.request_helper, self.version, self.config, self.opts)
        self.handler.moodle_test_url = 'https://moodle.example.com'
        self.handler.cookies_path = None
        self.request_helper.moodle_url = Mock()
        self.request_helper.moodle_url.domain = 'moodle.example.com'

    @patch('moodle_dl.moodle.cookie_handler.logging')
    @patch('moodle_dl.utils.Log.success')
    @patch('moodle_dl.moodle.cookie_handler.CookieHandler.test_cookies')
    def test_successful_refresh_from_browser(self, mock_test_cookies, mock_log, mock_logging):
        """测试成功从浏览器刷新 cookies"""
        mock_cookie_manager = Mock()
        mock_cookie_manager.refresh_cookies.return_value = True
        mock_test_cookies.return_value = True

        with patch('moodle_dl.cookie_manager.CookieManager', return_value=mock_cookie_manager):
            result = self.handler._try_refresh_from_browser()

        self.assertTrue(result)
        mock_cookie_manager.refresh_cookies.assert_called_once_with(auto_get_token=False)

    @patch('moodle_dl.moodle.cookie_handler.logging')
    @patch('moodle_dl.utils.Log.warning')
    @patch('moodle_dl.moodle.cookie_handler.CookieHandler.test_cookies')
    def test_browser_refresh_failed_validation(self, mock_test_cookies, mock_log, mock_logging):
        """测试从浏览器导出 cookies 但验证失败"""
        mock_cookie_manager = Mock()
        mock_cookie_manager.refresh_cookies.return_value = True
        mock_test_cookies.return_value = False

        with patch('moodle_dl.cookie_manager.CookieManager', return_value=mock_cookie_manager):
            result = self.handler._try_refresh_from_browser()

        self.assertFalse(result)

    @patch('moodle_dl.moodle.cookie_handler.logging')
    @patch('moodle_dl.utils.Log.warning')
    def test_browser_refresh_failed(self, mock_log, mock_logging):
        """测试从浏览器刷新 cookies 失败"""
        mock_cookie_manager = Mock()
        mock_cookie_manager.refresh_cookies.return_value = False

        with patch('moodle_dl.cookie_manager.CookieManager', return_value=mock_cookie_manager):
            result = self.handler._try_refresh_from_browser()

        self.assertFalse(result)

    @patch('moodle_dl.moodle.cookie_handler.logging')
    def test_browser_refresh_exception(self, mock_logging):
        """测试从浏览器刷新时发生异常"""
        with patch('moodle_dl.cookie_manager.CookieManager', side_effect=Exception('Browser error')):
            result = self.handler._try_refresh_from_browser()

        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
