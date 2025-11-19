"""
集成测试：下载失败重试流程的文件名一致性

测试完整的失败重试流程：
- 文件首次下载失败，保存到数据库（包含 position_in_section）
- 从数据库读取失败文件
- 重试时生成的文件名应与首次尝试一致
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
from moodle_dl.downloader.task import Task
from moodle_dl.types import Course, DownloadOptions, File, MoodleDlOpts


class TestRetryIntegration(unittest.TestCase):
    """测试失败重试流程的集成"""

    def setUp(self):
        """测试前的准备工作"""
        # 创建临时目录和数据库
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'moodle_state.db')

        # 创建模拟的配置对象
        self.config = MagicMock(spec=ConfigHelper)
        self.config.get_misc_files_path.return_value = self.temp_dir

        # 创建模拟的选项对象
        self.opts = MagicMock(spec=MoodleDlOpts)

        # 初始化数据库
        self.db = StateRecorder(self.config, self.opts)

        self.course_id = 101
        self.course_fullname = '测试课程'

    def tearDown(self):
        """测试后的清理工作"""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_failed_file_retains_position_on_retry(self):
        """测试失败文件在重试时保留位置信息"""
        # 创建带位置信息的文件
        file = File(
            module_id=12345,
            section_name='第1周',
            section_id=1,
            module_name='讲义',
            content_filepath='/',
            content_filename='lecture.pdf',
            content_fileurl='https://example.com/lecture.pdf',
            content_filesize=1024000,
            content_timemodified=int(time.time()),
            module_modname='resource',
            content_type='pdf',
            content_isexternalfile=False,
            saved_to='/path/to/01-lecture.pdf',
            position_in_section=0  # 位置：0（文件名应为 01-lecture.pdf）
        )

        # 模拟下载失败，保存到数据库
        error_message = '网络超时'
        self.db.save_failed_file(file, self.course_id, self.course_fullname, error_message)

        # 从数据库读取失败文件
        failed_files = self.db.get_failed_files(course_id=self.course_id)

        self.assertEqual(len(failed_files), 1, '应该有1个失败文件')
        retrieved_file = failed_files[0]

        # 验证位置信息被正确保留
        self.assertEqual(retrieved_file.position_in_section, 0, '位置信息应为 0')
        self.assertEqual(retrieved_file.content_filename, 'lecture.pdf')

        # 验证重试时生成的文件名一致
        generated_filename = Task._generate_filename_with_index(retrieved_file)
        self.assertEqual(generated_filename, '01 lecture.pdf', '重试时文件名应与首次一致')

    def test_multiple_failures_preserve_position(self):
        """测试多次失败后位置信息仍然保留"""
        file = File(
            module_id=12346,
            section_name='第2周',
            section_id=2,
            module_name='作业',
            content_filepath='/',
            content_filename='assignment.pdf',
            content_fileurl='https://example.com/assignment.pdf',
            content_filesize=2048000,
            content_timemodified=int(time.time()),
            module_modname='resource',
            content_type='pdf',
            content_isexternalfile=False,
            saved_to='/path/to/05-assignment.pdf',
            position_in_section=4  # 位置：4（文件名应为 05-assignment.pdf）
        )

        # 模拟失败 3 次
        for i in range(3):
            error_message = f'失败 {i+1}'
            self.db.save_failed_file(file, self.course_id, self.course_fullname, error_message)

        # 验证数据库中的失败次数
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT consecutive_failures, position_in_section
            FROM files
            WHERE module_id = ? AND content_fileurl = ?
        """, (file.module_id, file.content_fileurl))
        result = cursor.fetchone()
        conn.close()

        self.assertEqual(result['consecutive_failures'], 3, '应该失败 3 次')
        self.assertEqual(result['position_in_section'], 4, '位置信息应保持不变')

        # 从数据库读取
        failed_files = self.db.get_failed_files(course_id=self.course_id)
        retrieved_file = failed_files[0]

        # 验证位置信息
        self.assertEqual(retrieved_file.position_in_section, 4)

        # 验证文件名
        generated_filename = Task._generate_filename_with_index(retrieved_file)
        self.assertEqual(generated_filename, '05 assignment.pdf', '多次失败后文件名应保持一致')

    def test_system_file_no_index_on_retry(self):
        """测试系统文件在重试时不添加索引"""
        file = File(
            module_id=12347,
            section_name='第1周',
            section_id=1,
            module_name='元数据',
            content_filepath='/',
            content_filename='metadata.json',
            content_fileurl='https://example.com/metadata.json',
            content_filesize=512,
            content_timemodified=int(time.time()),
            module_modname='resource',
            content_type='json',
            content_isexternalfile=False,
            saved_to='/path/to/metadata.json',
            position_in_section=None  # 系统文件无位置信息
        )

        # 保存失败记录
        self.db.save_failed_file(file, self.course_id, self.course_fullname, '错误')

        # 读取失败文件
        failed_files = self.db.get_failed_files(course_id=self.course_id)
        retrieved_file = failed_files[0]

        # 验证无位置信息
        self.assertIsNone(retrieved_file.position_in_section, '系统文件不应有位置信息')

        # 验证文件名（不添加索引）
        generated_filename = Task._generate_filename_with_index(retrieved_file)
        self.assertEqual(generated_filename, 'metadata.json', '系统文件不应添加索引')

    def test_reset_for_retry_preserves_position(self):
        """测试重置重试状态时保留位置信息"""
        file = File(
            module_id=12348,
            section_name='第3周',
            section_id=3,
            module_name='笔记',
            content_filepath='/',
            content_filename='notes.pdf',
            content_fileurl='https://example.com/notes.pdf',
            content_filesize=1536000,
            content_timemodified=int(time.time()),
            module_modname='resource',
            content_type='pdf',
            content_isexternalfile=False,
            saved_to='/path/to/08-notes.pdf',
            position_in_section=7  # 位置：7
        )

        # 保存失败记录
        self.db.save_failed_file(file, self.course_id, self.course_fullname, '下载失败')

        # 重置重试状态
        self.db.reset_failed_file_for_retry(file, self.course_id)

        # 验证数据库状态
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT download_status, consecutive_failures, position_in_section
            FROM files
            WHERE module_id = ? AND content_fileurl = ?
        """, (file.module_id, file.content_fileurl))
        result = cursor.fetchone()
        conn.close()

        self.assertEqual(result['download_status'], 'pending', '状态应重置为 pending')
        self.assertEqual(result['consecutive_failures'], 0, '连续失败次数应重置为 0')
        self.assertEqual(result['position_in_section'], 7, '位置信息应保持不变')

    def test_original_filename_with_numbers_preserved(self):
        """测试原始文件名包含数字时的保留"""
        file = File(
            module_id=12349,
            section_name='第4周',
            section_id=4,
            module_name='课程资料',
            content_filepath='/',
            content_filename='01-introduction.pdf',  # 原始文件名包含 "01-"
            content_fileurl='https://example.com/01-introduction.pdf',
            content_filesize=3072000,
            content_timemodified=int(time.time()),
            module_modname='resource',
            content_type='pdf',
            content_isexternalfile=False,
            saved_to='/path/to/03-01-introduction.pdf',
            position_in_section=2  # 位置：2，文件名应为 03-01-introduction.pdf
        )

        # 保存失败记录
        self.db.save_failed_file(file, self.course_id, self.course_fullname, '失败')

        # 读取失败文件
        failed_files = self.db.get_failed_files(course_id=self.course_id)
        retrieved_file = failed_files[0]

        # 验证位置信息
        self.assertEqual(retrieved_file.position_in_section, 2)

        # 验证文件名（保留原始文件名中的 "01-"）
        generated_filename = Task._generate_filename_with_index(retrieved_file)
        self.assertEqual(
            generated_filename,
            '03 01-introduction.pdf',
            '应保留原始文件名中的数字前缀'
        )


if __name__ == '__main__':
    unittest.main()
