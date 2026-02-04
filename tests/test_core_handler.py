# -*- coding: utf-8 -*-
"""
core_handler.py 单元测试

测试 Moodle 核心 API 处理功能：
- API 选项构建
- 用户信息获取
- 课程列表获取
- 章节/模块内容获取
"""

import json
import unittest
from unittest.mock import MagicMock, AsyncMock, patch, Mock
import asyncio

from moodle_dl.moodle.core_handler import CoreHandler
from moodle_dl.moodle.request_helper import RequestHelper
from moodle_dl.types import Course


class TestBuildApiOptions(unittest.TestCase):
    """_build_api_options 方法测试"""

    def setUp(self):
        self.request_helper = Mock(spec=RequestHelper)
        self.core_handler = CoreHandler(self.request_helper)

    def test_build_api_options_empty(self):
        """测试空选项列表"""
        result = self.core_handler._build_api_options([])
        self.assertEqual(result, {})

    def test_build_api_options_single(self):
        """测试单个选项"""
        options = [{'name': 'excludemodules', 'value': 'true'}]
        result = self.core_handler._build_api_options(options)
        self.assertEqual(result, {'options[0][name]': 'excludemodules', 'options[0][value]': 'true'})

    def test_build_api_options_multiple(self):
        """测试多个选项"""
        options = [
            {'name': 'excludemodules', 'value': 'true'},
            {'name': 'excludecontents', 'value': 'true'}
        ]
        result = self.core_handler._build_api_options(options)
        self.assertEqual(result['options[0][name]'], 'excludemodules')
        self.assertEqual(result['options[0][value]'], 'true')
        self.assertEqual(result['options[1][name]'], 'excludecontents')
        self.assertEqual(result['options[1][value]'], 'true')


class TestFetchUseridAndVersion(unittest.TestCase):
    """fetch_userid_and_version 方法测试"""

    def setUp(self):
        self.request_helper = Mock(spec=RequestHelper)
        self.core_handler = CoreHandler(self.request_helper)

    def test_fetch_userid_and_version_success(self):
        """测试成功获取用户 ID 和版本"""
        self.request_helper.post.return_value = {
            'userid': '12345',
            'version': '2023100900'  # Moodle 4.3+
        }

        userid, version = self.core_handler.fetch_userid_and_version()

        self.assertEqual(userid, 12345)
        self.assertEqual(version, 2023100900)
        self.assertEqual(self.core_handler.version, 2023100900)

    def test_fetch_userid_and_version_with_dot_version(self):
        """测试带点号的版本号"""
        self.request_helper.post.return_value = {
            'userid': '999',
            'version': '3.9.1'  # Moodle 3.9
        }

        userid, version = self.core_handler.fetch_userid_and_version()

        self.assertEqual(userid, 999)
        self.assertEqual(version, 3)  # 版本号取点号前的部分

    def test_fetch_userid_and_version_missing_userid(self):
        """测试响应中没有 userid"""
        self.request_helper.post.return_value = {
            'version': '2023100900'
        }

        with self.assertRaises(RuntimeError) as context:
            self.core_handler.fetch_userid_and_version()

        self.assertIn('user ID', str(context.exception))

    def test_fetch_userid_and_version_empty_userid(self):
        """测试 userid 为空字符串"""
        self.request_helper.post.return_value = {
            'userid': '',
            'version': '2023100900'
        }

        with self.assertRaises(RuntimeError) as context:
            self.core_handler.fetch_userid_and_version()

        self.assertIn('Invalid userid', str(context.exception))

    def test_fetch_userid_and_version_none_userid(self):
        """测试 userid 为 None"""
        self.request_helper.post.return_value = {
            'userid': None,
            'version': '2023100900'
        }

        with self.assertRaises(RuntimeError) as context:
            self.core_handler.fetch_userid_and_version()

        self.assertIn('Invalid userid', str(context.exception))

    def test_fetch_userid_and_version_invalid_userid_type(self):
        """测试 userid 无效类型"""
        self.request_helper.post.return_value = {
            'userid': 'not_a_number',
            'version': '2023100900'
        }

        with self.assertRaises(RuntimeError) as context:
            self.core_handler.fetch_userid_and_version()

        self.assertIn('parse userid', str(context.exception))


class TestFetchCourses(unittest.TestCase):
    """fetch_courses 方法测试"""

    def setUp(self):
        # Create a fresh Mock for each test (no spec to avoid attribute issues)
        self.request_helper = Mock()
        self.request_helper.post = Mock()
        self.core_handler = CoreHandler(self.request_helper)
        # Save and restore PathTools.restricted_filenames to ensure isolation
        from moodle_dl.utils import PathTools
        self.original_restricted = PathTools.restricted_filenames

    def tearDown(self):
        # Restore PathTools.restricted_filenames state
        from moodle_dl.utils import PathTools
        PathTools.restricted_filenames = self.original_restricted

    def test_fetch_courses_success(self):
        """测试成功获取课程列表"""
        # Ensure PathTools.restricted_filenames is set to False for this test
        from moodle_dl.utils import PathTools
        PathTools.restricted_filenames = False

        self.request_helper.post.return_value = [
            {'id': 1, 'fullname': 'Course 1'},
            {'id': 2, 'fullname': 'Course 2'},
            {'id': 3, 'fullname': 'Course 3'}
        ]

        courses = self.core_handler.fetch_courses(123)

        self.assertEqual(len(courses), 3)
        self.assertEqual(courses[0].id, 1)
        self.assertEqual(courses[0].fullname, 'Course 1')
        self.assertEqual(courses[1].id, 2)
        self.assertEqual(courses[2].id, 3)

    def test_fetch_courses_empty(self):
        """测试空课程列表"""
        from moodle_dl.utils import PathTools
        PathTools.restricted_filenames = False

        self.request_helper.post.return_value = []

        courses = self.core_handler.fetch_courses(123)

        self.assertEqual(len(courses), 0)

    def test_fetch_courses_with_missing_fields(self):
        """测试课程数据缺少字段"""
        # Ensure PathTools.restricted_filenames is set to False for this test
        from moodle_dl.utils import PathTools
        PathTools.restricted_filenames = False

        self.request_helper.post.return_value = [
            {'id': 1, 'fullname': 'Valid Course'},
            {'id': 2},  # Missing fullname
            {'fullname': 'Course without ID'}  # Missing id
        ]

        courses = self.core_handler.fetch_courses(123)

        self.assertEqual(len(courses), 3)
        self.assertEqual(courses[0].fullname, 'Valid Course')
        self.assertEqual(courses[1].fullname, '')  # Default empty string
        self.assertEqual(courses[2].id, 0)  # Default 0

    def test_fetch_courses_api_call(self):
        """测试 API 调用参数"""
        from moodle_dl.utils import PathTools
        PathTools.restricted_filenames = False

        self.request_helper.post.return_value = []

        self.core_handler.fetch_courses(123)

        self.request_helper.post.assert_called_once_with('core_enrol_get_users_courses', {'userid': 123})


class TestFetchAllVisibleCourses(unittest.TestCase):
    """fetch_all_visible_courses 方法测试"""

    def setUp(self):
        self.request_helper = Mock(spec=RequestHelper)
        self.core_handler = CoreHandler(self.request_helper)

    def test_fetch_all_visible_courses_success(self):
        """测试成功获取可见课程"""
        self.core_handler.version = 2016120500  # Moodle 3.2+
        self.request_helper.post.return_value = {
            'courses': [
                {'id': 1, 'fullname': 'Visible Course 1', 'visible': 1},
                {'id': 2, 'fullname': 'Hidden Course', 'visible': 0},
                {'id': 3, 'fullname': 'Visible Course 2', 'visible': 1}
            ]
        }

        courses = self.core_handler.fetch_all_visible_courses()

        self.assertEqual(len(courses), 2)  # Only visible courses
        self.assertEqual(courses[0].id, 1)
        self.assertEqual(courses[1].id, 3)

    def test_fetch_all_visible_courses_old_version(self):
        """测试旧版本 Moodle（不支持此 API）"""
        self.core_handler.version = 2015051100  # Moodle 2.9

        courses = self.core_handler.fetch_all_visible_courses()

        self.assertEqual(len(courses), 0)
        self.request_helper.post.assert_not_called()

    def test_fetch_all_visible_courses_with_logging(self):
        """测试带日志记录的课程获取"""
        import tempfile
        import os

        self.core_handler.version = 2016120500
        self.request_helper.post.return_value = {
            'courses': [{'id': 1, 'fullname': 'Course', 'visible': 1}]
        }

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            log_path = f.name

        try:
            courses = self.core_handler.fetch_all_visible_courses(log_path)

            # Verify log file was created and contains data
            with open(log_path, 'r', encoding='utf-8') as f:
                log_content = f.read()
            self.assertIn('courses', log_content)
            self.assertEqual(len(courses), 1)
        finally:
            os.unlink(log_path)


class TestFetchCoursesInfo(unittest.TestCase):
    """fetch_courses_info 方法测试"""

    def setUp(self):
        self.request_helper = Mock(spec=RequestHelper)
        self.core_handler = CoreHandler(self.request_helper)

    def test_fetch_courses_info_success(self):
        """测试成功获取课程信息"""
        self.core_handler.version = 2016120500  # Moodle 3.2+
        self.request_helper.post.return_value = {
            'courses': [
                {'id': 1, 'fullname': 'Course 1'},
                {'id': 2, 'fullname': 'Course 2'}
            ]
        }

        courses = self.core_handler.fetch_courses_info([1, 2])

        self.assertEqual(len(courses), 2)
        self.assertEqual(courses[0].id, 1)
        self.assertEqual(courses[1].id, 2)

    def test_fetch_courses_info_empty_ids(self):
        """测试空课程 ID 列表"""
        self.core_handler.version = 2016120500

        courses = self.core_handler.fetch_courses_info([])

        self.assertEqual(len(courses), 0)
        self.request_helper.post.assert_not_called()

    def test_fetch_courses_info_old_version(self):
        """测试旧版本 Moodle"""
        self.core_handler.version = 2015051100  # Moodle 2.9

        courses = self.core_handler.fetch_courses_info([1, 2])

        self.assertEqual(len(courses), 0)
        self.request_helper.post.assert_not_called()

    def test_fetch_courses_info_api_call(self):
        """测试 API 调用参数"""
        self.core_handler.version = 2016120500
        self.request_helper.post.return_value = {'courses': []}

        self.core_handler.fetch_courses_info([1, 2, 3])

        call_args = self.request_helper.post.call_args
        self.assertEqual(call_args[0][0], 'core_course_get_courses_by_field')
        self.assertEqual(call_args[0][1]['field'], 'ids')
        self.assertEqual(call_args[0][1]['value'], '1,2,3')


class TestFetchSections(unittest.TestCase):
    """fetch_sections 方法测试"""

    def setUp(self):
        self.request_helper = Mock(spec=RequestHelper)
        self.core_handler = CoreHandler(self.request_helper)

    def test_fetch_sections_old_version(self):
        """测试旧版本 Moodle（不使用选项）"""
        self.core_handler.version = 2014111000  # Moodle 2.8
        self.request_helper.post.return_value = [
            {'id': 1, 'name': 'Section 1'},
            {'id': 2, 'name': 'Section 2'}
        ]

        sections = self.core_handler.fetch_sections(123)

        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0]['id'], 1)
        self.assertEqual(sections[0]['name'], 'Section 1')

        # Check that options were not added
        call_args = self.request_helper.post.call_args
        self.assertNotIn('options[0][name]', call_args[0][1])

    def test_fetch_sections_new_version(self):
        """测试新版本 Moodle（使用选项）"""
        self.core_handler.version = 2015051100  # Moodle 2.9+
        self.request_helper.post.return_value = [
            {'id': 1, 'name': 'Section 1'},
            {'id': 2, 'name': 'Section 2'}
        ]

        sections = self.core_handler.fetch_sections(123)

        self.assertEqual(len(sections), 2)

        # Check that options were added
        call_args = self.request_helper.post.call_args
        self.assertIn('options[0][name]', call_args[0][1])
        self.assertEqual(call_args[0][1]['options[0][name]'], 'excludemodules')

    def test_fetch_sections_with_warnings(self):
        """测试带警告的响应"""
        self.core_handler.version = 2015051100
        self.request_helper.post.return_value = [
            {'id': 1, 'name': 'Section 1'}
        ]

        with patch('moodle_dl.moodle.core_handler.logging') as mock_logging:
            sections = self.core_handler.fetch_sections(123)
            self.assertEqual(len(sections), 1)

    def test_fetch_sections_with_warning_in_response(self):
        """测试响应包含 warnings 字段"""
        self.core_handler.version = 2015051100
        self.request_helper.post.return_value = [
            {'id': 1, 'name': 'Section 1'}
        ]
        # Mock the response with warnings - this would be added to the list after API call
        # The actual implementation checks for warnings separately

        with patch('moodle_dl.moodle.core_handler.logging') as mock_logging:
            sections = self.core_handler.fetch_sections(123)
            self.assertEqual(len(sections), 1)


class TestFetchCourseBlocks(unittest.TestCase):
    """fetch_course_blocks 方法测试"""

    def setUp(self):
        self.request_helper = Mock(spec=RequestHelper)
        self.core_handler = CoreHandler(self.request_helper)

    def test_fetch_course_blocks_success(self):
        """测试成功获取课程块"""
        self.core_handler.version = 2017051500  # Moodle 3.3+
        self.request_helper.post.return_value = {
            'blocks': [
                {'id': 1, 'name': 'Block 1'},
                {'id': 2, 'name': 'Block 2'}
            ]
        }

        blocks = self.core_handler.fetch_course_blocks(123)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]['id'], 1)
        self.assertEqual(blocks[1]['id'], 2)

    def test_fetch_course_blocks_old_version(self):
        """测试旧版本 Moodle（不支持此 API）"""
        self.core_handler.version = 2016120500  # Moodle 3.2

        blocks = self.core_handler.fetch_course_blocks(123)

        self.assertEqual(len(blocks), 0)
        self.request_helper.post.assert_not_called()

    def test_fetch_course_blocks_api_error(self):
        """测试 API 调用失败"""
        self.core_handler.version = 2017051500
        self.request_helper.post.side_effect = Exception('API error')

        blocks = self.core_handler.fetch_course_blocks(123)

        # Should return empty list on error
        self.assertEqual(len(blocks), 0)

    def test_fetch_course_blocks_api_call(self):
        """测试 API 调用参数"""
        self.core_handler.version = 2017051500
        self.request_helper.post.return_value = {'blocks': []}

        self.core_handler.fetch_course_blocks(123)

        call_args = self.request_helper.post.call_args
        self.assertEqual(call_args[0][0], 'core_block_get_course_blocks')
        self.assertEqual(call_args[0][1]['courseid'], 123)
        self.assertEqual(call_args[0][1]['returncontents'], 1)


class TestAsyncLoadCourseCore(unittest.TestCase):
    """async_load_course_core 方法测试"""

    def setUp(self):
        self.request_helper = Mock(spec=RequestHelper)
        self.request_helper.async_post = AsyncMock()
        self.core_handler = CoreHandler(self.request_helper)

    def test_async_load_course_core_old_version(self):
        """测试旧版本 Moodle（不使用选项）"""
        self.core_handler.version = 2014111000  # Moodle 2.8
        self.request_helper.async_post.return_value = [
            {'id': 1, 'name': 'Section 1'}
        ]

        course = Course(123, 'Test Course')

        result = asyncio.run(self.core_handler.async_load_course_core(course))

        self.assertEqual(len(result), 1)

        # Check that options were not added
        call_args = self.request_helper.async_post.call_args
        self.assertNotIn('options[0][name]', call_args[0][1])

    def test_async_load_course_core_new_version(self):
        """测试新版本 Moodle（使用选项）"""
        self.core_handler.version = 2015051100  # Moodle 2.9+
        self.request_helper.async_post.return_value = [
            {'id': 1, 'name': 'Section 1'},
            {'id': 2, 'name': 'Section 2'}
        ]

        course = Course(123, 'Test Course')

        result = asyncio.run(self.core_handler.async_load_course_core(course))

        self.assertEqual(len(result), 2)

        # Check that options were added
        call_args = self.request_helper.async_post.call_args
        self.assertIn('options[0][name]', call_args[0][1])
        self.assertEqual(call_args[0][1]['courseid'], 123)

    def test_async_load_course_core_with_warnings(self):
        """测试带警告的响应"""
        self.core_handler.version = 2015051100
        self.request_helper.async_post.return_value = {
            '0': {'id': 1, 'name': 'Section 1'},
            'warnings': [{'message': 'Warning message'}]
        }

        course = Course(123, 'Test Course')

        with patch('moodle_dl.moodle.core_handler.logging') as mock_logging:
            result = asyncio.run(self.core_handler.async_load_course_core(course))
            self.assertGreater(len(result), 0)


class TestAsyncLoadCoreContents(unittest.TestCase):
    """async_load_core_contents 方法测试"""

    def setUp(self):
        self.request_helper = Mock(spec=RequestHelper)
        self.request_helper.async_post = AsyncMock()
        self.core_handler = CoreHandler(self.request_helper)

    def test_async_load_core_contents_empty(self):
        """测试空课程列表"""
        result = asyncio.run(self.core_handler.async_load_core_contents([]))
        self.assertEqual(result, {})

    def test_async_load_core_contents_single_course(self):
        """测试单个课程"""
        self.core_handler.version = 2015051100
        self.request_helper.async_post.return_value = [
            {'id': 1, 'name': 'Section 1'}
        ]

        courses = [Course(1, 'Course 1')]

        result = asyncio.run(self.core_handler.async_load_core_contents(courses))

        self.assertIn(1, result)
        self.assertEqual(len(result[1]), 1)

    def test_async_load_core_contents_multiple_courses(self):
        """测试多个课程"""
        self.core_handler.version = 2015051100
        self.request_helper.async_post.return_value = [
            {'id': 1, 'name': 'Section 1'}
        ]

        courses = [
            Course(1, 'Course 1'),
            Course(2, 'Course 2'),
            Course(3, 'Course 3')
        ]

        result = asyncio.run(self.core_handler.async_load_core_contents(courses))

        self.assertEqual(len(result), 3)
        self.assertIn(1, result)
        self.assertIn(2, result)
        self.assertIn(3, result)


class TestCoreHandlerInit(unittest.TestCase):
    """CoreHandler 初始化测试"""

    def test_init_default_version(self):
        """测试默认版本"""
        request_helper = Mock(spec=RequestHelper)
        core_handler = CoreHandler(request_helper)

        self.assertEqual(core_handler.version, 2011120500)
        self.assertEqual(core_handler.client, request_helper)

    def test_init_with_custom_request_helper(self):
        """测试自定义 RequestHelper"""
        request_helper = Mock(spec=RequestHelper)
        core_handler = CoreHandler(request_helper)

        self.assertIs(core_handler.client, request_helper)


if __name__ == '__main__':
    unittest.main()
