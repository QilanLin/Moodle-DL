# -*- coding: utf-8 -*-
"""
database_manager.py 单元测试

测试数据库管理功能：
- 交互式数据库管理
- 旧文件删除
- 文件状态重置
"""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch, mock_open

from moodle_dl.cli.database_manager import DatabaseManager
from moodle_dl.types import File, Course
from moodle_dl.utils import PathTools as PT


class TestDatabaseManagerInit(unittest.TestCase):
    """DatabaseManager 初始化测试"""

    def test_init(self):
        """测试初始化"""
        config = Mock()
        opts = Mock()
        state_recorder = Mock()
        config.get_misc_files_path.return_value = '/tmp/test_moodle'

        with patch('moodle_dl.cli.database_manager.StateRecorder', return_value=state_recorder):
            manager = DatabaseManager(config, opts)

        self.assertEqual(manager.config, config)
        self.assertEqual(manager.opts, opts)
        self.assertEqual(manager.state_recorder, state_recorder)


class TestInteractivelyManageDatabase(unittest.TestCase):
    """interactively_manage_database 方法测试"""

    def setUp(self):
        self.config = Mock()
        self.config.get_misc_files_path.return_value = '/tmp/test_moodle'
        self.opts = Mock()
        self.state_recorder = Mock()

        with patch('moodle_dl.cli.database_manager.StateRecorder', return_value=self.state_recorder):
            self.manager = DatabaseManager(self.config, self.opts)

    @patch('moodle_dl.cli.database_manager.MoodleService.filter_courses')
    def test_no_stored_files(self, mock_filter):
        """测试没有存储的文件"""
        self.state_recorder.get_stored_files.return_value = []
        mock_filter.return_value = []

        result = self.manager.interactively_manage_database()

        # Should return early without doing anything
        self.assertIsNone(result)

    @patch('os.path.exists')
    @patch('moodle_dl.moodle.moodle_service.MoodleService.filter_courses')
    def test_no_missing_files(self, mock_filter, mock_exists):
        """测试没有缺失的文件"""
        from moodle_dl.types import File as FileType
        # File exists - use actual File object
        file1 = FileType(
            module_id=1,
            section_name='Section 1',
            section_id=1,
            module_name='Resource',
            content_filepath='/path',
            content_filename='file1.pdf',
            content_fileurl='https://example.com/file1.pdf',
            content_filesize=1024,
            content_timemodified=123456,
            module_modname='resource',
            content_type='application/pdf',
            content_isexternalfile=False,
            saved_to='/existing/path'
        )

        mock_exists.return_value = True

        self.state_recorder.get_stored_files.return_value = [
            Course(1, 'Course 1', [file1])
        ]
        mock_filter.return_value = [
            Course(1, 'Course 1', [file1])
        ]

        result = self.manager.interactively_manage_database()

        # Should return early since all files exist (no courses have missing files)
        self.assertIsNone(result)

    @patch('os.path.exists')
    @patch('moodle_dl.moodle.moodle_service.MoodleService.filter_courses')
    @patch('moodle_dl.utils.Cutie.select')
    @patch('moodle_dl.utils.Cutie.select_multiple')
    def test_select_course_and_sections(self, mock_select_multiple, mock_select, mock_filter, mock_exists):
        """测试选择课程和章节"""
        from moodle_dl.types import File as FileType
        # Setup test data - use actual File objects
        file1 = FileType(
            module_id=1,
            section_name='Section 1',
            section_id=1,
            module_name='Resource',
            content_filepath='/path',
            content_filename='file1.pdf',
            content_fileurl='https://example.com/file1.pdf',
            content_filesize=1024,
            content_timemodified=123456,
            module_modname='resource',
            content_type='application/pdf',
            content_isexternalfile=False,
            saved_to='/missing/file1.pdf'
        )

        file2 = FileType(
            module_id=2,
            section_name='Section 2',
            section_id=2,
            module_name='Resource',
            content_filepath='/path',
            content_filename='file2.pdf',
            content_fileurl='https://example.com/file2.pdf',
            content_filesize=1024,
            content_timemodified=123456,
            module_modname='resource',
            content_type='application/pdf',
            content_isexternalfile=False,
            saved_to='/missing/file2.pdf'
        )

        course1 = Course(1, 'Course 1', [file1, file2])
        self.state_recorder.get_stored_files.return_value = [course1]
        mock_filter.return_value = [course1]

        # Both files missing - provide enough side_effect values
        mock_exists.return_value = False
        mock_select.return_value = 0  # Select first course
        mock_select_multiple.side_effect = [[0], [0]]  # Select all sections, then all files

        result = self.manager.interactively_manage_database()

        # Should complete without error
        self.assertIsNone(result)


class TestDeleteOldFiles(unittest.TestCase):
    """delete_old_files 方法测试"""

    def setUp(self):
        self.config = Mock()
        self.config.get_misc_files_path.return_value = '/tmp/test_moodle'
        self.opts = Mock()
        self.state_recorder = Mock()

        with patch('moodle_dl.cli.database_manager.StateRecorder', return_value=self.state_recorder):
            self.manager = DatabaseManager(self.config, self.opts)

    @patch('moodle_dl.utils.Cutie.select')
    def test_no_old_files(self, mock_select):
        """测试没有旧文件"""
        self.state_recorder.get_old_files.return_value = []

        with patch('builtins.print'):
            result = self.manager.delete_old_files()

        # Should print message and return early
        self.assertIsNone(result)

    @patch('moodle_dl.utils.PathTools.remove_file')
    @patch('moodle_dl.utils.Cutie.select')
    @patch('moodle_dl.utils.Cutie.select_multiple')
    def test_delete_old_files_with_file_removal(self, mock_select_multiple, mock_select, mock_remove):
        """测试删除旧文件"""
        # Setup test data - use actual File object instead of Mock
        from moodle_dl.types import File as FileType
        file1 = FileType(
            module_id=1,
            section_name='Section 1',
            section_id=1,
            module_name='Resource',
            content_filepath='/path',
            content_filename='old1.pdf',
            content_fileurl='https://example.com/file1.pdf',
            content_filesize=1024,
            content_timemodified=123456,
            module_modname='resource',
            content_type='application/pdf',
            content_isexternalfile=False,
            saved_to='/old/file1.pdf'
        )

        course = Course(1, 'Course 1', [file1])
        self.state_recorder.get_old_files.return_value = [course]
        mock_select.return_value = 0
        # select_multiple is called twice: once for sections, once for files
        mock_select_multiple.side_effect = [[0], [0]]  # Select all sections, then all files

        # Setup batch_delete to be called
        files_deleted = []
        self.state_recorder.batch_delete_files_from_db.side_effect = lambda files: files_deleted.extend(files)

        with patch('builtins.print'):
            result = self.manager.delete_old_files()

        # Should complete successfully
        self.assertIsNone(result)
        # Verify batch_delete was called with the file
        self.assertEqual(len(files_deleted), 1)


class TestResetAllDownloadedFiles(unittest.TestCase):
    """reset_all_downloaded_files 方法测试"""

    def setUp(self):
        self.config = Mock()
        self.config.get_misc_files_path.return_value = '/tmp/test_moodle'
        self.opts = Mock()
        self.state_recorder = Mock()

        with patch('moodle_dl.cli.database_manager.StateRecorder', return_value=self.state_recorder):
            self.manager = DatabaseManager(self.config, self.opts)

        # Mock db_file
        self.state_recorder.db_file = '/tmp/test.db'

    def test_no_stored_files(self):
        """测试没有存储的文件"""
        self.state_recorder.get_stored_files.return_value = []

        with patch('builtins.print'):
            result = self.manager.reset_all_downloaded_files()

        self.assertIsNone(result)

    @patch('sqlite3.connect')
    def test_no_downloaded_files(self, mock_connect):
        """测试没有已下载的文件"""
        # All files have empty saved_to
        file1 = Mock()
        file1.saved_to = ''
        file1.file_id = 1

        file2 = Mock()
        file2.saved_to = None
        file2.file_id = 2

        course1 = Course(1, 'Course 1', [file1, file2])
        self.state_recorder.get_stored_files.return_value = [course1]

        with patch('builtins.print'):
            result = self.manager.reset_all_downloaded_files()

        self.assertIsNone(result)

    @patch('sqlite3.connect')
    def test_reset_success(self, mock_connect):
        """测试成功重置文件状态"""
        # Setup test data
        file1 = Mock()
        file1.saved_to = '/path/to/file1.pdf'
        file1.file_id = 1

        file2 = Mock()
        file2.saved_to = '/path/to/file2.pdf'
        file2.file_id = 2

        course1 = Course(1, 'Course 1', [file1, file2])
        self.state_recorder.get_stored_files.return_value = [course1]

        # Mock the connection and cursor
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        with patch('builtins.print'):
            result = self.manager.reset_all_downloaded_files()

        # Verify SQL update was called for each file
        self.assertEqual(mock_cursor.execute.call_count, 2)
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

        self.assertIsNone(result)

    @patch('sqlite3.connect')
    def test_reset_rollback_on_error(self, mock_connect):
        """测试错误时回滚"""
        # Setup test data
        file1 = Mock()
        file1.saved_to = '/path/to/file1.pdf'
        file1.file_id = 1

        course1 = Course(1, 'Course 1', [file1])
        self.state_recorder.get_stored_files.return_value = [course1]

        # Mock connection that raises error on cursor
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception('DB error')
        mock_connect.return_value = mock_conn

        with patch('builtins.print'):
            result = self.manager.reset_all_downloaded_files()

        # Should still complete without crashing
        self.assertIsNone(result)
        # Verify rollback was called
        mock_conn.rollback.assert_called_once()


class TestDatabaseManagerIntegration(unittest.TestCase):
    """集成测试 - 完整流程"""

    def setUp(self):
        self.config = Mock()
        self.config.get_misc_files_path.return_value = '/tmp/test_moodle'
        self.opts = Mock()
        self.state_recorder = Mock()

        with patch('moodle_dl.cli.database_manager.StateRecorder', return_value=self.state_recorder):
            self.manager = DatabaseManager(self.config, self.opts)

    @patch('os.path.exists')
    @patch('moodle_dl.moodle.moodle_service.MoodleService.filter_courses')
    @patch('moodle_dl.utils.Cutie.select')
    @patch('moodle_dl.utils.Cutie.select_multiple')
    def test_interactively_manage_database_full_flow(self, mock_select_multiple, mock_select, mock_filter, mock_exists):
        """测试完整的数据库管理流程"""
        from moodle_dl.types import File as FileType
        # Setup: course with some missing files - use actual File objects
        file1 = FileType(
            module_id=1,
            section_name='Section 1',
            section_id=1,
            module_name='Resource',
            content_filepath='/path',
            content_filename='file1.pdf',
            content_fileurl='https://example.com/file1.pdf',
            content_filesize=1024,
            content_timemodified=123456,
            module_modname='resource',
            content_type='application/pdf',
            content_isexternalfile=False,
            saved_to='/missing/file1.pdf'
        )

        file2 = FileType(
            module_id=2,
            section_name='Section 1',
            section_id=1,
            module_name='Resource',
            content_filepath='/path',
            content_filename='file2.pdf',
            content_fileurl='https://example.com/file2.pdf',
            content_filesize=2048,
            content_timemodified=123456,
            module_modname='resource',
            content_type='application/pdf',
            content_isexternalfile=False,
            saved_to='/existing/file2.pdf'
        )

        course1 = Course(1, 'Course 1', [file1, file2])

        stored_files = [course1]
        mock_filter.return_value = stored_files
        mock_exists.return_value = False  # First file missing
        mock_select.return_value = 0
        mock_select_multiple.side_effect = [[0], [0]]  # Select all sections, then all files

        with patch('builtins.print'):
            result = self.manager.interactively_manage_database()

        # Should complete without error
        self.assertIsNone(result)

    @patch('os.path.exists')
    @patch('moodle_dl.moodle.moodle_service.MoodleService.filter_courses')
    @patch('moodle_dl.utils.PathTools.remove_file')
    @patch('moodle_dl.utils.Cutie.select')
    @patch('moodle_dl.utils.Cutie.select_multiple')
    def test_delete_old_files_full_flow(self, mock_select_multiple, mock_select, mock_remove, mock_filter, mock_exists):
        """测试完整的旧文件删除流程"""
        # Setup: course with old files - use actual File objects
        from moodle_dl.types import File as FileType
        file1 = FileType(
            module_id=1,
            section_name='Section 1',
            section_id=1,
            module_name='Resource',
            content_filepath='/path',
            content_filename='old1.pdf',
            content_fileurl='https://example.com/file1.pdf',
            content_filesize=1024,
            content_timemodified=123456,
            module_modname='resource',
            content_type='application/pdf',
            content_isexternalfile=False,
            saved_to='/old/file1.pdf'
        )

        file2 = FileType(
            module_id=2,
            section_name='Section 2',
            section_id=2,
            module_name='Resource',
            content_filepath='/path',
            content_filename='old2.pdf',
            content_fileurl='https://example.com/file2.pdf',
            content_filesize=2048,
            content_timemodified=123456,
            module_modname='resource',
            content_type='application/pdf',
            content_isexternalfile=False,
            saved_to='/old/file2.pdf'
        )

        course1 = Course(1, 'Course 1', [file1, file2])

        stored_files = [course1]
        self.state_recorder.get_old_files.return_value = stored_files
        mock_filter.return_value = stored_files
        mock_exists.return_value = True

        files_deleted = []
        self.state_recorder.batch_delete_files_from_db.side_effect = lambda fs: files_deleted.extend(fs)

        mock_select.return_value = 0
        mock_select_multiple.side_effect = [[0], [0]]  # Select all sections, then all files

        with patch('builtins.print'):
            result = self.manager.delete_old_files()

        # Should complete without error
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
