# -*- coding: utf-8 -*-
"""
types.py 单元测试 - None 值处理

测试：
- File.content_fileurl 为 None 时会规范化为空字符串
- TaskStatus.set_error 行为正常
"""

import unittest

from moodle_dl.types import File, TaskStatus


class TestFileStrWithNoneHandling(unittest.TestCase):
    """测试 File 和 TaskStatus 的相关行为"""

    def test_file_str_with_none_content_fileurl(self):
        """测试 content_fileurl 为 None 时会规范化为空字符串且 __str__ 正常"""
        file = File(
            module_id=1,
            section_name="Week 1",
            section_id=1,
            module_name="Test Resource",
            content_filepath="/",
            content_filename="test.pdf",
            content_fileurl=None,  # URL 为 None
            content_filesize=1024,
            content_timemodified=1234567890,
            module_modname="resource",
            content_type="application/pdf",
            content_isexternalfile=False,
            saved_to="/tmp/test.pdf",
            time_stamp=1234567890,
            modified=0,
            moved=0,
            deleted=0,
            notified=0
        )

        # 应该不会抛出 TypeError: object of type 'NoneType' has no len()
        try:
            file_str = str(file)
            # 验证输出包含必要的信息
            self.assertIn("content_filename", file_str)
            self.assertIn("test.pdf", file_str)
            self.assertIn("content_fileurl", file_str)
            self.assertEqual(file.content_fileurl, "")
        except TypeError as e:
            if "object of type 'NoneType' has no len()" in str(e):
                self.fail(f"File.__str__ raised TypeError for None content_fileurl: {e}")
            else:
                raise

    def test_task_status_set_error(self):
        """测试 TaskStatus.set_error 会记录错误"""
        status = TaskStatus()
        status.set_error("No URL available for download")
        self.assertEqual(status.error, "No URL available for download")
        self.assertEqual(status.get_error_text(), "No URL available for download")

    def test_file_str_with_long_content_fileurl(self):
        """测试 content_fileurl 很长时的截断处理"""
        # 创建一个超过 256 字符的 URL
        long_url = "https://example.com/" + "a" * 300 + "/file.pdf"

        file = File(
            module_id=1,
            section_name="Week 1",
            section_id=1,
            module_name="Test Resource",
            content_filepath="/",
            content_filename="test.pdf",
            content_fileurl=long_url,
            content_filesize=1024,
            content_timemodified=1234567890,
            module_modname="resource",
            content_type="application/pdf",
            content_isexternalfile=False,
            saved_to="/tmp/test.pdf",
            time_stamp=1234567890,
            modified=0,
            moved=0,
            deleted=0,
            notified=0
        )

        # 应该不会抛出异常
        try:
            file_str = str(file)
            # 验证输出中 URL 被截断了
            self.assertIn("content_fileurl (longer than 256 chars)", file_str)
            self.assertIn("[...]", file_str)
            self.assertNotIn(long_url, file_str)  # 完整 URL 不应该在输出中
        except Exception as e:
            self.fail(f"File.__str__ raised exception for long content_fileurl: {e}")

    def test_file_str_with_short_content_fileurl(self):
        """测试 content_fileurl 较短时正常显示"""
        short_url = "https://example.com/file.pdf"

        file = File(
            module_id=1,
            section_name="Week 1",
            section_id=1,
            module_name="Test Resource",
            content_filepath="/",
            content_filename="test.pdf",
            content_fileurl=short_url,
            content_filesize=1024,
            content_timemodified=1234567890,
            module_modname="resource",
            content_type="application/pdf",
            content_isexternalfile=False,
            saved_to="/tmp/test.pdf",
            time_stamp=1234567890,
            modified=0,
            moved=0,
            deleted=0,
            notified=0
        )

        # 应该正常显示完整 URL
        file_str = str(file)
        self.assertIn(short_url, file_str)
        self.assertNotIn("longer than 256 chars", file_str)


if __name__ == '__main__':
    unittest.main()
