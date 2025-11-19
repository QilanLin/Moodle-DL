"""
单元测试：下载失败文件追踪系统

测试 database.py 中添加的失败文件追踪功能：
- save_failed_file()
- mark_download_success()
- get_failed_files()
- get_failed_files_summary()
- reset_failed_file_for_retry()
"""

import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.types import File, MoodleDlOpts


class TestFailedFileTracking(unittest.TestCase):
    """测试失败文件追踪功能"""

    def setUp(self):
        """每个测试前的准备工作"""
        # 创建临时目录和数据库
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'moodle_state.db')  # 注意：必须用 moodle_state.db

        # 创建模拟的配置对象
        self.config = MagicMock(spec=ConfigHelper)
        self.config.get_misc_files_path.return_value = self.temp_dir

        # 创建模拟的选项对象
        self.opts = MagicMock(spec=MoodleDlOpts)

        # 初始化数据库（这会自动创建表）
        self.db = StateRecorder(self.config, self.opts)

        # 验证数据库和表已创建
        if not os.path.exists(self.db_path):
            raise RuntimeError(f"数据库文件未创建: {self.db_path}")

        # 创建测试用的文件对象
        self.test_file = File(
            module_id=12345,
            section_name='第1周',
            section_id=1,
            module_name='测试文件.pdf',
            content_filepath='/',
            content_filename='test_file.pdf',
            content_fileurl='https://example.com/test_file.pdf',
            content_filesize=1024000,
            content_timemodified=int(time.time()),
            module_modname='resource',
            content_type='pdf',
            content_isexternalfile=False,
            saved_to='/path/to/test_file.pdf'
        )

        self.course_id = 101
        self.course_fullname = '测试课程'

    def tearDown(self):
        """每个测试后的清理工作"""
        # 删除临时目录及其所有内容
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_save_failed_file_new(self):
        """测试保存新的失败文件记录"""
        error_msg = "连接超时：无法下载文件"

        # 保存失败记录
        self.db.save_failed_file(self.test_file, self.course_id, self.course_fullname, error_msg)

        # 验证数据库中的记录
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT download_status, download_attempts, consecutive_failures,
                   last_failed_reason, saved_to
            FROM files
            WHERE module_id = ? AND content_fileurl = ?
        """, (self.test_file.module_id, self.test_file.content_fileurl))

        result = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(result, "失败文件记录应该被插入到数据库")
        self.assertEqual(result[0], 'failed', "状态应为 'failed'")
        self.assertEqual(result[1], 1, "首次失败，尝试次数应为 1")
        self.assertEqual(result[2], 1, "连续失败次数应为 1")
        self.assertEqual(result[3], error_msg, "失败原因应被记录")
        self.assertEqual(result[4], self.test_file.saved_to, "目标路径应被记录")

    def test_save_failed_file_existing(self):
        """测试更新已存在文件的失败记录"""
        error_msg1 = "第一次失败"
        error_msg2 = "第二次失败"

        # 第一次失败
        self.db.save_failed_file(self.test_file, self.course_id, self.course_fullname, error_msg1)

        # 第二次失败
        self.db.save_failed_file(self.test_file, self.course_id, self.course_fullname, error_msg2)

        # 验证数据库
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT download_attempts, consecutive_failures, last_failed_reason
            FROM files
            WHERE module_id = ? AND content_fileurl = ?
        """, (self.test_file.module_id, self.test_file.content_fileurl))

        result = cursor.fetchone()
        conn.close()

        self.assertEqual(result[0], 2, "第二次失败，尝试次数应为 2")
        self.assertEqual(result[1], 2, "连续失败次数应为 2")
        self.assertEqual(result[2], error_msg2, "应记录最新的失败原因")

    def test_mark_download_success(self):
        """测试标记下载成功并重置失败计数"""
        # 先记录两次失败
        self.db.save_failed_file(self.test_file, self.course_id, self.course_fullname, "失败1")
        self.db.save_failed_file(self.test_file, self.course_id, self.course_fullname, "失败2")

        # 标记成功
        self.db.mark_download_success(self.test_file, self.course_id)

        # 验证数据库
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT download_status, consecutive_failures, last_failed_reason
            FROM files
            WHERE module_id = ? AND content_fileurl = ?
        """, (self.test_file.module_id, self.test_file.content_fileurl))

        result = cursor.fetchone()
        conn.close()

        self.assertEqual(result[0], 'success', "状态应为 'success'")
        self.assertEqual(result[1], 0, "连续失败次数应被重置为 0")
        self.assertIsNone(result[2], "失败原因应被清除")

    def test_get_failed_files(self):
        """测试查询失败文件列表"""
        # 创建多个失败文件
        file1 = self.test_file
        file2 = File(
            module_id=12346,
            section_name='第2周',
            section_id=2,
            module_name='测试文件2.pdf',
            content_filepath='/',
            content_filename='test_file2.pdf',
            content_fileurl='https://example.com/test_file2.pdf',
            content_filesize=2048000,
            content_timemodified=int(time.time()),
            module_modname='resource',
            content_type='pdf',
            content_isexternalfile=False,
            saved_to='/path/to/test_file2.pdf'
        )

        # 记录失败
        self.db.save_failed_file(file1, self.course_id, self.course_fullname, "错误1")
        self.db.save_failed_file(file2, self.course_id, self.course_fullname, "错误2")
        self.db.save_failed_file(file2, self.course_id, self.course_fullname, "错误2-再次")  # file2 失败2次

        # 查询所有失败文件
        failed_files = self.db.get_failed_files()

        self.assertEqual(len(failed_files), 2, "应返回2个失败文件")

        # 验证排序（consecutive_failures DESC）
        self.assertEqual(failed_files[0].module_id, 12346, "file2 应排在第一（失败2次）")
        self.assertEqual(failed_files[1].module_id, 12345, "file1 应排在第二（失败1次）")

        # 验证 saved_to 字段
        self.assertEqual(failed_files[0].saved_to, file2.saved_to, "应记录目标路径")
        self.assertEqual(failed_files[1].saved_to, file1.saved_to, "应记录目标路径")

    def test_get_failed_files_with_course_filter(self):
        """测试按课程过滤失败文件"""
        # 在两个不同课程中创建失败文件
        self.db.save_failed_file(self.test_file, 101, '课程A', "错误")

        file2 = File(
            module_id=99999,
            section_name='第1周',
            section_id=1,
            module_name='其他课程文件.pdf',
            content_filepath='/',
            content_filename='other_file.pdf',
            content_fileurl='https://example.com/other_file.pdf',
            content_filesize=1024,
            content_timemodified=int(time.time()),
            module_modname='resource',
            content_type='pdf',
            content_isexternalfile=False,
            saved_to='/path/to/other_file.pdf'
        )
        self.db.save_failed_file(file2, 202, '课程B', "错误")

        # 只查询课程 101 的失败文件
        failed_files = self.db.get_failed_files(course_id=101)

        self.assertEqual(len(failed_files), 1, "应只返回课程101的失败文件")
        self.assertEqual(failed_files[0].module_id, self.test_file.module_id)

    def test_get_failed_files_summary(self):
        """测试获取失败文件统计摘要"""
        # 在两个课程中创建失败文件
        file1 = self.test_file
        file2 = File(
            module_id=12346,
            section_name='第2周',
            section_id=2,
            module_name='测试文件2.pdf',
            content_filepath='/',
            content_filename='test_file2.pdf',
            content_fileurl='https://example.com/test_file2.pdf',
            content_filesize=2048000,
            content_timemodified=int(time.time()),
            module_modname='resource',
            content_type='pdf',
            content_isexternalfile=False,
            saved_to='/path/to/test_file2.pdf'
        )

        # 课程 101：2个失败文件
        self.db.save_failed_file(file1, 101, '课程A', "错误1")
        self.db.save_failed_file(file2, 101, '课程A', "错误2")

        # 课程 202：1个失败文件（失败3次）
        file3 = File(
            module_id=99999,
            section_name='第1周',
            section_id=1,
            module_name='其他文件.pdf',
            content_filepath='/',
            content_filename='other.pdf',
            content_fileurl='https://example.com/other.pdf',
            content_filesize=1024,
            content_timemodified=int(time.time()),
            module_modname='resource',
            content_type='pdf',
            content_isexternalfile=False,
            saved_to='/path/to/other.pdf'
        )
        self.db.save_failed_file(file3, 202, '课程B', "错误1")
        self.db.save_failed_file(file3, 202, '课程B', "错误2")
        self.db.save_failed_file(file3, 202, '课程B', "错误3")

        # 获取摘要
        summary = self.db.get_failed_files_summary()

        self.assertEqual(len(summary), 2, "应有2个课程的统计")

        # 验证课程 101
        self.assertEqual(summary[101]['failed_count'], 2, "课程101有2个失败文件")
        self.assertEqual(summary[101]['total_failures'], 2, "总失败次数为2")
        self.assertEqual(summary[101]['max_consecutive'], 1, "最大连续失败次数为1")

        # 验证课程 202
        self.assertEqual(summary[202]['failed_count'], 1, "课程202有1个失败文件")
        self.assertEqual(summary[202]['total_failures'], 3, "总失败次数为3")
        self.assertEqual(summary[202]['max_consecutive'], 3, "最大连续失败次数为3")

    def test_reset_failed_file_for_retry(self):
        """测试重置失败文件状态用于重试"""
        # 记录失败
        self.db.save_failed_file(self.test_file, self.course_id, self.course_fullname, "失败")
        self.db.save_failed_file(self.test_file, self.course_id, self.course_fullname, "再次失败")

        # 重置用于重试
        self.db.reset_failed_file_for_retry(self.test_file, self.course_id)

        # 验证数据库
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT download_status, download_attempts, consecutive_failures, last_failed_reason
            FROM files
            WHERE module_id = ? AND content_fileurl = ?
        """, (self.test_file.module_id, self.test_file.content_fileurl))

        result = cursor.fetchone()
        conn.close()

        self.assertEqual(result[0], 'pending', "状态应重置为 'pending'")
        self.assertEqual(result[1], 2, "download_attempts 应保留历史（不重置）")
        self.assertEqual(result[2], 0, "consecutive_failures 应重置为 0")
        self.assertIsNone(result[3], "失败原因应被清除")

    def test_long_error_message_truncation(self):
        """测试超长错误信息会被截断"""
        # 创建超长错误信息（大于500字符）
        long_error = "错误" * 300  # 600字符

        self.db.save_failed_file(self.test_file, self.course_id, self.course_fullname, long_error)

        # 验证数据库
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT last_failed_reason
            FROM files
            WHERE module_id = ? AND content_fileurl = ?
        """, (self.test_file.module_id, self.test_file.content_fileurl))

        result = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(result[0], "失败原因应被记录")
        self.assertLessEqual(len(result[0]), 500, "错误信息应被截断为500字符")


if __name__ == '__main__':
    unittest.main()
