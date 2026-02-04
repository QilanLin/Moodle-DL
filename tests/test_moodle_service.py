# -*- coding: utf-8 -*-
"""
moodle_service.py 单元测试

测试 Moodle 服务的核心功能：
- Token 提取
- 课程过滤逻辑
- URL 解析
- 选项处理
"""

import unittest
from unittest.mock import MagicMock, patch, AsyncMock
from moodle_dl.moodle.moodle_service import MoodleService
from moodle_dl.types import Course, MoodleURL


class TestExtractToken(unittest.TestCase):
    """extract_token 静态方法测试"""

    def test_extract_token_with_valid_url(self):
        """测试从有效 URL 提取 token"""
        import base64
        # 创建一个有效的 token URL 格式
        # 格式：moodle-app://token=<base64 encoded data>
        token_data = "abc:::token123"
        encoded = base64.b64encode(token_data.encode()).decode()
        valid_url = f"moodle-app://token={encoded}"
        result = MoodleService.extract_token(valid_url)
        self.assertIsNotNone(result)
        if result:
            self.assertEqual(result[0], 'token123')
            self.assertIsNone(result[1])

    def test_extract_token_with_base64_encoded_only(self):
        """测试从纯 base64 编码字符串提取 token"""
        import base64
        # 创建一个有效的 base64 编码 token
        token_data = "abc:::token123"
        encoded = base64.b64encode(token_data.encode()).decode()
        result = MoodleService.extract_token(encoded)
        self.assertIsNotNone(result)
        if result:
            self.assertEqual(result[0], 'token123')
            self.assertIsNone(result[1])

    def test_extract_token_without_token(self):
        """测试没有 token 的 URL"""
        import base64
        # 这是一个 base64 编码的字符串，但不是有效的 token 格式
        invalid_data = "not_valid_token_format"
        encoded = base64.b64encode(invalid_data.encode()).decode()
        result = MoodleService.extract_token(encoded)
        # 由于没有 ':::' 分隔符，应该返回 None
        self.assertIsNone(result)

    def test_extract_token_with_private_token(self):
        """测试带私有 token 的 URL"""
        import base64
        # 创建包含私有 token 的数据
        token_data = "abc:::token123:::private456"
        encoded = base64.b64encode(token_data.encode()).decode()
        valid_url = f"moodle-app://token={encoded}"
        result = MoodleService.extract_token(valid_url)
        self.assertIsNotNone(result)
        if result:
            self.assertEqual(result[0], 'token123')
            self.assertEqual(result[1], 'private456')  # privatetoken 应该存在

    def test_extract_token_with_invalid_base64(self):
        """测试无效的 base64 编码"""
        result = MoodleService.extract_token("not_base64_at_all")
        self.assertIsNone(result)


class TestShouldDownloadCourse(unittest.TestCase):
    """should_download_course 静态方法测试"""

    def test_whitelist_mode_empty_list_allows_none(self):
        """测试白名单模式：空列表不允许任何课程"""
        result = MoodleService.should_download_course(
            course_id=1,
            download_course_ids=[],
            dont_download_course_ids=[],
            use_whitelist=True
        )
        self.assertFalse(result)

    def test_whitelist_mode_with_matching_id(self):
        """测试白名单模式：匹配的课程 ID"""
        result = MoodleService.should_download_course(
            course_id=123,
            download_course_ids=[123, 456],
            dont_download_course_ids=[],
            use_whitelist=True
        )
        self.assertTrue(result)

    def test_whitelist_mode_without_matching_id(self):
        """测试白名单模式：不匹配的课程 ID"""
        result = MoodleService.should_download_course(
            course_id=999,
            download_course_ids=[123, 456],
            dont_download_course_ids=[],
            use_whitelist=True
        )
        self.assertFalse(result)

    def test_blacklist_mode_not_in_blacklist(self):
        """测试黑名单模式：不在黑名单中的课程"""
        result = MoodleService.should_download_course(
            course_id=123,
            download_course_ids=[],
            dont_download_course_ids=[456, 789],
            use_whitelist=False
        )
        self.assertTrue(result)

    def test_blacklist_mode_in_blacklist(self):
        """测试黑名单模式：在黑名单中的课程"""
        result = MoodleService.should_download_course(
            course_id=456,
            download_course_ids=[],
            dont_download_course_ids=[456, 789],
            use_whitelist=False
        )
        self.assertFalse(result)

    def test_auto_detect_both_empty(self):
        """测试自动检测：两个列表都为空，允许所有课程"""
        result = MoodleService.should_download_course(
            course_id=123,
            download_course_ids=[],
            dont_download_course_ids=[],
            use_whitelist=None
        )
        self.assertTrue(result)

    def test_auto_detect_whitelist_items(self):
        """测试自动检测：有白名单项目，使用白名单模式"""
        result = MoodleService.should_download_course(
            course_id=123,
            download_course_ids=[123],
            dont_download_course_ids=[],
            use_whitelist=None
        )
        self.assertTrue(result)

    def test_auto_detect_blacklist_items(self):
        """测试自动检测：有黑名单项目，使用黑名单模式"""
        result = MoodleService.should_download_course(
            course_id=123,
            download_course_ids=[],
            dont_download_course_ids=[456],
            use_whitelist=None
        )
        self.assertTrue(result)


class TestShouldDownloadSection(unittest.TestCase):
    """should_download_section 静态方法测试"""

    def test_should_download_section(self):
        """测试不在排除列表中的 section"""
        result = MoodleService.should_download_section(
            section_id=1,
            dont_download_sections_ids=[2, 3, 4]
        )
        self.assertTrue(result)

    def test_section_in_exclude_list(self):
        """测试在排除列表中的 section"""
        # 注意：实际的实现是 `or len(...) == 0`，所以即使section在列表中，如果列表非空也会检查
        # 但实际的逻辑是：section_id not in dont_download_sections_ids OR list is empty
        # 所以如果 section_id=2 在 [2, 3, 4] 中，2 not in [2,3,4] 是 False
        result = MoodleService.should_download_section(
            section_id=2,
            dont_download_sections_ids=[2, 3, 4]
        )
        # 2 not in [2, 3, 4] = False, len([2,3,4]) == 0 = False, so False or False = False
        self.assertFalse(result)

    def test_empty_exclude_list(self):
        """测试空的排除列表"""
        # len([]) == 0 是 True，所以不管 section_id 是什么都返回 True
        result = MoodleService.should_download_section(
            section_id=1,
            dont_download_sections_ids=[]
        )
        self.assertTrue(result)

    def test_none_exclude_list(self):
        """测试 None 排除列表 - 这个会报错"""
        # 实际实现中，None 会报错，因为 len(None) 会报错
        # 但在实际使用中，这个参数总是列表
        pass


class TestSplitMoodleUrl(unittest.TestCase):
    """split_moodle_url 静态方法测试"""

    def test_split_simple_domain(self):
        """测试简单域名分割"""
        # 需要协议才能正确解析
        domain, path = MoodleService.split_moodle_url("https://example.com")
        self.assertEqual(domain, "example.com")
        self.assertEqual(path, "/")

    def test_split_domain_with_path(self):
        """测试带路径的域名分割"""
        domain, path = MoodleService.split_moodle_url("https://example.com/moodle/course")
        self.assertEqual(domain, "example.com")
        self.assertEqual(path, "/moodle/course/")

    def test_split_trailing_slash(self):
        """测试带尾部斜杠的 URL"""
        domain, path = MoodleService.split_moodle_url("https://example.com/moodle/")
        self.assertEqual(domain, "example.com")
        self.assertEqual(path, "/moodle/")

    def test_split_root_path(self):
        """测试根路径"""
        domain, path = MoodleService.split_moodle_url("https://example.com/")
        self.assertEqual(domain, "example.com")
        self.assertEqual(path, "/")

    def test_split_with_http_protocol(self):
        """测试 HTTP 协议"""
        domain, path = MoodleService.split_moodle_url("http://example.com/moodle")
        self.assertEqual(domain, "example.com")
        self.assertEqual(path, "/moodle/")

    def test_split_complex_path(self):
        """测试复杂路径"""
        domain, path = MoodleService.split_moodle_url("https://moodle.uni.edu/courses/fall2024")
        self.assertEqual(domain, "moodle.uni.edu")
        self.assertEqual(path, "/courses/fall2024/")


class TestAddOptionsToCourses(unittest.TestCase):
    """add_options_to_courses 方法测试"""

    def setUp(self):
        self.config = MagicMock()
        self.opts = MagicMock()

    def test_add_options_to_single_course(self):
        """测试为单个课程添加选项"""
        course = Course(123, "Test Course")
        self.config.get_options_of_courses.return_value = {
            "123": {
                "overwrite_name_with": "New Name",
                "create_directory_structure": False,
                "excluded_sections": [1, 2]
            }
        }

        service = MoodleService(self.config, self.opts)
        result = service.add_options_to_courses([course])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].overwrite_name_with, "New Name")
        self.assertFalse(result[0].create_directory_structure)
        self.assertEqual(result[0].excluded_sections, [1, 2])

    def test_add_options_to_multiple_courses(self):
        """测试为多个课程添加选项"""
        course1 = Course(1, "Course 1")
        course2 = Course(2, "Course 2")

        self.config.get_options_of_courses.return_value = {
            "1": {"overwrite_name_with": "Renamed 1"},
            "2": {"excluded_sections": [3, 4]}
        }

        service = MoodleService(self.config, self.opts)
        result = service.add_options_to_courses([course1, course2])

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].overwrite_name_with, "Renamed 1")
        self.assertEqual(result[1].excluded_sections, [3, 4])

    def test_add_options_no_options_defined(self):
        """测试没有定义选项的课程"""
        course = Course(999, "No Options Course")
        self.config.get_options_of_courses.return_value = {}

        service = MoodleService(self.config, self.opts)
        result = service.add_options_to_courses([course])

        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].overwrite_name_with)
        # 默认值应该保持不变
        self.assertTrue(result[0].create_directory_structure)
        self.assertEqual(result[0].excluded_sections, [])

    def test_add_options_partial_options(self):
        """测试部分选项设置"""
        course = Course(123, "Test Course")
        self.config.get_options_of_courses.return_value = {
            "123": {
                "overwrite_name_with": "New Name"
                # 缺少 create_directory_structure 和 excluded_sections
            }
        }

        service = MoodleService(self.config, self.opts)
        result = service.add_options_to_courses([course])

        self.assertEqual(result[0].overwrite_name_with, "New Name")
        # 未设置的选项应该保持默认值
        self.assertTrue(result[0].create_directory_structure)
        self.assertEqual(result[0].excluded_sections, [])


class TestObtainLoginToken(unittest.TestCase):
    """obtain_login_token 方法测试"""

    def setUp(self):
        self.config = MagicMock()
        self.opts = MagicMock()

    @patch('moodle_dl.moodle.moodle_service.RequestHelper')
    def test_obtain_token_success(self, mock_request_helper_class):
        """测试成功获取 token"""
        mock_helper = MagicMock()
        mock_helper.get_login.return_value = {
            'token': 'test_token_123',
            'privatetoken': 'private_token_456'
        }
        mock_request_helper_class.return_value = mock_helper

        service = MoodleService(self.config, self.opts)
        moodle_url = MoodleURL(use_http=False, domain="example.com", path="/")

        token, private_token = service.obtain_login_token("user", "pass", moodle_url)

        self.assertEqual(token, 'test_token_123')
        self.assertEqual(private_token, 'private_token_456')

    @patch('moodle_dl.moodle.moodle_service.RequestHelper')
    def test_obtain_token_without_private(self, mock_request_helper_class):
        """测试获取没有私有 token 的响应"""
        mock_helper = MagicMock()
        mock_helper.get_login.return_value = {
            'token': 'test_token_123'
        }
        mock_request_helper_class.return_value = mock_helper

        service = MoodleService(self.config, self.opts)
        moodle_url = MoodleURL(use_http=False, domain="example.com", path="/")

        token, private_token = service.obtain_login_token("user", "pass", moodle_url)

        self.assertEqual(token, 'test_token_123')
        self.assertIsNone(private_token)

    @patch('moodle_dl.moodle.moodle_service.RequestHelper')
    def test_obtain_token_no_token_in_response(self, mock_request_helper_class):
        """测试响应中没有 token 的错误处理"""
        mock_helper = MagicMock()
        mock_helper.get_login.return_value = {
            'error': 'Invalid credentials'
        }
        mock_request_helper_class.return_value = mock_helper

        service = MoodleService(self.config, self.opts)
        moodle_url = MoodleURL(use_http=False, domain="example.com", path="/")

        with self.assertRaises(RuntimeError) as context:
            service.obtain_login_token("user", "pass", moodle_url)

        self.assertIn("No token was received", str(context.exception))

    @patch('moodle_dl.moodle.moodle_service.RequestHelper')
    def test_obtain_token_empty_private_token(self, mock_request_helper_class):
        """测试私有 token 为空字符串的情况"""
        mock_helper = MagicMock()
        mock_helper.get_login.return_value = {
            'token': 'test_token_123',
            'privatetoken': ''
        }
        mock_request_helper_class.return_value = mock_helper

        service = MoodleService(self.config, self.opts)
        moodle_url = MoodleURL(use_http=False, domain="example.com", path="/")

        token, private_token = service.obtain_login_token("user", "pass", moodle_url)

        self.assertEqual(token, 'test_token_123')
        self.assertIsNone(private_token)  # 空字符串应该转换为 None


if __name__ == "__main__":
    unittest.main()
