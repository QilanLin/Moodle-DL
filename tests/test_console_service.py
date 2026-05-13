# -*- coding: utf-8 -*-
"""
console_service.py 单元测试

测试控制台通知服务对 None 值的处理：
- notify_about_failed_downloads 方法处理 content_fileurl 为 None 的情况
"""

import unittest
from unittest.mock import MagicMock, patch

from moodle_dl.notifications.console.console_service import ConsoleService
from moodle_dl.types import Course, File


class TestConsoleServiceNoneHandling(unittest.TestCase):
    """测试 ConsoleService 对 None 值的处理"""

    def setUp(self):
        """设置测试环境"""
        self.config = MagicMock()

    def test_notify_about_failed_downloads_with_none_url(self):
        """测试失败下载的 URL 为 None 时的处理"""
        service = ConsoleService(self.config)

        # 创建模拟的 Task 对象
        mock_task1 = MagicMock()
        mock_task1.filename = "*01* structure"
        mock_task1.file = MagicMock()
        mock_task1.file.saved_to = "/path/to/file.pdf"
        mock_task1.file.content_fileurl = None  # URL 为 None
        mock_task1.status = MagicMock()
        mock_task1.status.get_error_text.return_value = "Connection error"

        mock_task2 = MagicMock()
        mock_task2.filename = "*02* content"
        mock_task2.file = MagicMock()
        mock_task2.file.saved_to = "/path/to/file2.pdf"
        mock_task2.file.content_fileurl = "https://example.com/file.pdf"  # 正常 URL
        mock_task2.status = MagicMock()
        mock_task2.status.get_error_text.return_value = "Download failed"

        # 应该不会抛出 TypeError
        try:
            service.notify_about_failed_downloads([mock_task1, mock_task2])
        except TypeError as e:
            self.fail(f"notify_about_failed_downloads raised TypeError: {e}")

    def test_notify_about_failed_downloads_with_long_url(self):
        """测试失败下载的 URL 很长时的截断处理"""
        service = ConsoleService(self.config)

        # 创建一个很长的 URL（超过 120 字符）
        long_url = "https://example.com/" + "a" * 200 + "/file.pdf"

        mock_task = MagicMock()
        mock_task.filename = "test.pdf"
        mock_task.file = MagicMock()
        mock_task.file.saved_to = "/path/to/file.pdf"
        mock_task.file.content_fileurl = long_url
        mock_task.status = MagicMock()
        mock_task.status.get_error_text.return_value = "Error"

        # 应该不会抛出异常
        try:
            service.notify_about_failed_downloads([mock_task])
        except Exception as e:
            self.fail(f"notify_about_failed_downloads raised exception: {e}")

    def test_notify_about_failed_downloads_with_empty_list(self):
        """测试空失败列表"""
        service = ConsoleService(self.config)

        # 空列表应该不会抛出异常
        try:
            service.notify_about_failed_downloads([])
        except Exception as e:
            self.fail(f"notify_about_failed_downloads with empty list raised exception: {e}")

    def test_notify_about_changes_prints_moved_file_without_replacement(self):
        service = ConsoleService(self.config)
        moved_file = File(
            module_id=1,
            section_name="Week 1",
            section_id=1,
            module_name="Resource",
            content_filepath="/",
            content_filename="old.pdf",
            content_fileurl="https://example.com/old.pdf",
            content_filesize=1024,
            content_timemodified=1234567890,
            module_modname="resource",
            content_type="application/pdf",
            content_isexternalfile=False,
            saved_to="Course/old.pdf",
            moved=1,
        )

        with patch('moodle_dl.notifications.console.console_service.print') as print_mock:
            with patch('moodle_dl.notifications.console.console_service.Log') as log:
                log.cyan_str.return_value = '<->\tCourse/old.pdf'
                service.notify_about_changes_in_moodle([Course(1, 'Course', [moved_file])])

        log.cyan_str.assert_called_once_with('<->\tCourse/old.pdf')
        print_mock.assert_any_call('<->\tCourse/old.pdf')


if __name__ == '__main__':
    unittest.main()
