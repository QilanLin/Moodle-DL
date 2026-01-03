# -*- coding: utf-8 -*-
"""
配置验证框架的单元测试

测试覆盖：
1. 文件存在性检查
2. JSON 格式验证
3. 结构验证（必需字段）
4. 类型验证
5. 范围验证
6. 逻辑验证
7. 安全验证
8. 自动修复功能

Author: Claude (AI Assistant)
Date: 2025-11-20
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from moodle_dl.config_validator import (
    ConfigValidator,
    ValidationResult,
    auto_fix_config,
    validate_config_data,
    validate_config_file,
)


class TestValidationResult(unittest.TestCase):
    """测试 ValidationResult 类"""
    
    def test_empty_result(self):
        """测试空的验证结果"""
        result = ValidationResult(is_valid=True)
        self.assertTrue(result.is_valid)
        self.assertFalse(result.has_errors())
        self.assertFalse(result.has_warnings())
    
    def test_add_error(self):
        """测试添加错误"""
        result = ValidationResult(is_valid=True)
        result.add_error('test_field', 'Test error message')
        
        self.assertFalse(result.is_valid)
        self.assertTrue(result.has_errors())
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].field, 'test_field')
        self.assertEqual(result.errors[0].message, 'Test error message')
    
    def test_add_warning(self):
        """测试添加警告"""
        result = ValidationResult(is_valid=True)
        result.add_warning('test_field', 'Test warning message', 'Test suggestion')
        
        self.assertTrue(result.is_valid)  # 警告不影响 is_valid
        self.assertTrue(result.has_warnings())
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(result.warnings[0].suggestion, 'Test suggestion')
    
    def test_get_summary(self):
        """测试获取摘要"""
        result = ValidationResult(is_valid=True)
        
        # 空结果
        summary = result.get_summary()
        self.assertIn('✅', summary)
        
        # 添加错误和警告
        result.add_error('field1', 'Error 1')
        result.add_warning('field2', 'Warning 1', 'Fix this')
        
        summary = result.get_summary()
        self.assertIn('❌', summary)
        self.assertIn('⚠️', summary)
        self.assertIn('field1', summary)
        self.assertIn('field2', summary)
        self.assertIn('Fix this', summary)


class TestConfigValidator(unittest.TestCase):
    """测试 ConfigValidator 类"""
    
    def setUp(self):
        """测试前准备"""
        self.validator = ConfigValidator(strict=False)
        self.temp_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """测试后清理"""
        # 清理临时文件
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def create_temp_config(self, config_data: dict) -> str:
        """创建临时配置文件"""
        config_path = os.path.join(self.temp_dir, 'test_config.json')
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4)
        return config_path
    
    def test_valid_minimal_config(self):
        """测试最小有效配置"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle'
        }
        
        result = self.validator.validate_config_data(config)
        self.assertTrue(result.is_valid)
    
    def test_missing_required_field(self):
        """测试缺少必需字段"""
        config = {
            'moodle_domain': 'moodle.example.com'
            # 缺少 moodle_path
        }
        
        result = self.validator.validate_config_data(config)
        self.assertFalse(result.is_valid)
        self.assertTrue(result.has_errors())
        self.assertTrue(any('moodle_path' in err.field for err in result.errors))
    
    def test_invalid_type_moodle_domain(self):
        """测试 moodle_domain 类型错误"""
        config = {
            'moodle_domain': 12345,  # 应该是字符串
            'moodle_path': '/moodle'
        }
        
        result = self.validator.validate_config_data(config)
        self.assertFalse(result.is_valid)
        self.assertTrue(any('moodle_domain' in err.field for err in result.errors))
    
    def test_domain_with_protocol(self):
        """测试域名包含协议（应报错）"""
        config = {
            'moodle_domain': 'https://moodle.example.com',
            'moodle_path': '/moodle'
        }
        
        result = self.validator.validate_config_data(config)
        self.assertFalse(result.is_valid)
        self.assertTrue(any('moodle_domain' in err.field for err in result.errors))
        self.assertTrue(any('协议' in err.message for err in result.errors))
    
    def test_path_without_leading_slash(self):
        """测试路径缺少前导斜杠（警告）"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': 'moodle'  # 缺少前导 /
        }
        
        result = self.validator.validate_config_data(config)
        self.assertTrue(result.is_valid)  # 只是警告，不是错误
        self.assertTrue(result.has_warnings())
        self.assertTrue(any('moodle_path' in warn.field for warn in result.warnings))
    
    def test_invalid_course_id_type(self):
        """测试课程 ID 类型错误"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle',
            'download_course_ids': [1, 2, 'three']  # 应该是整数
        }
        
        result = self.validator.validate_config_data(config)
        self.assertFalse(result.is_valid)
        self.assertTrue(any('download_course_ids' in err.field for err in result.errors))
    
    def test_conflicting_course_ids(self):
        """测试课程 ID 冲突"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle',
            'download_course_ids': [1, 2, 3],
            'dont_download_course_ids': [2, 3, 4]  # 2 和 3 冲突
        }
        
        result = self.validator.validate_config_data(config)
        self.assertFalse(result.is_valid)
        self.assertTrue(any('course_filters' in err.field for err in result.errors))
        self.assertTrue(any('冲突' in err.message for err in result.errors))
    
    def test_invalid_download_option_type(self):
        """测试下载选项类型错误"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle',
            'download_options': {
                'submissions': 'yes'  # 应该是布尔值
            }
        }
        
        result = self.validator.validate_config_data(config)
        self.assertFalse(result.is_valid)
        self.assertTrue(any('download_options.submissions' in err.field for err in result.errors))
    
    def test_unknown_download_option(self):
        """测试未知的下载选项"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle',
            'download_options': {
                'unknown_option': True
            }
        }
        
        result = self.validator.validate_config_data(config)
        self.assertTrue(result.is_valid)  # 只是警告
        self.assertTrue(result.has_warnings())
        self.assertTrue(any('unknown_option' in warn.field for warn in result.warnings))
    
    def test_descriptions_logic(self):
        """测试 descriptions 和 links_in_descriptions 的逻辑关系"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle',
            'download_options': {
                'descriptions': False,
                'links_in_descriptions': True  # 不一致
            }
        }
        
        result = self.validator.validate_config_data(config)
        self.assertTrue(result.is_valid)  # 只是警告
        self.assertTrue(result.has_warnings())
        self.assertTrue(any('download_options' in warn.field for warn in result.warnings))
    
    def test_mail_notification_incomplete(self):
        """测试邮件通知配置不完整"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle',
            'notifications': {
                'mail': {
                    'enabled': True
                    # 缺少 server, sender, receiver
                }
            }
        }
        
        result = self.validator.validate_config_data(config)
        self.assertFalse(result.is_valid)
        # 应该有 3 个错误（缺少 server, sender, receiver）
        mail_errors = [err for err in result.errors if 'notifications.mail' in err.field]
        self.assertGreaterEqual(len(mail_errors), 3)
    
    def test_telegram_notification_incomplete(self):
        """测试 Telegram 通知配置不完整"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle',
            'notifications': {
                'telegram': {
                    'enabled': True
                    # 缺少 token 和 chat_id
                }
            }
        }
        
        result = self.validator.validate_config_data(config)
        self.assertFalse(result.is_valid)
        tg_errors = [err for err in result.errors if 'telegram' in err.field]
        self.assertGreaterEqual(len(tg_errors), 2)
    
    def test_token_placeholder(self):
        """测试 token 包含占位符"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle',
            'token': 'your_token_here'
        }
        
        result = self.validator.validate_config_data(config)
        self.assertTrue(result.is_valid)  # 只是警告
        self.assertTrue(result.has_warnings())
        self.assertTrue(any('token' in warn.field for warn in result.warnings))
    
    def test_short_token(self):
        """测试 token 太短"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle',
            'token': '123'  # 太短
        }
        
        result = self.validator.validate_config_data(config)
        self.assertTrue(result.is_valid)  # 只是警告
        self.assertTrue(result.has_warnings())
        self.assertTrue(any('太短' in warn.message for warn in result.warnings))
    
    def test_restricted_filenames_type_error(self):
        """restricted_filenames 不是布尔值时应报错"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle',
            'restricted_filenames': ['../etc/passwd']
        }
        
        result = self.validator.validate_config_data(config)
        self.assertFalse(result.is_valid)
        self.assertTrue(any('布尔' in err.message for err in result.errors))
    
    def test_file_validation_nonexistent(self):
        """测试验证不存在的文件"""
        result = self.validator.validate_config_file('/nonexistent/config.json')
        self.assertFalse(result.is_valid)
        self.assertTrue(any('不存在' in err.message for err in result.errors))
    
    def test_file_validation_invalid_json(self):
        """测试验证无效的 JSON 文件"""
        config_path = os.path.join(self.temp_dir, 'invalid.json')
        with open(config_path, 'w') as f:
            f.write('{ invalid json }')
        
        result = self.validator.validate_config_file(config_path)
        self.assertFalse(result.is_valid)
        self.assertTrue(any('JSON' in err.message for err in result.errors))
    
    def test_file_validation_valid(self):
        """测试验证有效的配置文件"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle'
        }
        config_path = self.create_temp_config(config)
        
        result = self.validator.validate_config_file(config_path)
        self.assertTrue(result.is_valid)


class TestAutoFix(unittest.TestCase):
    """测试自动修复功能"""
    
    def test_fix_domain_protocol(self):
        """测试修复域名中的协议"""
        config = {
            'moodle_domain': 'https://moodle.example.com',
            'moodle_path': '/moodle'
        }
        
        fixed_config, fixes = auto_fix_config(config)
        
        self.assertEqual(fixed_config['moodle_domain'], 'moodle.example.com')
        self.assertTrue(len(fixes) > 0)
        self.assertTrue(any('协议' in fix for fix in fixes))
    
    def test_fix_path_leading_slash(self):
        """测试修复路径前导斜杠"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': 'moodle'
        }
        
        fixed_config, fixes = auto_fix_config(config)
        
        self.assertEqual(fixed_config['moodle_path'], '/moodle')
        self.assertTrue(any('斜杠' in fix for fix in fixes))
    
    def test_fix_course_id_conflict(self):
        """测试修复课程 ID 冲突"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle',
            'download_course_ids': [1, 2, 3],
            'dont_download_course_ids': [2, 3, 4]
        }
        
        fixed_config, fixes = auto_fix_config(config)
        
        download_ids = set(fixed_config['download_course_ids'])
        dont_download_ids = set(fixed_config['dont_download_course_ids'])
        
        # 应该没有冲突了
        self.assertEqual(len(download_ids & dont_download_ids), 0)
        self.assertTrue(any('冲突' in fix for fix in fixes))
    
    def test_fix_descriptions_logic(self):
        """测试修复 descriptions 逻辑"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle',
            'download_options': {
                'descriptions': False,
                'links_in_descriptions': True
            }
        }
        
        fixed_config, fixes = auto_fix_config(config)
        
        self.assertFalse(fixed_config['download_options']['links_in_descriptions'])
        self.assertTrue(any('links_in_descriptions' in fix for fix in fixes))
    
    def test_fix_non_list_field(self):
        """测试修复非列表字段"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle',
            'download_course_ids': 123  # 应该是列表
        }
        
        fixed_config, fixes = auto_fix_config(config)
        
        self.assertIsInstance(fixed_config['download_course_ids'], list)
        self.assertEqual(fixed_config['download_course_ids'], [123])
        self.assertTrue(any('列表' in fix for fix in fixes))
    
    def test_no_fixes_needed(self):
        """测试不需要修复的配置"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle'
        }
        
        fixed_config, fixes = auto_fix_config(config)
        
        self.assertEqual(config, fixed_config)
        self.assertEqual(len(fixes), 0)


class TestConvenienceFunctions(unittest.TestCase):
    """测试便捷函数"""
    
    def test_validate_config_data_function(self):
        """测试 validate_config_data 函数"""
        config = {
            'moodle_domain': 'moodle.example.com',
            'moodle_path': '/moodle'
        }
        
        result = validate_config_data(config)
        self.assertTrue(result.is_valid)
    
    def test_validate_config_file_function(self):
        """测试 validate_config_file 函数"""
        result = validate_config_file('/nonexistent.json')
        self.assertFalse(result.is_valid)


if __name__ == '__main__':
    unittest.main()
