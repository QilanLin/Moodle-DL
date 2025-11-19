"""
集成测试：位置索引字段的数据库集成

测试 position_in_section 字段在数据库中的存储和读取：
- 数据库 schema v8 升级
- 保存带位置信息的文件到数据库
- 从数据库读取位置信息
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


class TestPositionDatabaseIntegration(unittest.TestCase):
    """测试位置索引的数据库集成"""

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

        # 初始化数据库（这会自动升级到 v8）
        self.db = StateRecorder(self.config, self.opts)

        # 验证数据库已升级到 v8
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('PRAGMA user_version;')
        version = cursor.fetchone()[0]
        conn.close()

        if version < 8:
            raise RuntimeError(f"数据库版本应为 8，实际为 {version}")

    def tearDown(self):
        """测试后的清理工作"""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_database_has_position_column(self):
        """测试数据库是否包含 position_in_section 列"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(files);")
        columns = [row[1] for row in cursor.fetchall()]
        conn.close()

        self.assertIn('position_in_section', columns, "数据库应包含 position_in_section 列")

    def test_save_file_with_position(self):
        """测试保存带位置信息的文件到数据库"""
        file = File(
            module_id=12345,
            section_name='第1周',
            section_id=1,
            module_name='测试文件',
            content_filepath='/',
            content_filename='lecture.pdf',
            content_fileurl='https://example.com/lecture.pdf',
            content_filesize=1024,
            content_timemodified=int(time.time()),
            module_modname='resource',
            content_type='pdf',
            content_isexternalfile=False,
            saved_to='/path/to/lecture.pdf',
            position_in_section=5  # 设置位置为 5
        )

        # 保存到数据库
        self.db.save_file(file, course_id=101, course_fullname='测试课程')

        # 从数据库读取
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT position_in_section
            FROM files
            WHERE module_id = ? AND content_fileurl = ?
        """, (file.module_id, file.content_fileurl))

        result = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(result, "文件应被保存到数据库")
        self.assertEqual(result['position_in_section'], 5, "位置信息应被正确保存")

    def test_save_file_without_position(self):
        """测试保存无位置信息的文件到数据库（系统文件）"""
        file = File(
            module_id=12346,
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
            position_in_section=None  # 无位置信息
        )

        # 保存到数据库
        self.db.save_file(file, course_id=101, course_fullname='测试课程')

        # 从数据库读取
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT position_in_section
            FROM files
            WHERE module_id = ? AND content_fileurl = ?
        """, (file.module_id, file.content_fileurl))

        result = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(result, "文件应被保存到数据库")
        self.assertIsNone(result['position_in_section'], "系统文件不应有位置信息")

    def test_read_file_from_database(self):
        """测试从数据库读取文件的位置信息"""
        # 先保存文件
        file = File(
            module_id=12347,
            section_name='第2周',
            section_id=2,
            module_name='测试文件',
            content_filepath='/',
            content_filename='notes.pdf',
            content_fileurl='https://example.com/notes.pdf',
            content_filesize=2048,
            content_timemodified=int(time.time()),
            module_modname='resource',
            content_type='pdf',
            content_isexternalfile=False,
            saved_to='/path/to/notes.pdf',
            position_in_section=10
        )

        self.db.save_file(file, course_id=101, course_fullname='测试课程')

        # 使用 File.fromRow() 读取
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT *
            FROM files
            WHERE module_id = ? AND content_fileurl = ?
        """, (file.module_id, file.content_fileurl))

        row = cursor.fetchone()
        conn.close()

        # 使用 File.fromRow() 构造对象
        loaded_file = File.fromRow(row)

        self.assertEqual(loaded_file.position_in_section, 10, "从数据库读取的位置应正确")
        self.assertEqual(loaded_file.content_filename, 'notes.pdf')

    def test_position_index_exists(self):
        """测试位置索引是否存在（用于加速查询）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index' AND name='idx_position_in_section';
        """)
        result = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(result, "应存在 idx_position_in_section 索引")


if __name__ == '__main__':
    unittest.main()
