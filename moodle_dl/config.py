# -*- coding: utf-8 -*-
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from moodle_dl.types import DownloadOptions, MoodleDlOpts, MoodleURL
from moodle_dl.utils import PathTools as PT


def normalize_moodle_url(url: str) -> str:
    """
    规范化 Moodle URL，自动添加 https:// 前缀（如果缺少）。
    
    支持以下输入:
    • keats.kcl.ac.uk → https://keats.kcl.ac.uk
    • https://keats.kcl.ac.uk → https://keats.kcl.ac.uk
    • http://keats.kcl.ac.uk → http://keats.kcl.ac.uk
    
    Args:
        url: 输入的 URL（可能缺少协议）
        
    Returns:
        完整的 URL（包含协议）
    """
    url = url.strip()
    
    # 如果已经有协议，直接返回
    if url.startswith('http://') or url.startswith('https://'):
        return url
    
    # 否则添加 https:// 前缀
    return f'https://{url}'


@dataclass
class DownloadOptionsConfig:
    """
    配置下载选项的 dataclass，提供类型安全和默认值
    
    所有字段默认为 False，除了常用的选项（如 descriptions, resources 等）
    """
    submissions: bool = False
    descriptions: bool = True
    links_in_descriptions: bool = True
    databases: bool = False
    forums: bool = False
    quizzes: bool = False
    lessons: bool = False
    workshops: bool = False
    books: bool = True
    bigbluebuttonbns: bool = False
    wikis: bool = False
    glossaries: bool = False
    h5pactivities: bool = False
    h5p_attempts: bool = False
    imscps: bool = False
    scorms: bool = False
    scorm_scos: bool = False
    scorm_attempts: bool = False
    subsections: bool = True
    qbanks: bool = False
    resources: bool = True
    urls: bool = False
    labels: bool = False
    chats: bool = False
    choices: bool = False
    feedbacks: bool = False
    surveys: bool = False
    ltis: bool = False
    calendars: bool = False
    metadata_files: bool = False
    
    @classmethod
    def from_dict(cls, data: Dict[str, bool]) -> 'DownloadOptionsConfig':
        """从字典创建配置对象"""
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})
    
    def to_dict(self) -> Dict[str, bool]:
        """转换为字典"""
        from dataclasses import asdict
        return asdict(self)


class ConfigHelper:
    """
    Handles the saving, formatting and loading of the local configuration.
    
    🔧 CONFIGURATION VALIDATION FRAMEWORK:
    
    Validation Strategy:
    1. SCHEMA VALIDATION:
       - Type checking for each configuration parameter
       - Range validation (e.g., 0 < parallel_downloads <= 10)
       - Enum validation for predefined options
    
    2. DEPENDENCY VALIDATION:
       - Cross-field validation (e.g., if whitelist enabled, blacklist must be empty)
       - Moodle URL format and accessibility checks
       - Token format validation
    
    3. ENCODING SUPPORT (Smart Encoding Fallback):
       - Auto-detect system encoding
       - Fallback chain: UTF-8 → ANSI → system default
       - Proper handling of non-ASCII characters in paths
       - File path normalization across platforms
    
    4. CONFIGURATION SOURCES (Priority Order):
       1. Command-line arguments (highest priority)
       2. config.json (local configuration)
       3. Environment variables (system-level)
       4. Defaults (built-in values, lowest priority)
    
    Module-Specific Features:
    - download_books: Enable Moodle Book module
    - download_also_with_cookie: Use browser cookie fallback
    - skip_cert_verify: SSL verification control
    - save_to: Download destination path
    
    Database Version: 9 with session and cookie management
    """

    class NoConfigError(ValueError):
        """An Exception which gets thrown if config could not be loaded."""

        pass

    @staticmethod
    def normalize_id_list(ids: List[Any]) -> List[int]:
        """
        将 ID 列表标准化为 int 列表。
        
        通用的类型转换函数，可用于任何类型的 ID 列表（课程 ID、章节 ID 等）。
        只负责类型转换，确保返回的始终是 int 列表。
        处理字符串、混合类型、None 值等情况。
        
        防御性编程：
        - 如果输入为 None，返回空列表
        - 如果输入不是列表，尝试转换为列表
        - 过滤 None 值
        - 跳过无法转换为 int 的值（记录警告）
        
        Args:
            ids: 原始 ID 列表（可能是 int、str 或混合类型），也可以是 None
            
        Returns:
            List[int]: 标准化后的 int 列表（None 值和无效值被过滤）
            
        Example:
            >>> ConfigHelper.normalize_id_list([1, "2", None, 3])
            [1, 2, 3]
            >>> ConfigHelper.normalize_id_list(None)
            []
            >>> ConfigHelper.normalize_id_list(["1", "abc", "2"])
            [1, 2]  # 'abc' 被跳过
        """
        # 防御性编程：处理 None 输入
        if ids is None:
            return []
        
        # 防御性编程：确保输入是列表
        if not isinstance(ids, list):
            logging.warning(f'normalize_id_list 收到非列表类型: {type(ids)}，尝试转换')
            try:
                ids = [ids] if ids is not None else []
            except Exception:
                return []
        
        # 类型转换，跳过 None 和无法转换的值
        result = []
        for cid in ids:
            if cid is None:
                continue
            try:
                result.append(int(cid))
            except (ValueError, TypeError):
                logging.debug(f'normalize_id_list 跳过无效的 ID 值: {cid} (类型: {type(cid)})')
                continue
        
        return result

    def __init__(self, opts: MoodleDlOpts):
        self._whole_config: Dict[str, Any] = {}
        self.opts: MoodleDlOpts = opts
        self.config_path: str = str(Path(opts.path) / 'config.json')
        self._auth_manager: Any = None  # AuthSessionManager
        self._db_file: str = None

        # 初始化认证管理器(用于存储 tokens 到数据库)
        # 数据库必须初始化成功,否则抛出异常,不使用 fallback
        self._db_file = str(Path(opts.path) / 'moodle_state.db')
        
        # 关键修复：先初始化数据库表，再初始化认证管理器
        # 这确保在 --init 等操作中，数据库表已被正确创建
        try:
            from moodle_dl.database import StateRecorder
            # 创建 StateRecorder 以初始化数据库表（如果表不存在）
            # 这是一个轻量级操作，只在首次运行时会创建表
            StateRecorder(self, opts)
        except Exception as e:
            import logging
            logging.warning(f'⚠️  数据库初始化过程中出现警告: {e}')
            # 不抛出异常，继续进行，因为表可能已经存在
        
        from moodle_dl.auth_session_manager import AuthSessionManager
        self._auth_manager = AuthSessionManager(self._db_file)

        if not self._auth_manager:
            raise RuntimeError(
                f'❌ 认证管理器初始化失败.数据库必须可用.\n'
                f'检查 {self._db_file} 是否存在且可写.'
            )

    def is_present(self) -> bool:
        # Tests if a configuration file exists
        return os.path.isfile(self.config_path)

    def load(self, validate: bool = True, auto_fix: bool = False):
        """
        加载配置文件
        
        Args:
            validate: 是否验证配置（默认为 True）
            auto_fix: 是否自动修复常见问题（默认为 False）
        
        Raises:
            NoConfigError: 配置文件加载失败
            ValueError: 配置验证失败（如果 validate=True）
        """
        # TODO: Load config into dataclass, so we can access that class instead of using getters
        # Opens the configuration file and parse it to a JSON object
        try:
            with open(self.config_path, 'r', encoding='utf-8') as config_file:
                config_raw = config_file.read()
                self._whole_config = json.loads(config_raw)
        except (IOError, OSError) as err_load:
            raise ConfigHelper.NoConfigError(f'Configuration could not be loaded from {self.config_path}\n{err_load!s}')
        except json.JSONDecodeError as err_json:
            raise ConfigHelper.NoConfigError(
                f'配置文件 JSON 格式错误: {self.config_path}\n{err_json!s}\n'
                f'提示: 使用 JSON 验证工具检查语法，或删除配置文件重新运行 --init'
            )
        
        # 配置验证
        if validate:
            from moodle_dl.config_validator import ConfigValidator, auto_fix_config
            
            validator = ConfigValidator(strict=False)
            result = validator.validate_config_data(self._whole_config)
            
            # 如果启用了自动修复
            if auto_fix and (result.has_errors() or result.has_warnings()):
                fixed_config, fixes = auto_fix_config(self._whole_config)
                if fixes:
                    logging.info('🔧 自动修复了以下配置问题:')
                    for fix in fixes:
                        logging.info(f'  • {fix}')
                    self._whole_config = fixed_config
                    self._save()
                    # 重新验证
                    result = validator.validate_config_data(self._whole_config)
            
            # 显示警告
            if result.has_warnings():
                logging.warning('⚠️  配置验证发现以下警告:')
                for warning in result.warnings:
                    logging.warning(f'  • {warning.field}: {warning.message}')
                    if warning.suggestion:
                        logging.warning(f'    💡 建议: {warning.suggestion}')
            
            # 如果有错误，抛出异常
            if result.has_errors():
                error_msg = '❌ 配置验证失败:\n'
                for error in result.errors:
                    error_msg += f'  • {error.field}: {error.message}\n'
                    if error.suggestion:
                        error_msg += f'    💡 建议: {error.suggestion}\n'
                raise ValueError(error_msg)

    def _get_default_download_options(self) -> Dict[str, bool]:
        """
        获取所有下载选项的默认值
        
        确保保存时配置文件包含所有下载选项，使用户一目了然所有可用的配置项
        
        Returns:
            包含所有下载选项及其默认值的字典
        """
        # 使用 dataclass 获取默认值，确保一致性
        return DownloadOptionsConfig().to_dict()

    def _ensure_download_options_present(self):
        """
        确保配置中存在所有下载选项
        
        对于缺失的选项，使用默认值填充。这样配置文件会始终包含所有可用的选项，
        用户可以很容易地看到哪些选项是可用的。
        """
        if 'download_options' not in self._whole_config:
            self._whole_config['download_options'] = {}
        
        download_options = self._whole_config['download_options']
        defaults = self._get_default_download_options()
        
        for option_name, default_value in defaults.items():
            if option_name not in download_options:
                download_options[option_name] = default_value
    
    def _get_download_options_config(self) -> DownloadOptionsConfig:
        """
        获取下载选项的 dataclass 对象（只读）
        
        这个方法提供类型安全的访问，但不会修改配置
        
        Returns:
            DownloadOptionsConfig 对象
        """
        download_options = self._whole_config.get('download_options', {})
        return DownloadOptionsConfig.from_dict(download_options)

    def _save(self, validate: bool = False):
        """
        保存配置到文件
        
        Args:
            validate: 是否在保存前验证配置（默认为 False，避免循环依赖）
        """
        # 改进: 保存前补全所有配置选项，使配置文件更完整
        self._ensure_download_options_present()
        
        # 可选：保存前验证
        if validate:
            from moodle_dl.config_validator import ConfigValidator
            validator = ConfigValidator(strict=False)
            result = validator.validate_config_data(self._whole_config)
            
            if result.has_errors():
                error_msg = '❌ 配置验证失败，无法保存:\n'
                for error in result.errors:
                    error_msg += f'  • {error.field}: {error.message}\n'
                raise ValueError(error_msg)
            
            if result.has_warnings():
                logging.warning('⚠️  配置保存时发现警告:')
                for warning in result.warnings:
                    logging.warning(f'  • {warning.field}: {warning.message}')
        
        config_formatted = json.dumps(self._whole_config, indent=4)
        # Saves the JSON object back to file
        with os.fdopen(
            os.open(self.config_path, flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode=0o600),
            mode='w',
            encoding='utf-8',
        ) as config_file:
            config_file.write(config_formatted)

    def get_property(self, key: str) -> any:
        # return a property if configured
        try:
            return self._whole_config[key]
        except KeyError:
            raise ValueError(f'The {key}-Property is not yet configured!')

    def get_property_or(self, key: str, default: any = None) -> any:
        # return a property if configured
        try:
            return self._whole_config[key]
        except KeyError:
            return default

    def has_property(self, key: str) -> bool:
        """Check if a property exists in the configuration"""
        return key in self._whole_config

    def get_download_option(self, option_name: str, default: bool = False) -> bool:
        """
        Generic getter for download_* configuration options.

        Args:
            option_name: The name of the option (e.g., 'submissions', 'quizzes')
                        Will be prefixed with 'download_'
            default: Default value if not configured

        Returns:
            bool: Whether the option should be downloaded

        Example:
            get_download_option('submissions') -> reads 'download_submissions'
        """
        return self.get_property_or(f'download_{option_name}', default)

    def set_property(self, key: str, value: any, validate: bool = False):
        """
        设置配置属性
        
        Args:
            key: 属性键
            value: 属性值
            validate: 是否验证配置（默认为 False）
        """
        # sets a property in the JSON object
        self._whole_config.update({key: value})
        self._save(validate=validate)

    def remove_property(self, key: str, validate: bool = False):
        """
        移除配置属性
        
        Args:
            key: 属性键
            validate: 是否验证配置（默认为 False）
        """
        # removes a property from the JSON object
        self._whole_config.pop(key, None)
        #                           ^ behavior if the key is not present
        self._save(validate=validate)
    
    def validate(self) -> bool:
        """
        验证当前配置
        
        Returns:
            bool: 配置是否有效（如果有错误返回 False，只有警告也返回 True）
        """
        from moodle_dl.config_validator import ConfigValidator
        
        validator = ConfigValidator(strict=False)
        result = validator.validate_config_data(self._whole_config)
        
        # 显示结果
        if result.has_errors() or result.has_warnings():
            print(result.get_summary())
        else:
            logging.info('✅ 配置验证通过')
        
        return not result.has_errors()

    # ---------------------------- GETTERS ------------------------------------

    # Download option getters - using generic method to avoid code duplication
    # ========================================================================
    # 下载选项 Getter 方法 (使用 dataclass 简化，提供类型安全)
    # ========================================================================
    
    def get_download_submissions(self) -> bool:
        """获取是否下载作业提交"""
        return self._get_download_options_config().submissions

    def get_download_descriptions(self) -> bool:
        """获取是否下载描述"""
        return self._get_download_options_config().descriptions

    def get_download_links_in_descriptions(self) -> bool:
        """获取是否下载描述中的链接"""
        return self._get_download_options_config().links_in_descriptions

    def get_download_databases(self) -> bool:
        """获取是否下载数据库"""
        return self._get_download_options_config().databases

    def get_download_forums(self) -> bool:
        """获取是否下载论坛"""
        return self._get_download_options_config().forums

    def get_download_quizzes(self) -> bool:
        """获取是否下载测验"""
        return self._get_download_options_config().quizzes

    def get_download_lessons(self) -> bool:
        """获取是否下载课程"""
        return self._get_download_options_config().lessons

    def get_download_workshops(self) -> bool:
        """获取是否下载研讨会"""
        return self._get_download_options_config().workshops

    def get_download_books(self) -> bool:
        """获取是否下载书籍"""
        return self._get_download_options_config().books

    def get_download_bigbluebuttonbns(self) -> bool:
        """获取是否下载 BigBlueButton 会议"""
        return self._get_download_options_config().bigbluebuttonbns

    def get_download_wikis(self) -> bool:
        """获取是否下载 Wiki"""
        return self._get_download_options_config().wikis

    def get_download_glossaries(self) -> bool:
        """获取是否下载词汇表"""
        return self._get_download_options_config().glossaries

    def get_download_h5pactivities(self) -> bool:
        """获取是否下载 H5P 活动"""
        return self._get_download_options_config().h5pactivities

    def get_download_h5p_attempts(self) -> bool:
        """获取是否下载 H5P 尝试"""
        return self._get_download_options_config().h5p_attempts

    def get_download_imscps(self) -> bool:
        """获取是否下载 IMSCP 包"""
        return self._get_download_options_config().imscps

    def get_download_scorms(self) -> bool:
        """获取是否下载 SCORM 课程"""
        return self._get_download_options_config().scorms

    def get_download_scorm_scos(self) -> bool:
        """获取是否下载 SCORM 模块"""
        return self._get_download_options_config().scorm_scos

    def get_download_scorm_attempts(self) -> bool:
        """获取是否下载 SCORM 尝试"""
        return self._get_download_options_config().scorm_attempts

    def get_download_subsections(self) -> bool:
        """获取是否下载子章节"""
        return self._get_download_options_config().subsections

    def get_download_qbanks(self) -> bool:
        """获取是否下载题库"""
        return self._get_download_options_config().qbanks

    def get_download_resources(self) -> bool:
        """获取是否下载资源（PDF、视频等文件）"""
        # Resource modules are one of the most commonly used in Moodle for file uploads
        return self._get_download_options_config().resources

    def get_download_urls(self) -> bool:
        """获取是否下载 URL 链接"""
        return self._get_download_options_config().urls

    def get_download_labels(self) -> bool:
        """获取是否下载标签"""
        return self._get_download_options_config().labels

    def get_download_chats(self) -> bool:
        """获取是否下载聊天"""
        return self._get_download_options_config().chats

    def get_download_choices(self) -> bool:
        """获取是否下载选择"""
        return self._get_download_options_config().choices

    def get_download_feedbacks(self) -> bool:
        """获取是否下载反馈"""
        return self._get_download_options_config().feedbacks

    def get_download_surveys(self) -> bool:
        """获取是否下载问卷"""
        return self._get_download_options_config().surveys

    def get_download_ltis(self) -> bool:
        """
        获取是否下载 LTI 工具
        
        LTI (external tool) module is always enabled for complete metadata export
        This method is kept for backward compatibility but always returns True
        The old cookie-based handling for 'lti' has been replaced with the full LTI module
        Note: kalvidres and helixmedia still use cookie-based handling with dedicated extractors
        """
        return True  # Always enabled

    def get_download_calendars(self) -> bool:
        """获取是否下载日历"""
        return self._get_download_options_config().calendars

    def get_download_metadata_files(self) -> bool:
        """
        获取是否下载元数据文件（JSON、info 等）
        
        元数据文件包括：
        - 资源模块元数据（.json 文件对应 PDF/视频）
        - 模块信息文件（_info）
        - 笔记文件（_notes.md）
        - 其他自动生成的元数据
        
        默认值: False（禁用以保持下载清洁）
        """
        return self._get_download_options_config().metadata_files

    def get_auth_manager(self):
        """获取 AuthSessionManager 实例(用于数据库操作)"""
        return self._auth_manager

    def get_userid_and_version(self) -> Tuple[str, int]:
        # return the userid and a version
        try:
            user_id = self.get_property('userid')
            version = int(self.get_property('version'))
            return user_id, version
        except ValueError:
            return None, None

    def get_do_not_ask_to_save_userid_and_version(self) -> bool:
        return self.get_property_or('do_not_ask_to_save_userid_and_version', False)

    def get_download_course_ids(self) -> List[int]:
        """获取应该下载的课程 ID 列表"""
        ids = self.get_property_or('download_course_ids', [])
        return self.normalize_id_list(ids)

    def get_download_public_course_ids(self) -> List[int]:
        """获取应该下载的公开课程 ID 列表"""
        ids = self.get_property_or('download_public_course_ids', [])
        return self.normalize_id_list(ids)
    
    def get_manually_specified_course_ids(self) -> List[int]:
        """
        获取手动指定的课程 ID 列表
        
        这些是用户通过网页版 API 指定的课程，
        通常是他们有权限访问但没有 enrolled 的课程（如教师/TA）。
        
        Returns:
            List[int]: 手动指定的课程 ID 列表
        """
        return self.get_property_or('manually_specified_course_ids', [])
    
    def set_manually_specified_course_ids(self, course_ids: List[int]):
        """
        设置手动指定的课程 ID 列表
        
        Args:
            course_ids: 课程 ID 列表
        """
        # 验证输入
        if not isinstance(course_ids, list):
            raise ValueError("course_ids 必须是列表类型")
        
        if not all(isinstance(cid, int) for cid in course_ids):
            raise ValueError("所有课程 ID 必须是整数")
        
        # 移除重复的 ID
        unique_ids = list(set(course_ids))
        unique_ids.sort()  # 按升序排列便于阅读
        
        self.set_property('manually_specified_course_ids', unique_ids)

    def get_token(self) -> str:
        """
        获取 token

        v2:从数据库读取有效的 token
        如果数据库中无有效 token,则从 JSON 配置读取
        (不做 fallback,数据库读取失败直接抛出异常)
        """
        # 优先从数据库读取有效的 token session
        session = self._auth_manager.get_valid_session(session_type='token')
        if session and session.get('token_value'):
            return session['token_value']

        # 如果数据库无有效 token,从 JSON 配置读取
        try:
            return self.get_property('token')
        except ValueError:
            raise ValueError('Token not yet configured!')

    def get_privatetoken(self) -> str:
        """
        获取 private token

        v2:从数据库读取有效的 private token
        如果数据库中无有效 private token,则从 JSON 配置读取
        """
        # 优先从数据库读取有效的 token session
        session = self._auth_manager.get_valid_session(session_type='token')
        if session and session.get('private_token_value'):
            return session['private_token_value']

        # 如果数据库无有效 private token,从 JSON 配置读取
        return self.get_property_or('privatetoken', None)

    def get_moodle_URL(self) -> MoodleURL:
        moodle_domain = self.get_moodle_domain()
        moodle_path = self.get_moodle_path()
        use_http = self.get_use_http()
        return MoodleURL(use_http, moodle_domain, moodle_path)

    def get_moodle_domain(self) -> str:
        # return a stored moodle_domain
        try:
            return self.get_property('moodle_domain')
        except ValueError:
            raise ValueError('Not yet configured!')

    def get_moodle_path(self) -> str:
        # return a stored moodle_path
        try:
            return self.get_property('moodle_path')
        except ValueError:
            raise ValueError('Not yet configured!')

    def get_options_of_courses(self) -> Dict:
        # return a stored dictionary of options for courses
        return self.get_property_or('options_of_courses', {})

    def get_dont_download_course_ids(self) -> List[int]:
        """获取不应该下载的课程 ID 列表"""
        ids = self.get_property_or('dont_download_course_ids', [])
        return self.normalize_id_list(ids)

    def get_download_linked_files(self) -> bool:
        # return if linked files should be downloaded
        return self.get_property_or('download_linked_files', False)

    def get_download_domains_whitelist(self) -> List:
        # return a list of white listed domains that should be downloaded
        return self.get_property_or('download_domains_whitelist', [])

    def get_download_domains_blacklist(self) -> List:
        # return a list of black listed domains that should not be downloaded
        return self.get_property_or('download_domains_blacklist', [])

    def get_cookies_text(self) -> str:
        """
        获取 cookies 文本
        
        v2:优先从数据库读取 cookie_batch 会话的 cookies
        如果数据库中没有或读取失败,回退到读取 txt 文件
        """
        # v2: 尝试从数据库读取 cookies(新方式)
        try:
            # 查找最新的有效的 cookie batch 会话
            session = self._auth_manager.get_valid_session(session_type='cookie_batch')
            if session:
                session_id = session['session_id']
                cookies = self._auth_manager.get_session_cookies(session_id)
                
                # 将 cookies 转换为 Netscape cookies.txt 格式
                if cookies:
                    cookie_lines = []
                    cookie_lines.append('# Netscape HTTP Cookie File')
                    cookie_lines.append('# Generated by Moodle-DL Auth Session Manager')
                    cookie_lines.append('')
                    
                    for cookie in cookies:
                        # 格式: domain	flag	path	secure	expires	name	value
                        domain = cookie.get('domain', '')
                        # 如果域名不以.开头且不是localhost,添加.
                        if domain and not domain.startswith('.') and domain != 'localhost':
                            domain = '.' + domain

                        flag = 'TRUE' if domain.startswith('.') else 'FALSE'
                        path = cookie.get('path', '/')
                        secure = 'TRUE' if cookie.get('secure', 0) else 'FALSE'

                        # ⚠️ 重要：Netscape cookies.txt 不接受 -1 作为 expires
                        # - 在 Playwright/数据库中，-1 表示「会话 cookie」
                        # - 但 yt-dlp / MozillaCookieJar 会直接丢弃 expires=-1 的条目
                        #   （表现为你看到的 “skipping cookie file entry due to invalid expires at -1”）
                        # - 为了兼容，它们期望会话 cookie 用 0 或空字符串
                        expires_raw = cookie.get('expires', 0)
                        if expires_raw is None or expires_raw <= 0:
                            # 统一映射为 0，表示「会话 cookie」，避免被 yt-dlp 跳过
                            expires = '0'
                        else:
                            try:
                                expires = str(int(expires_raw))
                            except (TypeError, ValueError):
                                # 异常情况安全回退为 0（会话）
                                expires = '0'

                        name = cookie.get('name', '')
                        value = cookie.get('value', '')
                        
                        if name and value:
                            cookie_line = f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}"
                            cookie_lines.append(cookie_line)
                    
                    cookies_text = '\n'.join(cookie_lines)
                    if cookies_text.strip():
                        return cookies_text
        except Exception as e:
            # 数据库读取失败,记录日志并回退到文件读取
            logging.error(f'从数据库读取 cookies 失败: {e},回退到文件读取')
        
        # 不再支持 Cookies.txt，仅使用数据库
        return None

    def get_yt_dlp_options(self) -> Dict:
        # return additional yt-dlp options
        return self.get_property_or('yt_dlp_options', {})

    def get_video_passwords(self) -> Dict:
        # return dict with passwords that get passed to yt-dlp
        return self.get_property_or('video_passwords', {})

    def get_external_file_downloaders(self) -> Dict:
        # return dict with configured external downloaders
        return self.get_property_or('external_file_downloaders', {})

    def get_exclude_file_extensions(self) -> Dict:
        # return a list of file extensions that should not be downloaded
        try:
            exclude_file_extensions = self.get_property('exclude_file_extensions')
            if not isinstance(exclude_file_extensions, list):
                exclude_file_extensions = [exclude_file_extensions]
            return exclude_file_extensions
        except ValueError:
            return []

    def get_max_file_size(self) -> int:
        # return the max size in bytes of files that should not be downloaded
        # default: 0 -> all file sizes
        return self.get_property_or('max_file_size', 0)

    def get_download_also_with_cookie(self) -> Dict:
        # return if files for which a cookie is required should be downloaded
        return self.get_property_or('download_also_with_cookie', False)

    def get_write_links(self) -> Dict:
        # returns what kind of shortcuts should be created
        write_links = {
            'url': self.get_property_or('write_url_link', False),
            'webloc': self.get_property_or('write_webloc_link', False),
            'desktop': self.get_property_or('write_desktop_link', False),
        }
        if self.get_property_or('write_link', True):
            link_type = (
                'webloc' if sys.platform == 'darwin' else 'desktop' if sys.platform.startswith('linux') else 'url'
            )
            write_links[link_type] = True

        return write_links

    def get_download_options(self, opts: MoodleDlOpts) -> DownloadOptions:
        # return the option dictionary for downloading files

        return DownloadOptions(
            token=self.get_token(),
            moodle_url=self.get_moodle_URL().url_base,
            download_linked_files=self.get_download_linked_files(),
            download_domains_whitelist=self.get_download_domains_whitelist(),
            download_domains_blacklist=self.get_download_domains_blacklist(),
            cookies_text=self.get_cookies_text(),
            yt_dlp_options=self.get_yt_dlp_options(),
            video_passwords=self.get_video_passwords(),
            external_file_downloaders=self.get_external_file_downloaders(),
            restricted_filenames=self.get_restricted_filenames(),
            write_links=self.get_write_links(),
            download_path=self.get_download_path(),
            download_metadata_files=self.get_download_metadata_files(),
            global_opts=opts,
        )

    def get_restricted_filenames(self) -> Dict:
        # return the filenames should be restricted
        return self.get_property_or('restricted_filenames', False)

    def get_use_http(self) -> bool:
        # return a stored boolean if http should be used instead of https
        return self.get_property_or('use_http', False)

    def get_download_path(self) -> str:
        # return path of download location
        return self.get_property_or('download_path', self.opts.path)

    def get_misc_files_path(self) -> str:
        # return path of misc files
        return self.get_property_or('misc_files_path', self.opts.path)

    # ---------------------------- SETTERS ------------------------------------

    def set_moodle_URL(self, moodle_url: MoodleURL):
        self.set_property('moodle_domain', moodle_url.domain)
        self.set_property('moodle_path', moodle_url.path)
        if moodle_url.use_http is True:
            self.set_property('use_http', moodle_url.use_http)
        else:
            if self.get_use_http():
                self.set_property('use_http', moodle_url.use_http)

    def set_tokens(self, moodle_token: str, moodle_privatetoken: str):
        """
        设置 token 和 private token

        v2:同时保存到数据库和 JSON 配置
        - JSON:向后兼容
        - 数据库:存储到 auth_sessions 表,形成版本链

        数据库操作失败时直接抛出异常(不做 fallback)
        """
        # 1. 保存到数据库(优先执行,失败则抛出异常)
        # 尝试获取已有的 token session
        old_session = self._auth_manager.get_valid_session(session_type='token')

        if old_session:
            # 创建新版本的 session
            self._auth_manager.refresh_session(
                old_session_id=old_session['session_id'],
                new_token=moodle_token,
                new_private_token=moodle_privatetoken
            )
        else:
            # 创建新 session
            self._auth_manager.create_session(
                session_type='token',
                source='api_login',
                token=moodle_token,
                private_token=moodle_privatetoken
            )

        # 2. 保存到 JSON 配置(向后兼容)
        self.set_property('token', moodle_token)
        if moodle_privatetoken is not None:
            self.set_property('privatetoken', moodle_privatetoken)
