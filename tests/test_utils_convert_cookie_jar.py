# -*- coding: utf-8 -*-
"""
convert_to_aiohttp_cookie_jar 单元测试

测试 Mozilla Cookie Jar 到 aiohttp Cookie Jar 的转换功能：
- 跳过保留的 cookie 名称（避免 CookieError）
- 跳过包含非法字符的 cookie 名称（如方括号）
- 确保其他 cookies 正常处理
- 大小写不敏感的保留名称匹配
"""

import http.cookies
import http.cookiejar
import unittest
from unittest.mock import Mock, MagicMock, patch
import logging

from moodle_dl.utils import convert_to_aiohttp_cookie_jar


class NestedDict:
    """Helper class to simulate the nested dict structure of CookieJar._cookies"""
    def __init__(self):
        self._data = {}

    def __getitem__(self, key):
        if key not in self._data:
            self._data[key] = PathDict()
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def values(self):
        return self._data.values()

    def keys(self):
        return self._data.keys()

    def __len__(self):
        return len(self._data)

    def total_cookies(self):
        """Count total cookies in all nested dictionaries"""
        return sum(len(path_dict) for path_dict in self._data.values())


class PathDict:
    """Helper class for the second level of nesting"""
    def __init__(self):
        self._data = {}

    def __getitem__(self, key):
        if key not in self._data:
            self._data[key] = {}
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def values(self):
        return self._data.values()

    def __len__(self):
        return len(self._data)


class TestConvertToAiohttpCookieJar(unittest.TestCase):
    """convert_to_aiohttp_cookie_jar 函数测试"""

    def setUp(self):
        """创建 Mozilla Cookie Jar 用于测试"""
        self.mozilla_jar = http.cookiejar.MozillaCookieJar()

    def _add_cookie(self, name, value, domain, path='/'):
        """辅助方法：添加 cookie 到 Mozilla Cookie Jar"""
        cookie = http.cookiejar.Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=domain.startswith('.'),
            path=path,
            path_specified=True,
            secure=False,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False
        )
        self.mozilla_jar.set_cookie(cookie)

    @patch('moodle_dl.utils.CookieJar')
    def test_normal_cookies_are_converted(self, mock_cookie_jar_class):
        """测试正常 cookies 被正确转换"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('session_id', 'abc123', '.example.com')
        self._add_cookie('user_pref', 'dark_mode', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        # 验证两个 cookies 都被转换
        self.assertEqual(nested_dict.total_cookies(), 2)

    @patch('moodle_dl.utils.CookieJar')
    def test_reserved_cookie_name_path_is_skipped(self, mock_cookie_jar_class):
        """测试保留名称 'path' 的 cookie 被跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('Path', '/some/value', '.example.com')
        self._add_cookie('normal_cookie', 'value123', '.example.com')

        # 应该不会抛出 CookieError
        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        # 只有 normal_cookie 应该被转换，Path 应该被跳过
        self.assertEqual(nested_dict.total_cookies(), 1)

    @patch('moodle_dl.utils.CookieJar')
    def test_reserved_cookie_name_version_is_skipped(self, mock_cookie_jar_class):
        """测试保留名称 'version' 的 cookie 被跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('Version', '1.0', '.example.com')
        self._add_cookie('session', 'xyz789', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        self.assertEqual(nested_dict.total_cookies(), 1)

    @patch('moodle_dl.utils.CookieJar')
    def test_reserved_cookie_name_domain_is_skipped(self, mock_cookie_jar_class):
        """测试保留名称 'domain' 的 cookie 被跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('Domain', 'example.com', '.example.com')
        self._add_cookie('auth_token', 'token123', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        self.assertEqual(nested_dict.total_cookies(), 1)

    @patch('moodle_dl.utils.CookieJar')
    def test_reserved_cookie_name_secure_is_skipped(self, mock_cookie_jar_class):
        """测试保留名称 'secure' 的 cookie 被跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('Secure', 'true', '.example.com')
        self._add_cookie('tracking_id', 'track456', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        self.assertEqual(nested_dict.total_cookies(), 1)

    @patch('moodle_dl.utils.CookieJar')
    def test_reserved_cookie_name_expires_is_skipped(self, mock_cookie_jar_class):
        """测试保留名称 'expires' 的 cookie 被跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('Expires', '1234567890', '.example.com')
        self._add_cookie('language', 'en', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        self.assertEqual(nested_dict.total_cookies(), 1)

    @patch('moodle_dl.utils.CookieJar')
    def test_reserved_cookie_name_comment_is_skipped(self, mock_cookie_jar_class):
        """测试保留名称 'comment' 的 cookie 被跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('Comment', 'some comment', '.example.com')
        self._add_cookie('theme', 'light', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        self.assertEqual(nested_dict.total_cookies(), 1)

    @patch('moodle_dl.utils.CookieJar')
    def test_reserved_cookie_name_port_is_skipped(self, mock_cookie_jar_class):
        """测试保留名称 'port' 的 cookie 被跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('Port', '8080', '.example.com')
        self._add_cookie('currency', 'USD', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        self.assertEqual(nested_dict.total_cookies(), 1)

    @patch('moodle_dl.utils.CookieJar')
    def test_reserved_cookie_name_max_age_is_skipped(self, mock_cookie_jar_class):
        """测试保留名称 'max-age' 的 cookie 被跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('Max-Age', '3600', '.example.com')
        self._add_cookie('timezone', 'UTC', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        self.assertEqual(nested_dict.total_cookies(), 1)

    @patch('moodle_dl.utils.CookieJar')
    def test_case_insensitive_reserved_name_matching(self, mock_cookie_jar_class):
        """测试保留名称匹配是大小写不敏感的"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        # 测试各种大小写组合
        self._add_cookie('PATH', '/value', '.example.com')
        self._add_cookie('Version', '2.0', '.example.com')
        self._add_cookie('DOMAIN', 'test.com', '.example.com')
        self._add_cookie('SECURE', 'yes', '.example.com')
        self._add_cookie('normal', 'keep_this', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        # 只有 normal cookie 应该被保留
        self.assertEqual(nested_dict.total_cookies(), 1)

    @patch('moodle_dl.utils.CookieJar')
    def test_all_reserved_names_together(self, mock_cookie_jar_class):
        """测试所有保留名称的 cookies 都被正确跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        # 添加所有保留名称的 cookies
        reserved_names = ['path', 'version', 'port', 'domain', 'secure', 'expires', 'comment', 'max-age']
        for name in reserved_names:
            self._add_cookie(name, f'value_{name}', '.example.com')

        # 添加一些正常 cookies
        self._add_cookie('cookie1', 'value1', '.example.com')
        self._add_cookie('cookie2', 'value2', '.example.com')
        self._add_cookie('cookie3', 'value3', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        # 应该只有 3 个正常 cookies 被转换
        self.assertEqual(nested_dict.total_cookies(), 3)

    @patch('moodle_dl.utils.CookieJar')
    def test_mixed_reserved_and_normal_cookies(self, mock_cookie_jar_class):
        """测试混合的保留和正常 cookies"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        # 混合添加
        self._add_cookie('normal1', 'val1', '.example.com')
        self._add_cookie('Path', '/test', '.example.com')  # 保留
        self._add_cookie('normal2', 'val2', '.example.com')
        self._add_cookie('Version', '1', '.example.com')  # 保留
        self._add_cookie('normal3', 'val3', '.example.com')
        self._add_cookie('secure', 'true', '.example.com')  # 保留（小写）
        self._add_cookie('normal4', 'val4', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        # 应该只有 4 个正常 cookies
        self.assertEqual(nested_dict.total_cookies(), 4)

    @patch('moodle_dl.utils.CookieJar')
    def test_empty_jar_returns_empty_jar(self, mock_cookie_jar_class):
        """测试空的 Mozilla Cookie Jar 返回空的 aiohttp Cookie Jar"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        self.assertEqual(nested_dict.total_cookies(), 0)

    @patch('moodle_dl.utils.CookieJar')
    def test_cookies_with_different_domains(self, mock_cookie_jar_class):
        """测试不同域名的 cookies"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('cookie1', 'val1', '.example.com')
        self._add_cookie('Path', '/test', '.example.com')  # 应被跳过
        self._add_cookie('cookie2', 'val2', '.another.com')
        self._add_cookie('Version', '2', '.another.com')  # 应被跳过

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        self.assertEqual(nested_dict.total_cookies(), 2)

    @patch('moodle_dl.utils.CookieJar')
    def test_cookies_with_different_paths(self, mock_cookie_jar_class):
        """测试不同路径的 cookies"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('cookie1', 'val1', '.example.com', '/api')
        self._add_cookie('Path', '/test', '.example.com', '/api')  # 应被跳过
        self._add_cookie('cookie2', 'val2', '.example.com', '/admin')
        self._add_cookie('Version', '2', '.example.com', '/admin')  # 应被跳过

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        self.assertEqual(nested_dict.total_cookies(), 2)

    @patch('moodle_dl.utils.CookieJar')
    def test_morsel_set_called_with_correct_params(self, mock_cookie_jar_class):
        """测试 morsel.set 被正确调用"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('test_cookie', 'test_value', '.example.com', '/test')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        # 验证 CookieJar 被创建时使用了 unsafe=True
        mock_cookie_jar_class.assert_called_once_with(unsafe=True)

        # 验证 cookie 被添加到 jar 中
        self.assertEqual(nested_dict.total_cookies(), 1)

        # 获取添加的 cookie 的键
        cookie_key = list(nested_dict.keys())[0]
        # 验证域名和路径正确
        domain, path = cookie_key
        self.assertEqual(domain, '.example.com')
        self.assertEqual(path, '/test')

    # ==================== 新增：非法字符 cookie 名称测试 ====================

    @patch('moodle_dl.utils.CookieJar')
    def test_illegal_cookie_name_with_square_brackets_is_skipped(self, mock_cookie_jar_class):
        """测试包含方括号的 cookie 名称被跳过（不会导致 CookieError）"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        # 模拟 barometric[cuid] 这个实际出现的 cookie
        self._add_cookie('barometric[cuid]', 'some_value', '.example.com')
        self._add_cookie('normal_cookie', 'value123', '.example.com')

        # 应该不会抛出 CookieError
        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        # 只有 normal_cookie 应该被转换，barometric[cuid] 应该被跳过
        self.assertEqual(nested_dict.total_cookies(), 1)

    @patch('moodle_dl.utils.CookieJar')
    def test_illegal_cookie_name_with_curly_braces_is_skipped(self, mock_cookie_jar_class):
        """测试包含花括号的 cookie 名称被跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('cookie{test}', 'value', '.example.com')
        self._add_cookie('normal_cookie', 'value123', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        # 只有 normal_cookie 应该被转换
        self.assertEqual(nested_dict.total_cookies(), 1)

    @patch('moodle_dl.utils.CookieJar')
    def test_illegal_cookie_name_with_parentheses_is_skipped(self, mock_cookie_jar_class):
        """测试包含圆括号的 cookie 名称被跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('cookie(test)', 'value', '.example.com')
        self._add_cookie('normal_cookie', 'value123', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        # 只有 normal_cookie 应该被转换
        self.assertEqual(nested_dict.total_cookies(), 1)

    @patch('moodle_dl.utils.CookieJar')
    def test_illegal_cookie_name_with_equals_is_skipped(self, mock_cookie_jar_class):
        """测试包含等号的 cookie 名称被跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        self._add_cookie('cookie=test', 'value', '.example.com')
        self._add_cookie('normal_cookie', 'value123', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        # 只有 normal_cookie 应该被转换
        self.assertEqual(nested_dict.total_cookies(), 1)

    @patch('moodle_dl.utils.CookieJar')
    def test_multiple_illegal_cookie_names_all_skipped(self, mock_cookie_jar_class):
        """测试多个非法 cookie 名称都被跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        # 添加多个非法 cookies
        self._add_cookie('cookie[1]', 'value1', '.example.com')
        self._add_cookie('cookie{2}', 'value2', '.example.com')
        self._add_cookie('cookie(3)', 'value3', '.example.com')
        # 添加一些正常 cookies
        self._add_cookie('good_cookie1', 'val1', '.example.com')
        self._add_cookie('good_cookie2', 'val2', '.example.com')

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        # 应该只有 2 个正常 cookies 被转换
        self.assertEqual(nested_dict.total_cookies(), 2)

    @patch('moodle_dl.utils.CookieJar')
    def test_reserved_and_illegal_cookie_names_both_skipped(self, mock_cookie_jar_class):
        """测试保留名称和非法字符的 cookie 都被跳过"""
        mock_jar = MagicMock()
        nested_dict = NestedDict()
        mock_jar._cookies = nested_dict
        mock_cookie_jar_class.return_value = mock_jar

        # 添加各种类型的 cookies
        self._add_cookie('Path', '/value', '.example.com')  # 保留名称
        self._add_cookie('illegal[name]', 'value', '.example.com')  # 非法字符
        self._add_cookie('Version', '1.0', '.example.com')  # 保留名称
        self._add_cookie('normal_cookie', 'value123', '.example.com')  # 正常

        result = convert_to_aiohttp_cookie_jar(self.mozilla_jar)

        # 应该只有 1 个正常 cookie 被转换
        self.assertEqual(nested_dict.total_cookies(), 1)


if __name__ == '__main__':
    unittest.main()
