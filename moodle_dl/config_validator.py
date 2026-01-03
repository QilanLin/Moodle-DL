# -*- coding: utf-8 -*-
"""
配置验证框架

提供全面的配置文件验证功能，确保配置的完整性、类型正确性和逻辑合理性。

验证层次：
1. 结构验证 - JSON 格式、必需字段存在性
2. 类型验证 - 数据类型正确性
3. 范围验证 - 数值范围、字符串格式
4. 逻辑验证 - 字段间的依赖关系和冲突检查
5. 安全验证 - 路径安全性、权限检查

Author: Claude (AI Assistant)
Date: 2025-11-20
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


@dataclass
class ValidationError:
    """单个验证错误"""
    field: str
    message: str
    severity: str  # 'error', 'warning', 'info'
    suggestion: Optional[str] = None


@dataclass
class ValidationResult:
    """验证结果"""
    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)
    
    def add_error(self, field: str, message: str, suggestion: Optional[str] = None):
        """添加错误"""
        self.errors.append(ValidationError(field, message, 'error', suggestion))
        self.is_valid = False
    
    def add_warning(self, field: str, message: str, suggestion: Optional[str] = None):
        """添加警告"""
        self.warnings.append(ValidationError(field, message, 'warning', suggestion))
    
    def has_errors(self) -> bool:
        """是否有错误"""
        return len(self.errors) > 0
    
    def has_warnings(self) -> bool:
        """是否有警告"""
        return len(self.warnings) > 0
    
    def get_summary(self) -> str:
        """获取验证摘要"""
        lines = []
        if self.has_errors():
            lines.append(f"❌ 发现 {len(self.errors)} 个错误:")
            for err in self.errors:
                lines.append(f"  • {err.field}: {err.message}")
                if err.suggestion:
                    lines.append(f"    💡 建议: {err.suggestion}")
        
        if self.has_warnings():
            lines.append(f"⚠️  发现 {len(self.warnings)} 个警告:")
            for warn in self.warnings:
                lines.append(f"  • {warn.field}: {warn.message}")
                if warn.suggestion:
                    lines.append(f"    💡 建议: {warn.suggestion}")
        
        if not self.has_errors() and not self.has_warnings():
            lines.append("✅ 配置验证通过，未发现问题")
        
        return "\n".join(lines)


class ConfigValidator:
    """
    配置验证器
    
    提供多层次的配置验证功能
    """
    
    # 必需的顶级字段
    REQUIRED_FIELDS = [
        'moodle_domain',
        'moodle_path'
    ]
    
    # 可选的顶级字段及其默认值
    OPTIONAL_FIELDS = {
        'download_options': {},
        'courses_to_filter': [],
        'token': '',
        'privatetoken': '',
        'download_course_ids': [],
        'download_public_course_ids': [],
        'dont_download_course_ids': [],
        'restricted_filenames': False,
        'include_noncourse_files': False,
        'notifications': {},
        # Cookie 和浏览器相关
        'preferred_browser': '',
        'download_also_with_cookie': False,
        'download_linked_files': False,
        # 手动指定的课程
        'manually_specified_course_ids': [],
        # 扁平化的下载选项会从 DOWNLOAD_OPTIONS_FIELDS 自动生成（见下方）
    }
    
    # 下载选项字段（单一来源，遵循 DRY 原则）
    # 从 DownloadOptionsConfig 的字段自动获取
    @staticmethod
    def _get_download_options_fields():
        """从 DownloadOptionsConfig 获取所有下载选项字段名"""
        from dataclasses import fields
        from moodle_dl.config import DownloadOptionsConfig
        return [f.name for f in fields(DownloadOptionsConfig)]
    
    # 延迟初始化，避免循环导入
    _download_options_fields_cache = None
    
    @classmethod
    def get_download_options_fields(cls):
        """获取下载选项字段列表（带缓存）"""
        if cls._download_options_fields_cache is None:
            cls._download_options_fields_cache = cls._get_download_options_fields()
        return cls._download_options_fields_cache
    
    @classmethod
    def get_known_config_fields(cls):
        """获取完整的已知配置字段字典（包括动态生成的 download_xxx 字段）"""
        # 复制基础字段（REQUIRED + OPTIONAL）
        fields = {field: None for field in cls.REQUIRED_FIELDS}
        fields.update(cls.OPTIONAL_FIELDS.copy())
        # 从 DownloadOptionsConfig 自动生成 download_xxx 字段
        for field in cls.get_download_options_fields():
            fields[f'download_{field}'] = None
        return fields
    
    def __init__(self, strict: bool = False):
        """
        初始化配置验证器
        
        Args:
            strict: 严格模式（警告也会导致验证失败）
        """
        self.strict = strict
    
    def validate_config_file(self, config_path: str) -> ValidationResult:
        """
        验证配置文件
        
        Args:
            config_path: 配置文件路径
        
        Returns:
            ValidationResult: 验证结果
        """
        result = ValidationResult(is_valid=True)
        
        # 1. 文件存在性检查
        if not os.path.exists(config_path):
            result.add_error('config_file', f'配置文件不存在: {config_path}')
            return result
        
        if not os.path.isfile(config_path):
            result.add_error('config_file', f'配置路径不是文件: {config_path}')
            return result
        
        # 2. 文件可读性检查
        if not os.access(config_path, os.R_OK):
            result.add_error('config_file', f'配置文件无法读取: {config_path}',
                           suggestion='检查文件权限')
            return result
        
        # 3. JSON 格式检查
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
        except json.JSONDecodeError as e:
            result.add_error('json_format', f'JSON 格式错误: {e}',
                           suggestion='使用 JSON 验证工具检查语法')
            return result
        except Exception as e:
            result.add_error('file_read', f'读取文件失败: {e}')
            return result
        
        # 4. 配置内容验证
        self._validate_config_data(config_data, result)
        
        return result
    
    def validate_config_data(self, config_data: Dict[str, Any]) -> ValidationResult:
        """
        验证配置数据（已加载的字典）
        
        Args:
            config_data: 配置数据字典
        
        Returns:
            ValidationResult: 验证结果
        """
        result = ValidationResult(is_valid=True)
        self._validate_config_data(config_data, result)
        return result
    
    def _validate_config_data(self, config: Dict[str, Any], result: ValidationResult):
        """内部验证逻辑"""
        # 层次 1: 结构验证
        self._validate_structure(config, result)
        
        # 层次 2: 类型验证
        self._validate_types(config, result)
        
        # 层次 3: 范围验证
        self._validate_ranges(config, result)
        
        # 层次 4: 逻辑验证
        self._validate_logic(config, result)
        
        # 层次 5: 安全验证
        self._validate_security(config, result)
    
    def _validate_structure(self, config: Dict[str, Any], result: ValidationResult):
        """验证配置结构"""
        # 检查必需字段
        for field in self.REQUIRED_FIELDS:
            if field not in config:
                result.add_error(field, f'缺少必需字段: {field}',
                               suggestion=f'添加字段 "{field}" 到配置文件')
            elif not config[field]:
                result.add_warning(field, f'必需字段为空: {field}')
        
        # 检查未知字段（可能是拼写错误）
        # 使用 get_known_config_fields() 获取完整的字段列表（包括自动生成的 download_xxx）
        known_fields = set(self.get_known_config_fields().keys())
        for field in config.keys():
            if field not in known_fields:
                result.add_warning(field, f'未知的配置字段: {field}',
                                 suggestion='检查是否拼写错误')
    
    def _validate_types(self, config: Dict[str, Any], result: ValidationResult):
        """验证字段类型"""
        # Moodle 域名和路径必须是字符串
        if 'moodle_domain' in config and not isinstance(config['moodle_domain'], str):
            result.add_error('moodle_domain', '必须是字符串类型')
        
        if 'moodle_path' in config and not isinstance(config['moodle_path'], str):
            result.add_error('moodle_path', '必须是字符串类型')
        
        # Token 必须是字符串
        if 'token' in config and config['token'] is not None and not isinstance(config['token'], str):
            result.add_error('token', 'Token 必须是字符串类型')
        
        if 'privatetoken' in config and config['privatetoken'] is not None and not isinstance(config['privatetoken'], str):
            result.add_error('privatetoken', 'Private token 必须是字符串类型')
        
        # 课程过滤器必须是列表
        list_fields = ['courses_to_filter', 'download_course_ids', 
                      'download_public_course_ids', 'dont_download_course_ids']
        for field in list_fields:
            if field in config and not isinstance(config[field], list):
                result.add_error(field, f'{field} 必须是列表类型',
                               suggestion=f'使用 [] 包裹值，例如 [1, 2, 3]')
        
        # 限制文件名开关必须是布尔类型
        if 'restricted_filenames' in config and not isinstance(config['restricted_filenames'], bool):
            result.add_error('restricted_filenames', 'restricted_filenames 必须是布尔类型 (true/false)')
        
        # 布尔字段
        if 'include_noncourse_files' in config and not isinstance(config['include_noncourse_files'], bool):
            result.add_error('include_noncourse_files', '必须是布尔类型 (true/false)')
        
        # 下载选项必须是字典
        if 'download_options' in config:
            if not isinstance(config['download_options'], dict):
                result.add_error('download_options', '必须是对象类型 {}')
            else:
                # 检查每个下载选项的类型
                for opt_name, opt_value in config['download_options'].items():
                    if opt_name in self.get_download_options_fields():
                        if not isinstance(opt_value, bool):
                            result.add_error(f'download_options.{opt_name}',
                                           f'必须是布尔类型 (true/false)，当前是 {type(opt_value).__name__}')
                    else:
                        result.add_warning(f'download_options.{opt_name}',
                                         f'未知的下载选项: {opt_name}')
        
        # 通知配置必须是字典
        if 'notifications' in config and not isinstance(config['notifications'], dict):
            result.add_error('notifications', '必须是对象类型 {}')
    
    def _validate_ranges(self, config: Dict[str, Any], result: ValidationResult):
        """验证值的范围和格式"""
        # Moodle 域名格式检查
        if 'moodle_domain' in config and config['moodle_domain']:
            domain = config['moodle_domain']
            
            # 确保 domain 是字符串（类型检查已经在 _validate_types 中完成，这里是防御性编程）
            if not isinstance(domain, str):
                # 跳过格式检查，因为类型错误已经在 _validate_types 中报告
                return
            
            # 简单的域名格式检查
            if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?$', domain):
                result.add_warning('moodle_domain', 
                                 f'域名格式可能不正确: {domain}',
                                 suggestion='域名应该类似 "moodle.example.com"')
            
            # 检查是否包含协议（不应该包含）
            if domain.startswith('http://') or domain.startswith('https://'):
                result.add_error('moodle_domain',
                               '域名不应包含协议 (http:// 或 https://)',
                               suggestion=f'使用 "{domain.replace("https://", "").replace("http://", "")}"')
        
        # Moodle 路径格式检查
        if 'moodle_path' in config and config['moodle_path']:
            path = config['moodle_path']
            if not path.startswith('/'):
                result.add_warning('moodle_path',
                                 '路径通常应以 / 开头',
                                 suggestion=f'考虑使用 "/{path}"')
        
        # 课程 ID 必须是正整数
        id_list_fields = ['download_course_ids', 'download_public_course_ids', 
                         'dont_download_course_ids']
        for field in id_list_fields:
            if field in config and config[field]:
                for i, course_id in enumerate(config[field]):
                    if not isinstance(course_id, int) or course_id <= 0:
                        result.add_error(f'{field}[{i}]',
                                       f'课程 ID 必须是正整数，当前是: {course_id}')
        
        # Token 长度检查（Moodle token 通常是 32 个字符）
        if 'token' in config and config['token']:
            token = config['token']
            if len(token) < 20:
                result.add_warning('token', 
                                 f'Token 长度似乎太短 (当前: {len(token)} 字符)',
                                 suggestion='Moodle token 通常是 32 个字符')
            if not re.match(r'^[a-f0-9]+$', token):
                result.add_warning('token',
                                 'Token 格式可能不正确（通常是十六进制字符串）')
        
        if 'privatetoken' in config and config['privatetoken']:
            token = config['privatetoken']
            if len(token) < 20:
                result.add_warning('privatetoken',
                                 f'Private token 长度似乎太短 (当前: {len(token)} 字符)')
    
    def _validate_logic(self, config: Dict[str, Any], result: ValidationResult):
        """验证逻辑关系"""
        # 检查课程过滤逻辑冲突
        download_ids = set(config.get('download_course_ids', []))
        dont_download_ids = set(config.get('dont_download_course_ids', []))
        
        # 同一课程不能既在下载列表又在排除列表
        conflict_ids = download_ids & dont_download_ids
        if conflict_ids:
            result.add_error('course_filters',
                           f'课程 ID 冲突：以下课程同时在下载和排除列表中: {conflict_ids}',
                           suggestion='从其中一个列表中移除这些课程 ID')
        
        # 检查 token 和认证相关逻辑
        has_token = config.get('token') or config.get('privatetoken')
        if not has_token:
            result.add_warning('authentication',
                             '未配置 token，可能需要运行 --init 进行身份验证',
                             suggestion='运行 "moodle-dl --init" 或 "moodle-dl --init --sso"')
        
        # 检查下载选项的逻辑性
        if 'download_options' in config:
            opts = config['download_options']
            
            # 如果禁用了 descriptions，那么 links_in_descriptions 也没有意义
            if opts.get('descriptions') is False and opts.get('links_in_descriptions') is True:
                result.add_warning('download_options',
                                 'descriptions 为 false 时，links_in_descriptions 不会生效',
                                 suggestion='考虑将 links_in_descriptions 也设为 false')
            
            # 如果所有选项都是 false，警告用户
            if all(not opts.get(field, False) for field in self.get_download_options_fields()):
                result.add_warning('download_options',
                                 '所有下载选项都是 false，可能不会下载任何文件',
                                 suggestion='至少启用一些下载选项')
        
        # 检查通知配置的完整性
        if 'notifications' in config and config['notifications']:
            notif = config['notifications']
            
            # 邮件通知需要必要的字段
            if notif.get('mail', {}).get('enabled'):
                mail_config = notif['mail']
                required_mail_fields = ['server', 'sender', 'receiver']
                for field in required_mail_fields:
                    if not mail_config.get(field):
                        result.add_error(f'notifications.mail.{field}',
                                       f'邮件通知已启用，但缺少必需字段: {field}')
            
            # Telegram 通知需要 token 和 chat_id
            if notif.get('telegram', {}).get('enabled'):
                tg_config = notif['telegram']
                if not tg_config.get('token'):
                    result.add_error('notifications.telegram.token',
                                   'Telegram 通知已启用，但缺少 token')
                if not tg_config.get('chat_id'):
                    result.add_error('notifications.telegram.chat_id',
                                   'Telegram 通知已启用，但缺少 chat_id')
            
            # XMPP 通知需要 JID 和密码
            if notif.get('xmpp', {}).get('enabled'):
                xmpp_config = notif['xmpp']
                if not xmpp_config.get('sender'):
                    result.add_error('notifications.xmpp.sender',
                                   'XMPP 通知已启用，但缺少发送者 JID')
                if not xmpp_config.get('password'):
                    result.add_error('notifications.xmpp.password',
                                   'XMPP 通知已启用，但缺少密码')
                if not xmpp_config.get('receiver'):
                    result.add_error('notifications.xmpp.receiver',
                                   'XMPP 通知已启用，但缺少接收者 JID')
    
    def _validate_security(self, config: Dict[str, Any], result: ValidationResult):
        """验证安全性"""
        # 警告：Token 不应该包含明显的占位符
        placeholder_patterns = ['xxx', 'replace', 'your', 'token', 'here', 'example']
        if 'token' in config and config['token']:
            token_lower = config['token'].lower()
            for pattern in placeholder_patterns:
                if pattern in token_lower:
                    result.add_warning('token',
                                     f'Token 似乎包含占位符文本: "{pattern}"',
                                     suggestion='确保使用真实的 Moodle token')
                    break
        
        # 检查通知中的敏感信息
        if 'notifications' in config:
            notif = config['notifications']
            
            # 邮件密码不应该是明显的弱密码
            if notif.get('mail', {}).get('password'):
                password = notif['mail']['password']
                weak_passwords = ['password', '123456', 'admin', 'test']
                if password.lower() in weak_passwords:
                    result.add_warning('notifications.mail.password',
                                     '邮件密码似乎太弱或是默认密码',
                                     suggestion='使用强密码')


def validate_config_file(config_path: str, strict: bool = False) -> ValidationResult:
    """
    便捷函数：验证配置文件
    
    Args:
        config_path: 配置文件路径
        strict: 严格模式
    
    Returns:
        ValidationResult: 验证结果
    """
    validator = ConfigValidator(strict=strict)
    return validator.validate_config_file(config_path)


def validate_config_data(config_data: Dict[str, Any], strict: bool = False) -> ValidationResult:
    """
    便捷函数：验证配置数据
    
    Args:
        config_data: 配置数据字典
        strict: 严格模式
    
    Returns:
        ValidationResult: 验证结果
    """
    validator = ConfigValidator(strict=strict)
    return validator.validate_config_data(config_data)


def auto_fix_config(config: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    自动修复常见的配置问题
    
    Args:
        config: 配置数据
    
    Returns:
        (修复后的配置, 修复说明列表)
    """
    fixes = []
    fixed_config = config.copy()
    
    # 修复 1: 移除域名中的协议
    if 'moodle_domain' in fixed_config and fixed_config['moodle_domain']:
        domain = fixed_config['moodle_domain']
        if domain.startswith('http://') or domain.startswith('https://'):
            new_domain = domain.replace('https://', '').replace('http://', '')
            fixed_config['moodle_domain'] = new_domain
            fixes.append(f'移除域名中的协议: {domain} -> {new_domain}')
    
    # 修复 2: 确保路径以 / 开头
    if 'moodle_path' in fixed_config and fixed_config['moodle_path']:
        path = fixed_config['moodle_path']
        if not path.startswith('/'):
            new_path = '/' + path
            fixed_config['moodle_path'] = new_path
            fixes.append(f'路径添加前导斜杠: {path} -> {new_path}')
    
    # 修复 3: 移除课程过滤冲突（但首先确保它们是列表）
    if 'download_course_ids' in fixed_config:
        if not isinstance(fixed_config['download_course_ids'], list):
            try:
                fixed_config['download_course_ids'] = [fixed_config['download_course_ids']] if fixed_config['download_course_ids'] else []
                fixes.append('将 download_course_ids 转换为列表')
            except:
                fixed_config['download_course_ids'] = []
                fixes.append('将 download_course_ids 重置为空列表')
    
    if 'dont_download_course_ids' in fixed_config:
        if not isinstance(fixed_config['dont_download_course_ids'], list):
            try:
                fixed_config['dont_download_course_ids'] = [fixed_config['dont_download_course_ids']] if fixed_config['dont_download_course_ids'] else []
                fixes.append('将 dont_download_course_ids 转换为列表')
            except:
                fixed_config['dont_download_course_ids'] = []
                fixes.append('将 dont_download_course_ids 重置为空列表')
    
    # 现在可以安全地处理课程 ID 冲突
    download_ids = set(fixed_config.get('download_course_ids', []))
    dont_download_ids = set(fixed_config.get('dont_download_course_ids', []))
    conflict_ids = download_ids & dont_download_ids
    if conflict_ids:
        # 从 dont_download_course_ids 中移除冲突的 ID
        fixed_config['dont_download_course_ids'] = [
            id for id in fixed_config['dont_download_course_ids']
            if id not in conflict_ids
        ]
        fixes.append(f'从排除列表中移除冲突的课程 ID: {conflict_ids}')
    
    # 修复 4: 如果 descriptions 为 false，也将 links_in_descriptions 设为 false
    if 'download_options' in fixed_config:
        opts = fixed_config['download_options']
        if opts.get('descriptions') is False and opts.get('links_in_descriptions') is True:
            opts['links_in_descriptions'] = False
            fixes.append('descriptions 为 false 时，自动禁用 links_in_descriptions')
    
    # 修复 5: 确保列表字段确实是列表
    list_fields = ['courses_to_filter', 'download_course_ids', 
                  'download_public_course_ids', 'dont_download_course_ids']
    for field in list_fields:
        if field in fixed_config and not isinstance(fixed_config[field], list):
            # 尝试转换为列表
            try:
                fixed_config[field] = [fixed_config[field]] if fixed_config[field] else []
                fixes.append(f'将 {field} 转换为列表')
            except:
                fixed_config[field] = []
                fixes.append(f'将 {field} 重置为空列表（无法转换）')
    
    # 修复 6: 将 restricted_filenames 统一为布尔值
    if 'restricted_filenames' in fixed_config and not isinstance(fixed_config['restricted_filenames'], bool):
        fixed_config['restricted_filenames'] = bool(fixed_config['restricted_filenames'])
        fixes.append('将 restricted_filenames 转换为布尔值')

    return fixed_config, fixes
