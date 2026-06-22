# -*- coding: utf-8 -*-
"""
单元测试：文件名前缀索引系统

测试文件名索引功能的实现：
- result_builder.py 中的位置分配逻辑
- task.py 中的索引文件名生成
- 系统文件的排除规则
"""

import time
import unittest
from unittest.mock import MagicMock

from moodle_dl.downloader.task import Task
from moodle_dl.downloader.task_file_ops import TaskFileOps
from moodle_dl.moodle.result_builder import ResultBuilder
from moodle_dl.types import Course, File, MoodleURL


class TestFilenamePrefixIndexing(unittest.TestCase):
    """测试文件名前缀索引功能"""

    def setUp(self):
        """测试前的准备工作"""
        # 创建模拟的 MoodleURL
        self.moodle_url = MoodleURL(
            use_http=False, domain="example.com", path="/moodle/"
        )

        # 创建 ResultBuilder 实例
        self.result_builder = ResultBuilder(
            moodle_url=self.moodle_url,
            version=2020061500,
            mod_plurals={"book": "Books", "assign": "Assignments"},
        )

    def test_is_system_file(self):
        """测试系统文件识别"""
        # 隐藏文件应返回 True
        self.assertTrue(ResultBuilder._is_system_file(".hidden"))
        self.assertTrue(ResultBuilder._is_system_file(".DS_Store"))

        # 基础元数据文件应返回 True
        self.assertTrue(ResultBuilder._is_system_file("metadata.json"))
        self.assertTrue(ResultBuilder._is_system_file("table of contents"))
        self.assertTrue(ResultBuilder._is_system_file("table of contents.html"))

        # 所有 .json 文件应返回 True（Resource 模块元数据）
        self.assertTrue(
            ResultBuilder._is_system_file(
                "[Mandatory] Week 1 - Recorded Lecture 1 Handouts.json"
            )
        )
        self.assertTrue(ResultBuilder._is_system_file("homework_metadata.json"))
        self.assertTrue(ResultBuilder._is_system_file("lecture_notes.json"))
        self.assertTrue(ResultBuilder._is_system_file("data.json"))

        # 各模块生成的文件应返回 True
        self.assertTrue(
            ResultBuilder._is_system_file("questions.json")
        )  # Feedback/Quiz
        self.assertTrue(ResultBuilder._is_system_file("analysis.json"))  # Feedback
        self.assertTrue(ResultBuilder._is_system_file("grade"))  # Lesson
        self.assertTrue(
            ResultBuilder._is_system_file("entry_metadata.json")
        )  # Glossary/Data
        self.assertTrue(
            ResultBuilder._is_system_file("session_1234567890.json")
        )  # Chat
        self.assertTrue(ResultBuilder._is_system_file("homework_info"))  # Info 文件
        self.assertTrue(ResultBuilder._is_system_file("lecture_notes.md"))  # Notes 文件

        # 普通文件应返回 False
        self.assertFalse(ResultBuilder._is_system_file("lecture.pdf"))
        self.assertFalse(ResultBuilder._is_system_file("01-introduction.pdf"))
        self.assertFalse(ResultBuilder._is_system_file("video.mp4"))
        self.assertFalse(ResultBuilder._is_system_file("homework.doc"))
        self.assertFalse(ResultBuilder._is_system_file("readme.txt"))

    def test_assign_positions_basic(self):
        """测试基本的位置分配"""
        files = [
            self._create_file("file1.pdf"),
            self._create_file("file2.pdf"),
            self._create_file("file3.pdf"),
        ]

        self.result_builder._assign_positions_to_files(files)

        self.assertEqual(files[0].position_in_section, 0)
        self.assertEqual(files[1].position_in_section, 1)
        self.assertEqual(files[2].position_in_section, 2)

    def test_assign_positions_with_system_files(self):
        """测试包含系统文件时的位置分配"""
        files = [
            self._create_file("file1.pdf"),
            self._create_file("metadata.json"),  # 系统文件
            self._create_file("file2.pdf"),
            self._create_file("Table of Contents.html"),  # 系统文件
            self._create_file("file3.pdf"),
            self._create_file(".hidden"),  # 系统文件
        ]

        self.result_builder._assign_positions_to_files(files)

        # 普通文件应获得连续的位置索引
        self.assertEqual(files[0].position_in_section, 0)  # file1.pdf
        self.assertIsNone(files[1].position_in_section)  # metadata.json
        self.assertEqual(files[2].position_in_section, 1)  # file2.pdf
        self.assertIsNone(files[3].position_in_section)  # Table of Contents.html
        self.assertEqual(files[4].position_in_section, 2)  # file3.pdf
        self.assertIsNone(files[5].position_in_section)  # .hidden

    def test_assign_positions_are_scoped_to_logical_output_folder(self):
        """不同输出目录的文件应各自从 0 开始编号

        With section-wide indexing (the default), non-book files in
        the same section share one 0-based counter. The exception is
        the book modname: each book chapter (each content_filepath
        value) gets its own 0-based counter — every chapter is a
        standalone "booklet" the user navigates as its own entity
        (matches the official Moodle mobile app's UX, which groups
        book content by chapter).

        Different sections and different book modules (different
        module_ids) get independent counters.
        """
        section_file_1 = self._create_file("section-a.pdf", module_id=1, module_modname="resource")
        book_root = self._create_file(
            "Book overview.html",
            module_id=2,
            module_modname="book",
            content_filepath="/",
        )
        chapter_index = self._create_file(
            "index.html",
            module_id=2,
            module_modname="book",
            content_filepath="/01 - Introduction/",
        )
        chapter_attachment = self._create_file(
            "attachment.pdf",
            module_id=2,
            module_modname="book",
            content_filepath="/01 - Introduction/",
        )
        section_file_2 = self._create_file("section-b.pdf", module_id=3, module_modname="resource")

        self.result_builder._assign_positions_to_files([
            section_file_1,
            book_root,
            chapter_index,
            chapter_attachment,
            section_file_2,
        ])

        # Order in the input list:
        #   1. section_file_1 (resource, sec=1)               → 0  (section scope)
        #   2. book_root       (book, mod=2)                   → 0  (book scope, whole book)
        #   3. chapter_index   (book, mod=2)                   → 1  (book scope, continues)
        #   4. chapter_attach  (book, mod=2)                   → 2  (book scope, continues)
        #   5. section_file_2  (resource, sec=1)               → 1  (section scope, continues)
        self.assertEqual(section_file_1.position_in_section, 0)
        self.assertEqual(section_file_2.position_in_section, 1)
        self.assertEqual(book_root.position_in_section, 0)
        self.assertEqual(chapter_index.position_in_section, 1)
        self.assertEqual(chapter_attachment.position_in_section, 2)

    def test_filename_generation_with_position(self):
        """测试带位置索引的文件名生成"""
        # position=0 → "*01*"
        file = self._create_file("lecture.pdf")
        file.position_in_section = 0
        filename = TaskFileOps(MagicMock()).generate_filename_with_index(file)
        self.assertEqual(filename, "*01* lecture.pdf")

        # position=4 → "*05*"
        file = self._create_file("notes.pdf")
        file.position_in_section = 4
        filename = TaskFileOps(MagicMock()).generate_filename_with_index(file)
        self.assertEqual(filename, "*05* notes.pdf")

        # position=9 → "*10*"
        file = self._create_file("quiz.pdf")
        file.position_in_section = 9
        filename = TaskFileOps(MagicMock()).generate_filename_with_index(file)
        self.assertEqual(filename, "*10* quiz.pdf")

    def test_filename_generation_large_position(self):
        """测试大位置索引（3位数）"""
        # position=98 → "*99*" (仍然是2位)
        file = self._create_file("file.pdf")
        file.position_in_section = 98
        filename = TaskFileOps(MagicMock()).generate_filename_with_index(file)
        self.assertEqual(filename, "*99* file.pdf")

        # position=99 → "*100*" (3位数)
        file = self._create_file("file.pdf")
        file.position_in_section = 99
        filename = TaskFileOps(MagicMock()).generate_filename_with_index(file)
        self.assertEqual(filename, "*100* file.pdf")

        # position=123 → "*124*" (3位数)
        file = self._create_file("file.pdf")
        file.position_in_section = 123
        filename = TaskFileOps(MagicMock()).generate_filename_with_index(file)
        self.assertEqual(filename, "*124* file.pdf")

    def test_filename_generation_without_position(self):
        """测试无位置索引的文件名生成（系统文件）"""
        file = self._create_file("metadata.json")
        file.position_in_section = None
        filename = TaskFileOps(MagicMock()).generate_filename_with_index(file)
        self.assertEqual(filename, "metadata.json")

    def test_filename_generation_preserves_original(self):
        """测试保留原始文件名（关键测试）"""
        # 原始文件名本身就有数字前缀，应完整保留
        file = self._create_file("01-introduction.pdf")
        file.position_in_section = 4
        filename = TaskFileOps(MagicMock()).generate_filename_with_index(file)
        # 应该是 "*05* 01-introduction.pdf"，不是 "*05* introduction.pdf"
        self.assertEqual(filename, "*05* 01-introduction.pdf")

        # 更复杂的例子
        file = self._create_file("2024-01-15-lecture.pdf")
        file.position_in_section = 0
        filename = TaskFileOps(MagicMock()).generate_filename_with_index(file)
        self.assertEqual(filename, "*01* 2024-01-15-lecture.pdf")

    def test_empty_file_list(self):
        """测试空文件列表"""
        files = []
        self.result_builder._assign_positions_to_files(files)
        self.assertEqual(len(files), 0)

    def test_all_system_files(self):
        """测试全部是系统文件的情况"""
        files = [
            self._create_file("metadata.json"),
            self._create_file("Table of Contents.html"),
            self._create_file(".hidden"),
        ]

        self.result_builder._assign_positions_to_files(files)

        # 所有文件都应该没有位置索引
        for file in files:
            self.assertIsNone(file.position_in_section)

    def _create_file(
        self,
        filename: str,
        *,
        module_id: int = 12345,
        module_modname: str = "resource",
        content_filepath: str = "/",
    ) -> File:
        """创建测试用的 File 对象"""
        return File(
            module_id=module_id,
            section_name="第1周",
            section_id=1,
            module_name="测试模块",
            content_filepath=content_filepath,
            content_filename=filename,
            content_fileurl=f"https://example.com/{filename}",
            content_filesize=1024,
            content_timemodified=int(time.time()),
            module_modname=module_modname,
            content_type="pdf",
            content_isexternalfile=False,
            saved_to=f"/path/to/{filename}",
        )


if __name__ == "__main__":
    unittest.main()
