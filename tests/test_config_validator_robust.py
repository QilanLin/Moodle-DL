# -*- coding: utf-8 -*-
"""
配置验证器强健壮等价类测试 (Robust Equivalence Class Testing)
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from moodle_dl.config_validator import ConfigValidator, ValidationResult


class TestConfigValidatorRobust(unittest.TestCase):
    """ConfigValidator 的强健壮等价类测试"""

    def setUp(self):
        self.validator = ConfigValidator(strict=False)
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ========== 有效等价类测试 ==========

    def test_valid_minimal_config(self):
        """有效等价类: 最小有效配置"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/'
        }
        result = self.validator.validate_config_data(config)
        self.assertTrue(result.is_valid)

    def test_valid_complete_config(self):
        """有效等价类: 完整配置"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/',
            'token': 'abc123',
            'download_course_ids': [1, 2, 3],
            'restricted_filenames': True
        }
        result = self.validator.validate_config_data(config)
        self.assertTrue(result.is_valid)

    def test_valid_domain_with_subdomain(self):
        """有效等价类: 带子域名的域名"""
        config = {
            'moodle_domain': 'moodle.uni.edu.cn',
            'moodle_path': '/'
        }
        result = self.validator.validate_config_data(config)
        self.assertTrue(result.is_valid)

    # ========== 无效等价类测试 ==========

    def test_invalid_missing_required_field(self):
        """无效等价类: 缺少必需字段"""
        config = {
            'moodle_domain': 'moodle.example.com'
            # 缺少 moodle_path
        }
        result = self.validator.validate_config_data(config)
        self.assertFalse(result.is_valid)
        self.assertTrue(result.has_errors())

    def test_invalid_wrong_type_domain(self):
        """无效等价类: 域名类型错误"""
        config = {
            'moodle_domain': 123,
            'moodle_path': '/'
        }
        result = self.validator.validate_config_data(config)
        self.assertFalse(result.is_valid)

    def test_invalid_wrong_type_course_ids(self):
        """无效等价类: 课程 ID 列表类型错误"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/',
            'download_course_ids': 'not a list'
        }
        result = self.validator.validate_config_data(config)
        self.assertFalse(result.is_valid)

    # ========== 边界值测试 ==========

    def test_boundary_empty_domain(self):
        """边界值: 空域名"""
        config = {
            'moodle_domain': '',
            'moodle_path': '/'
        }
        result = self.validator.validate_config_data(config)
        # 空域名应该产生警告
        self.assertTrue(result.has_warnings())

    def test_boundary_single_char_domain(self):
        """边界值: 单字符域名"""
        config = {
            'moodle_domain': 'a',
            'moodle_path': '/'
        }
        result = self.validator.validate_config_data(config)
        self.assertTrue(result.is_valid)

    # ========== 特殊字符和 Unicode 测试 ==========

    def test_unicode_domain_idn(self):
        """Unicode: 国际化域名"""
        config = {
            'moodle_domain': 'moodle.大学.cn',
            'moodle_path': '/'
        }
        result = self.validator.validate_config_data(config)
        self.assertTrue(result is not None)

    def test_unicode_path_with_unicode(self):
        """Unicode: 包含 Unicode 的路径"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/course/2024'
        }
        result = self.validator.validate_config_data(config)
        self.assertTrue(result.is_valid)

    # ========== 文件系统测试 ==========

    def test_file_valid_config_file(self):
        """文件系统: 有效的配置文件"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/'
        }
        config_path = os.path.join(self.temp_dir, 'config.json')
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f)
        result = self.validator.validate_config_file(config_path)
        self.assertTrue(result.is_valid)

    def test_file_non_existent_file(self):
        """文件系统: 不存在的文件"""
        result = self.validator.validate_config_file('/non/existent/path/config.json')
        self.assertFalse(result.is_valid)
        self.assertTrue(result.has_errors())

    def test_file_invalid_json_syntax(self):
        """文件系统: 无效的 JSON 语法"""
        config_path = os.path.join(self.temp_dir, 'config.json')
        with open(config_path, 'w') as f:
            f.write('{"invalid": json}')
        result = self.validator.validate_config_file(config_path)
        self.assertFalse(result.is_valid)


if __name__ == "__main__":
    unittest.main()
