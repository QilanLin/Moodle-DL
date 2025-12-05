# -*- coding: utf-8 -*-
"""StateRecorder 重建逻辑回归测试"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.types import MoodleDlOpts


class TestStateRecorderRebuild(unittest.TestCase):
    """验证 StateRecorder 的重建策略覆盖关键分支"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'moodle_state.db')

        self.config = MagicMock(spec=ConfigHelper)
        self.config.get_misc_files_path.return_value = self.temp_dir
        self.opts = MagicMock(spec=MoodleDlOpts)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_upgrade_from_v8_to_v9(self):
        """旧版本（v8）应直接重建为 v9 并带上新表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        StateRecorder._create_fresh_database_v8(cursor)
        conn.commit()
        conn.close()

        StateRecorder(self.config, self.opts)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        version = cursor.execute('PRAGMA user_version;').fetchone()[0]
        self.assertEqual(version, 9)
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incomplete_downloads';"
        )
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_rebuilds_when_incomplete_downloads_missing(self):
        """缺少 incomplete_downloads 表时应触发重建"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        StateRecorder._create_fresh_database_v9(cursor)
        conn.commit()
        cursor.execute('DROP TABLE IF EXISTS incomplete_downloads;')
        conn.commit()
        conn.close()

        StateRecorder(self.config, self.opts)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='incomplete_downloads';"
        )
        self.assertIsNotNone(cursor.fetchone())
        version = cursor.execute('PRAGMA user_version;').fetchone()[0]
        self.assertEqual(version, 9)
        conn.close()


if __name__ == '__main__':
    unittest.main()
