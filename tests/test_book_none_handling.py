# -*- coding: utf-8 -*-
"""
book.py 单元测试 - None 值处理

测试 Book 模块对 None 值的处理：
- _get_chapter_title_from_toc 方法处理 href 为 None 的情况
"""

import unittest
from unittest.mock import MagicMock, patch

from moodle_dl.moodle.mods.book import BookMod
from moodle_dl.types import Course, MoodleDlOpts
from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.request_helper import RequestHelper


class TestBookModNoneHandling(unittest.TestCase):
    """测试 BookMod 对 href 为 None 的处理"""

    def test_get_chapter_title_from_toc_with_none_href(self):
        """测试 TOC 中 href 为 None 时不会崩溃"""
        # 创建必要的 mock 对象
        request_helper = MagicMock(spec=RequestHelper)
        config = MagicMock(spec=ConfigHelper)
        moodle_version = 2023100900
        user_id = 1
        last_timestamps = {}

        book = BookMod(request_helper, moodle_version, user_id, last_timestamps, config)

        # 创建一个 TOC，其中某些项的 href 是 None
        toc = [
            {
                'title': 'Chapter 1',
                'href': '12345/index.html',
                'subitems': []
            },
            {
                'title': 'Chapter 2',
                'href': None,  # href 为 None
                'subitems': []
            },
            {
                'title': 'Chapter 3',
                'href': '67890/page.html',
                'subitems': [
                    {
                        'title': 'Subsection 3.1',
                        'href': None,  # 子项的 href 为 None
                        'subitems': []
                    }
                ]
            }
        ]

        # 应该不会抛出 AttributeError: 'NoneType' object has no attribute 'startswith'
        try:
            # 测试查找存在的章节
            title = book._get_chapter_title_from_toc('12345', toc)
            self.assertEqual(title, 'Chapter 1')

            # 测试查找不存在的章节（应该返回默认标题）
            title = book._get_chapter_title_from_toc('99999', toc)
            self.assertEqual(title, 'Chapter 99999')

            # 测试查找存在的章节，其子项的 href 为 None（应该正常找到章节）
            # 67890 在 TOC 中有标题 'Chapter 3'
            title = book._get_chapter_title_from_toc('67890', toc)
            self.assertEqual(title, 'Chapter 3')

        except AttributeError as e:
            if "'NoneType' object has no attribute 'startswith'" in str(e):
                self.fail("_get_chapter_title_from_toc raised AttributeError for None href")
            else:
                raise

    def test_get_chapter_title_from_toc_with_empty_href(self):
        """测试 TOC 中 href 为空字符串时正常工作"""
        request_helper = MagicMock(spec=RequestHelper)
        config = MagicMock(spec=ConfigHelper)
        moodle_version = 2023100900
        user_id = 1
        last_timestamps = {}

        book = BookMod(request_helper, moodle_version, user_id, last_timestamps, config)

        toc = [
            {
                'title': 'Chapter 1',
                'href': '',  # 空字符串
                'subitems': []
            },
            {
                'title': 'Chapter 2',
                'href': None,  # None
                'subitems': []
            }
        ]

        try:
            # 应该不会崩溃
            title = book._get_chapter_title_from_toc('12345', toc)
            self.assertEqual(title, 'Chapter 12345')

        except AttributeError as e:
            if "'NoneType' object has no attribute 'startswith'" in str(e):
                self.fail("_get_chapter_title_from_toc raised AttributeError for None href")
            else:
                raise

    def test_get_chapter_title_from_toc_all_none(self):
        """测试所有项的 href 都是 None 的情况"""
        request_helper = MagicMock(spec=RequestHelper)
        config = MagicMock(spec=ConfigHelper)
        moodle_version = 2023100900
        user_id = 1
        last_timestamps = {}

        book = BookMod(request_helper, moodle_version, user_id, last_timestamps, config)

        toc = [
            {
                'title': 'Chapter 1',
                'href': None,
                'subitems': [
                    {
                        'title': 'Sub 1.1',
                        'href': None,
                        'subitems': []
                    }
                ]
            }
        ]

        try:
            title = book._get_chapter_title_from_toc('12345', toc)
            self.assertEqual(title, 'Chapter 12345')

        except AttributeError as e:
            if "'NoneType' object has no attribute 'startswith'" in str(e):
                self.fail("_get_chapter_title_from_toc raised AttributeError for None href")
            else:
                raise


if __name__ == '__main__':
    unittest.main()
