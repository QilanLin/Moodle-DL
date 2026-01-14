#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 Cookie 加载功能

验证 CookieManager 能够正确加载 Netscape 格式的 Cookie 文件，
包括使用标准库和手动解析两种方式。
"""

import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from moodle_dl.cookie_manager import CookieManager


class TestCookieLoading(unittest.TestCase):
    """测试 Cookie 文件加载"""

    def setUp(self):
        """设置测试环境"""
        self.config = Mock()
        self.config.get_misc_files_path.return_value = tempfile.gettempdir()
        
        self.manager = CookieManager(
            config=self.config,
            moodle_domain='keats.kcl.ac.uk',
            cookies_path=None,
            database_file=':memory:'
        )

    def test_load_standard_netscape_format(self):
        """测试加载标准 Netscape 格式的 Cookie"""
        # 创建测试 Cookie 文件
        cookie_content = """# Netscape HTTP Cookie File
# This is a generated file. Do not edit.

keats.kcl.ac.uk	FALSE	/	TRUE	-1	MoodleSession	test_session_123
keats.kcl.ac.uk	FALSE	/	FALSE	1735689600	test_cookie	test_value
"""
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(cookie_content)
            temp_file = f.name
        
        try:
            # 测试加载
            cookies = self.manager._load_cookies_from_file(temp_file)
            
            # 验证结果
            self.assertIsNotNone(cookies)
            self.assertEqual(len(cookies), 2)
            
            # 验证第一个 cookie（secure=TRUE）
            cookie1 = cookies[0]
            self.assertEqual(cookie1['name'], 'MoodleSession')
            self.assertEqual(cookie1['value'], 'test_session_123')
            self.assertEqual(cookie1['secure'], 1)  # TRUE -> 1
            self.assertIsNone(cookie1['expires'])  # -1 表示会话 cookie
            
            # 验证第二个 cookie（secure=FALSE）
            cookie2 = cookies[1]
            self.assertEqual(cookie2['name'], 'test_cookie')
            self.assertEqual(cookie2['secure'], 0)  # FALSE -> 0
            self.assertEqual(cookie2['expires'], 1735689600)
            
        finally:
            os.unlink(temp_file)

    def test_load_numeric_format(self):
        """测试加载数字格式的 Cookie（兼容性）"""
        cookie_content = """# Cookie File
keats.kcl.ac.uk	0	/	1	0	NumericCookie	value123
"""
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(cookie_content)
            temp_file = f.name
        
        try:
            cookies = self.manager._load_cookies_from_file(temp_file)
            
            self.assertIsNotNone(cookies)
            self.assertEqual(len(cookies), 1)
            
            cookie = cookies[0]
            self.assertEqual(cookie['name'], 'NumericCookie')
            self.assertEqual(cookie['secure'], 1)  # 数字 1 -> 1
            
        finally:
            os.unlink(temp_file)

    def test_fallback_manual_parse(self):
        """测试手动解析回退机制"""
        # 创建一个可能不符合标准库严格要求的文件
        cookie_content = """keats.kcl.ac.uk	FALSE	/	TRUE	-1	TestCookie	value
"""
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(cookie_content)
            temp_file = f.name
        
        try:
            # 测试手动解析
            cookies = self.manager._fallback_manual_parse(temp_file)
            
            self.assertIsNotNone(cookies)
            self.assertEqual(len(cookies), 1)
            
            cookie = cookies[0]
            self.assertEqual(cookie['name'], 'TestCookie')
            self.assertEqual(cookie['secure'], 1)
            
        finally:
            os.unlink(temp_file)

    def test_invalid_secure_field_handling(self):
        """测试处理无效的 secure 字段值"""
        cookie_content = """# Test invalid values
keats.kcl.ac.uk	FALSE	/	INVALID	-1	BadCookie	value
"""
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(cookie_content)
            temp_file = f.name
        
        try:
            cookies = self.manager._fallback_manual_parse(temp_file)
            
            # 应该优雅降级，使用默认值
            self.assertIsNotNone(cookies)
            self.assertEqual(len(cookies), 1)
            
            cookie = cookies[0]
            self.assertEqual(cookie['secure'], 0)  # 默认为 0
            
        finally:
            os.unlink(temp_file)

    def test_invalid_expires_field_handling(self):
        """测试处理无效的 expires 字段值"""
        cookie_content = """keats.kcl.ac.uk	FALSE	/	TRUE	invalid_date	ExpiresCookie	value
"""
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(cookie_content)
            temp_file = f.name
        
        try:
            cookies = self.manager._fallback_manual_parse(temp_file)
            
            # 应该优雅降级
            self.assertIsNotNone(cookies)
            self.assertEqual(len(cookies), 1)
            
            cookie = cookies[0]
            self.assertIsNone(cookie['expires'])  # 默认为 None
            
        finally:
            os.unlink(temp_file)

    def test_empty_file(self):
        """测试空文件处理"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("")
            temp_file = f.name
        
        try:
            cookies = self.manager._load_cookies_from_file(temp_file)
            self.assertIsNone(cookies)
        finally:
            os.unlink(temp_file)

    def test_nonexistent_file(self):
        """测试不存在的文件"""
        cookies = self.manager._load_cookies_from_file('/nonexistent/path/cookies.txt')
        self.assertIsNone(cookies)

    def test_comments_and_whitespace(self):
        """测试正确处理注释和空白行"""
        cookie_content = """# Netscape HTTP Cookie File
# Comment line

# Another comment
keats.kcl.ac.uk	FALSE	/	TRUE	-1	Cookie1	value1

# More comments
keats.kcl.ac.uk	FALSE	/	FALSE	0	Cookie2	value2

"""
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(cookie_content)
            temp_file = f.name
        
        try:
            cookies = self.manager._load_cookies_from_file(temp_file)
            
            # 应该只加载 2 个 cookie，忽略注释和空行
            self.assertIsNotNone(cookies)
            self.assertEqual(len(cookies), 2)
            
        finally:
            os.unlink(temp_file)


class TestCookieStandardLibraryIntegration(unittest.TestCase):
    """测试与 Python 标准库的集成"""

    def test_mozilla_cookie_jar_compatibility(self):
        """测试与 http.cookiejar.MozillaCookieJar 的兼容性"""
        import http.cookiejar
        
        # 创建标准格式的 Cookie 文件
        cookie_content = """# Netscape HTTP Cookie File
# This is a generated file. Do not edit.

.example.com	TRUE	/	FALSE	0	test	value
"""
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(cookie_content)
            temp_file = f.name
        
        try:
            # 验证标准库可以加载
            jar = http.cookiejar.MozillaCookieJar(temp_file)
            jar.load(ignore_discard=True, ignore_expires=True)
            
            self.assertEqual(len(jar), 1)
            cookie = list(jar)[0]
            self.assertEqual(cookie.name, 'test')
            self.assertEqual(cookie.value, 'value')
            
        finally:
            os.unlink(temp_file)


if __name__ == '__main__':
    # 运行测试
    unittest.main(verbosity=2)
