# -*- coding: utf-8 -*-
"""
认证器体系 - 处理所有 token 获取流程（普通登录、SSO 登录等）

设计原则：
1. 代码复用性：提取公共逻辑到 Helper 类
2. 封装性：每个认证器独立处理一种认证方式
3. 原子性：通过 ConfigurationTransaction 确保配置保存的一致性
4. 可测试性：每个认证器独立可测试
"""

import sys
import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from getpass import getpass
from typing import Tuple, Optional, Dict, Any

from moodle_dl.config import ConfigHelper
from moodle_dl.types import MoodleDlOpts, MoodleURL
from moodle_dl.moodle.moodle_service import MoodleService
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.utils import Log
from moodle_dl.cli.localization import tr as _


TRUE_ENV_VALUES = {'1', 'true', 'yes', 'y', 'on'}
FALSE_ENV_VALUES = {'0', 'false', 'no', 'n', 'off'}


def _read_bool_env(name: str) -> Optional[bool]:
    value = os.getenv(name)
    if value is None:
        return None

    normalized = value.strip().lower()
    if normalized in TRUE_ENV_VALUES:
        return True
    if normalized in FALSE_ENV_VALUES:
        return False

    logging.warning(_('⚠️  环境变量 %s=%r 无法识别，将忽略', '⚠️  Environment variable %s=%r is not recognized and will be ignored'), name, value)
    return None


def _should_use_headless_sso() -> bool:
    """Return whether SSO auto login should use a headless Playwright browser."""
    headless = _read_bool_env('MOODLE_DL_HEADLESS')
    if headless is not None:
        return headless

    headful = _read_bool_env('MOODLE_DL_HEADFUL')
    if headful is not None:
        return not headful

    return False


# ==================== 异常定义 ====================

class AuthenticationError(Exception):
    """认证失败异常"""
    pass


class ConfigurationTransactionError(Exception):
    """配置事务错误"""
    pass


# ==================== 数据类 ====================

@dataclass
class TokenAcquisitionResult:
    """Token 获取结果 - 追踪所有需要保存到配置的状态"""
    token: str
    private_token: Optional[str] = None
    extra_properties: Dict[str, Any] = None  # 其他配置项（如 preferred_browser）

    def __post_init__(self):
        if self.extra_properties is None:
            self.extra_properties = {}

    def validate(self) -> None:
        """验证获取的 token 有效性"""
        if not self.token:
            raise AuthenticationError(_('Token 不能为空', 'Token cannot be empty'))
        if not isinstance(self.token, str):
            raise AuthenticationError(
                _('Token 必须是字符串，当前类型: {token_type}', 'Token must be a string, current type: {token_type}', token_type=type(self.token))
            )


# ==================== 配置事务系统 ====================

class ConfigurationTransaction:
    """
    配置事务 - 确保原子性

    功能：
    1. 记录所有要保存的配置变更
    2. 全部成功时一次性提交
    3. 任何失败时完全回滚（不保存任何内容）
    """

    def __init__(self, config: ConfigHelper, moodle_url: MoodleURL):
        self.config = config
        self.moodle_url = moodle_url
        self._operations = []  # 记录所有待提交的操作
        self._committed = False

    def add_token(self, token: str, private_token: Optional[str] = None) -> None:
        """添加 token 保存操作"""
        self._operations.append({
            'type': 'tokens',
            'token': token,
            'private_token': private_token
        })

    def add_property(self, key: str, value: Any) -> None:
        """添加单个配置属性保存操作"""
        self._operations.append({
            'type': 'property',
            'key': key,
            'value': value
        })

    def commit(self) -> None:
        """
        原子性提交所有操作

        提交顺序：
        1. Token 保存（最重要）
        2. URL 保存
        3. 其他属性保存

        任何失败都会抛出异常，配置保持一致
        """
        if self._committed:
            raise ConfigurationTransactionError(_('事务已提交，不能重复提交', 'Transaction has already been committed and cannot be committed again'))

        if not self._operations:
            logging.debug(_('📋 事务为空，无需提交', '📋 Transaction is empty; nothing to commit'))
            return

        try:
            # 阶段 1: 提交 token（最关键）
            token_ops = [op for op in self._operations if op['type'] == 'tokens']
            if token_ops:
                for op in token_ops:
                    logging.debug(_('💾 提交 token 到数据库...', '💾 Committing token to database...'))
                    self.config.set_tokens(op['token'], op['private_token'])

            # 阶段 2: 提交 URL
            logging.debug(_('💾 提交 Moodle URL: {domain}', '💾 Committing Moodle URL: {domain}', domain=self.moodle_url.domain))
            self.config.set_moodle_URL(self.moodle_url)

            # 阶段 3: 提交其他属性
            property_ops = [op for op in self._operations if op['type'] == 'property']
            for op in property_ops:
                logging.debug(_('💾 提交属性: {key} = {value}', '💾 Committing property: {key} = {value}', key=op['key'], value=op['value']))
                self.config.set_property(op['key'], op['value'])

            self._committed = True
            logging.info(_('✅ 配置事务提交成功（原子性保证）', '✅ Configuration transaction committed successfully (atomic guarantee)'))

        except Exception as e:
            logging.error(_('❌ 配置事务提交失败: {error}', '❌ Failed to commit configuration transaction: {error}', error=e))
            logging.error(_('⚠️  由于失败，所有配置更改都未被保存（保持一致性）', '⚠️  Because of the failure, no configuration changes were saved (consistency preserved)'))
            raise ConfigurationTransactionError(_('提交失败: {error}', 'Commit failed: {error}', error=e)) from e

    def is_committed(self) -> bool:
        """检查事务是否已提交"""
        return self._committed


# ==================== 认证辅助类 ====================

class BrowserSelector:
    """浏览器选择器 - 提取浏览器选择逻辑"""

    BROWSER_CHOICES = [
        'Firefox',
        'Chrome',
        'Edge',
        'Safari',
        'Brave',
        'Arc',
        'Zen Browser',
        'Waterfox',
    ]

    BROWSER_MAP = {
        0: 'firefox',
        1: 'chrome',
        2: 'edge',
        3: 'safari',
        4: 'brave',
        5: 'arc',
        6: 'zen',
        7: 'waterfox',
    }

    @staticmethod
    def select_or_load(config: ConfigHelper) -> str:
        """
        选择浏览器或加载已保存的浏览器

        返回: 浏览器名称字符串（如 'firefox'）
        """
        preferred_browser = config.get_property_or('preferred_browser', None)

        if preferred_browser:
            logging.debug(_('已加载首选浏览器: {browser}', 'Loaded preferred browser: {browser}', browser=preferred_browser))
            return preferred_browser

        # 首次配置，需要询问用户
        print('')
        Log.blue(_('请选择你使用的浏览器：', 'Select the browser you use:'))

        try:
            from moodle_dl.utils import Cutie
            browser_choice = Cutie.select(BrowserSelector.BROWSER_CHOICES)
        except ImportError:
            Log.error(_('缺少依赖库 Cutie，无法进行浏览器选择', 'Missing dependency Cutie; cannot select a browser'))
            raise AuthenticationError(_('缺少必要的依赖库', 'Missing required dependency'))

        if browser_choice not in BrowserSelector.BROWSER_MAP:
            raise AuthenticationError(_('无效的浏览器选择: {choice}', 'Invalid browser selection: {choice}', choice=browser_choice))

        browser_name = BrowserSelector.BROWSER_MAP[browser_choice]
        display_name = BrowserSelector.BROWSER_CHOICES[browser_choice]

        Log.info(_('✓ 已选择：{display_name}', '✓ Selected: {display_name}', display_name=display_name))
        print('')

        return browser_name


class ExportBrowserCookiesHelper:
    """
    浏览器 Cookies 导出辅助类 - 处理所有与 export_browser_cookies 相关的逻辑
    """

    @staticmethod
    def load_export_module():
        """
        动态加载 export_browser_cookies.py 模块

        返回: export_module 或 None
        异常: ImportError 或 FileNotFoundError
        """
        import importlib.util

        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'export_browser_cookies.py',
        )

        if not os.path.exists(script_path):
            logging.error(_('未找到 export_browser_cookies.py: {path}', 'export_browser_cookies.py not found: {path}', path=script_path))
            raise FileNotFoundError(_('export_browser_cookies.py 不存在', 'export_browser_cookies.py does not exist'))

        try:
            spec = importlib.util.spec_from_file_location("export_browser_cookies", script_path)
            export_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(export_module)
            return export_module
        except Exception as e:
            raise ImportError(_('无法加载 export_browser_cookies.py: {error}', 'Unable to load export_browser_cookies.py: {error}', error=e)) from e


class SSOReferenceHelper:
    """SSO 参考文本辅助类 - 处理手动 token 获取的参考信息"""

    @staticmethod
    def show_manual_token_help(moodle_url: MoodleURL) -> None:
        """显示手动获取 token 的帮助信息"""
        print('')
        Log.warning(_('请使您选择的浏览器进行以下操作', 'In your selected browser, do the following:'))
        print(_('1. 登录你的 Moodle 账户', '1. Log in to your Moodle account'))
        print(_('2. 打开开发者控制台（按 F12）并转到 Network（网络）标签', '2. Open Developer Tools (F12) and go to the Network tab'))
        print(_('3. 然后在你已登录的同一浏览器标签页中访问以下 URL：', '3. Then open the following URL in the same logged-in browser tab:'))
        print('')
        print(
            moodle_url.url_base
            + 'admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&urlscheme=moodledl'
        )
        print()
        print(
            _(
                '如果你打开链接，不应该加载网页，而是会出现错误。'
                + '在你之前打开的开发者控制台的 Network 标签中应该有一个错误条目。',
                'When you open the link, no web page should load; an error should appear instead. '
                + 'There should be a failed entry in the Network tab you opened earlier.'
            )
        )

        print(_('脚本期望一个类似这样的 URL：', 'The script expects a URL like this:'))
        Log.info('moodledl://token=$apptoken')
        print(
            _(
                ' 其中 $apptoken 看起来是随机的，"moodledl" 也可以是不同的 URL scheme，'
                + '比如 "moodlemobile"。实际上 $apptoken 是一个包含访问 Moodle 令牌的 Base64 字符串。',
                ' Here $apptoken looks random. The URL scheme may be different from "moodledl", '
                + 'for example "moodlemobile". The $apptoken value is a Base64 string containing the Moodle access token.'
            )
        )

        print(
            _(
                '4. 复制无法加载的网站的链接地址' + '（右键单击列表条目，然后单击"复制"，然后单击"复制链接地址"）',
                '4. Copy the failed request URL (right-click the list entry, then choose Copy, then Copy link address)'
            )
        )


# ==================== 基础认证器 ====================

class BaseAuthenticator(ABC):
    """
    认证器基类 - 定义所有认证方式的通用接口

    工作流程：
    1. pre_configure() - 前置配置（可选）
    2. acquire_token() - 获取 token
    3. commit_configuration() - 原子性提交配置
    """

    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts, moodle_url: MoodleURL):
        self.config = config
        self.opts = opts
        self.moodle_url = moodle_url
        self._result = None
        self._transaction = None

    @property
    def result(self) -> Optional[TokenAcquisitionResult]:
        """获取 token 获取结果"""
        return self._result

    def pre_configure(self) -> None:
        """
        前置配置钩子 - 子类可以覆盖

        用于：
        - 浏览器选择
        - 用户信息输入
        - 配置验证
        等
        """
        pass

    @abstractmethod
    def acquire_token(self) -> TokenAcquisitionResult:
        """
        获取 token - 必须由子类实现

        返回: TokenAcquisitionResult
        异常: AuthenticationError
        """
        raise NotImplementedError

    def _validate_result(self) -> None:
        """验证 token 获取结果"""
        if not self._result:
            raise AuthenticationError(_('Token 获取结果为空', 'Token acquisition result is empty'))

        self._result.validate()

    def commit_configuration(self) -> None:
        """
        原子性提交配置

        这是事务的关键：
        1. 创建事务
        2. 添加所有要保存的操作
        3. 一次性提交（原子性）
        4. 失败则一个都不保存
        """
        if not self._result:
            raise ConfigurationTransactionError(_('没有 token 结果可提交', 'No token result to commit'))

        # 验证结果
        self._validate_result()

        # 创建事务
        self._transaction = ConfigurationTransaction(self.config, self.moodle_url)

        # 添加所有待提交的操作
        self._transaction.add_token(self._result.token, self._result.private_token)

        # 添加额外的配置属性（如 preferred_browser）
        for key, value in self._result.extra_properties.items():
            self._transaction.add_property(key, value)

        # 原子性提交
        self._transaction.commit()

        Log.success(_('✅ 令牌已成功保存！', '✅ Token saved successfully!'))

    def execute(self) -> str:
        """
        执行完整的认证流程

        返回: token 字符串
        异常: AuthenticationError, ConfigurationTransactionError
        """
        try:
            # 1. 前置配置
            logging.info(_('📋 进行前置配置...', '📋 Running pre-configuration...'))
            self.pre_configure()

            # 2. 获取 token
            logging.info(_('🔑 开始获取 token...', '🔑 Starting token acquisition...'))
            self._result = self.acquire_token()

            # 3. 原子性提交配置
            logging.info(_('💾 提交配置...', '💾 Saving configuration...'))
            self.commit_configuration()

            return self._result.token

        except AuthenticationError as e:
            logging.error(_('❌ 认证失败: {error}', '❌ Authentication failed: {error}', error=e))
            raise
        except ConfigurationTransactionError as e:
            logging.error(_('❌ 配置提交失败: {error}', '❌ Failed to save configuration: {error}', error=e))
            raise
        except Exception as e:
            logging.error(_('❌ 认证过程出错: {error}', '❌ Authentication process failed: {error}', error=e))
            raise AuthenticationError(_('认证过程出错: {error}', 'Authentication process failed: {error}', error=e)) from e


# ==================== 普通登录认证器 ====================

class NormalAuthenticator(BaseAuthenticator):
    """
    普通登录认证器 - 使用用户名/密码进行登录

    特点：
    - 支持命令行参数自动化
    - 支持交互式输入
    - 支持重试机制
    - 可选的 cookies 导出
    """

    MAX_LOGIN_ATTEMPTS = 3  # 最大登录尝试次数

    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts, moodle_url: MoodleURL):
        super().__init__(config, opts, moodle_url)
        self._login_attempts = 0

    def _get_credentials(self) -> Tuple[str, str]:
        """
        获取用户凭据

        优先级：
        1. 命令行参数 (opts.username, opts.password)
        2. 交互式输入

        返回: (username, password)
        """
        if self.opts.username is not None and self.opts.password is not None:
            logging.info(_('🔐 使用命令行提供的凭据', '🔐 Using credentials provided on the command line'))
            return self.opts.username, self.opts.password

        # 交互式输入
        username = input(_('Moodle 用户名:   ', 'Moodle username:   ')).strip()
        if not username:
            raise AuthenticationError(_('用户名不能为空', 'Username cannot be empty'))

        password = getpass(_('Moodle 密码 [无输出显示]:   ', 'Moodle password [no output shown]:   '))
        if not password:
            raise AuthenticationError(_('密码不能为空', 'Password cannot be empty'))

        return username, password

    def acquire_token(self) -> TokenAcquisitionResult:
        """
        获取 token - 通过用户名/密码登录

        工作流程：
        1. 获取凭据
        2. 尝试登录（最多 MAX_LOGIN_ATTEMPTS 次）
        3. 返回 token 结果

        返回: TokenAcquisitionResult
        异常: AuthenticationError
        """
        if self.opts.username is not None or self.opts.password is not None:
            # 命令行自动化模式 - 只尝试一次
            max_attempts = 1
            print(_('[使用命令行参数自动化登录，仅尝试一次]', '[Using command-line credentials; trying once]'))
        else:
            # 交互式模式 - 允许多次重试
            max_attempts = self.MAX_LOGIN_ATTEMPTS
            print(_('[以下凭据不会被保存，仅临时用于生成登录令牌。]', '[The following credentials will not be saved; they are only used temporarily to generate a login token.]'))

        print('')

        for attempt in range(1, max_attempts + 1):
            try:
                logging.info(_('🔐 登录尝试 {attempt}/{max_attempts}...', '🔐 Login attempt {attempt}/{max_attempts}...', attempt=attempt, max_attempts=max_attempts))

                # 获取凭据
                username, password = self._get_credentials()

                # 调用 Moodle 登录
                moodle_service = MoodleService(self.config, self.opts)
                token, private_token = moodle_service.obtain_login_token(
                    username, password, self.moodle_url
                )

                if not token:
                    raise AuthenticationError(_('未收到有效的 token', 'Did not receive a valid token'))

                logging.info(_('✅ 登录成功！', '✅ Login successful!'))
                print('')

                # 提示可选的 cookies 导出
                self._prompt_cookies_export()

                # 返回结果
                return TokenAcquisitionResult(
                    token=token,
                    private_token=private_token,
                    extra_properties={}
                )

            except RequestRejectedError as e:
                logging.error(_('❌ 登录失败（请求被拒绝）: {error}', '❌ Login failed (request rejected): {error}', error=e))
                if attempt < max_attempts:
                    print(_('请重试。', 'Please try again.'))
                    print('')
                else:
                    raise AuthenticationError(_('登录失败: {error}', 'Login failed: {error}', error=e)) from e

            except (ValueError, RuntimeError) as e:
                logging.error(_('❌ 与 Moodle 系统通信时出错: {error}', '❌ Error while communicating with the Moodle system: {error}', error=e))
                if attempt < max_attempts:
                    print(_('请重试。', 'Please try again.'))
                    print('')
                else:
                    raise AuthenticationError(_('与 Moodle 系统通信时出错: {error}', 'Error while communicating with the Moodle system: {error}', error=e)) from e

            except ConnectionError as e:
                logging.error(_('❌ 网络连接错误: {error}', '❌ Network connection error: {error}', error=e))
                if attempt < max_attempts:
                    print(_('请检查网络连接后重试。', 'Please check your network connection and try again.'))
                    print('')
                else:
                    raise AuthenticationError(_('网络连接错误: {error}', 'Network connection error: {error}', error=e)) from e

            except AuthenticationError:
                # 直接抛出
                raise
            except Exception as e:
                logging.error(_('❌ 登录时出现意外错误: {error}', '❌ Unexpected error during login: {error}', error=e))
                raise AuthenticationError(_('登录时出现意外错误: {error}', 'Unexpected error during login: {error}', error=e)) from e

        # 如果在自动化模式下全部失败
        if self.opts.username is not None or self.opts.password is not None:
            raise AuthenticationError(_('自动化登录失败（命令行参数可能有误）', 'Automated login failed (command-line arguments may be incorrect)'))

        raise AuthenticationError(_('达到最大尝试次数 ({max_attempts})，登录失败', 'Reached maximum attempts ({max_attempts}); login failed', max_attempts=max_attempts))

    def _prompt_cookies_export(self) -> None:
        """
        提示用户是否导出浏览器 cookies

        普通登录后，用户通常需要 cookies 来下载某些受保护的内容
        （如 Kaltura 视频、受保护的链接等）
        """
        try:
            print('')
            Log.info(_('💡 提示：某些内容需要浏览器cookies才能下载', '💡 Tip: some content requires browser cookies to download'))
            Log.info(_('   例如：Kaltura视频、描述中的受保护链接等', '   Examples: Kaltura videos, protected links in descriptions, and similar content'))
            print('')

            from moodle_dl.utils import Cutie, PathTools as PT

            should_export = Cutie.prompt_yes_or_no(
                Log.blue_str(_('是否现在从浏览器导出cookies（推荐）？', 'Export cookies from your browser now (recommended)?')), default_is_yes=True
            )

            if not should_export:
                Log.info(_('⏭️  跳过cookies导出，你可以稍后在配置步骤7导出', '⏭️  Skipping cookie export. You can export them later in configuration step 7.'))
                return

            # 加载 export_browser_cookies 模块
            try:
                export_module = ExportBrowserCookiesHelper.load_export_module()
            except (FileNotFoundError, ImportError) as e:
                Log.warning(_('⚠️  无法加载 export_browser_cookies: {error}', '⚠️  Unable to load export_browser_cookies: {error}', error=e))
                Log.info(_('   你可以稍后在配置步骤7导出cookies', '   You can export cookies later in configuration step 7'))
                return

            # 导出 cookies
            cookies_path = PT.get_cookies_path(self.config.get_misc_files_path())
            Log.info(_('正在从浏览器导出cookies...', 'Exporting cookies from the browser...'))

            try:
                success = export_module.export_cookies_interactive(
                    domain=self.moodle_url.domain,
                    output_file=cookies_path,
                    ask_browser=True,
                    auto_get_token=False,  # 已有 token，不需要再获取
                )

                if success:
                    Log.success(_('✅ Cookies导出成功！', '✅ Cookies exported successfully!'))
                else:
                    Log.warning(_('⚠️  Cookies导出失败，你可以稍后在配置步骤7重新导出', '⚠️  Cookie export failed. You can retry later in configuration step 7.'))
            except Exception as e:
                logging.error(_('导出 cookies 时出错: {error}', 'Error while exporting cookies: {error}', error=e))
                Log.warning(_('⚠️  Cookies导出出错: {error}', '⚠️  Cookie export error: {error}', error=e))
                Log.info(_('   你可以稍后在配置步骤7导出cookies', '   You can export cookies later in configuration step 7'))

        except ImportError as e:
            Log.warning(_('⚠️  缺少依赖库: {error}', '⚠️  Missing dependency: {error}', error=e))
            Log.info(_('   提示：运行 `pip install browser-cookie3`', '   Tip: run `pip install browser-cookie3`'))
            Log.info(_('   你可以稍后在配置步骤7导出cookies', '   You can export cookies later in configuration step 7'))
        except Exception as e:
            logging.error(_('导出 cookies 提示时出错: {error}', 'Error while showing cookie export prompt: {error}', error=e))
            # 不影响主流程


# ==================== SSO 登录认证器 ====================

class SSOAuthenticator(BaseAuthenticator):
    """
    SSO 登录认证器 - 使用浏览器 SSO cookies 自动完成登录

    工作流程：
    1. 前置配置：选择浏览器
    2. SSO 自动登录：使用 Playwright 从浏览器读取 SSO cookies 完成 Moodle 登录
    3. Token 提取：自动获取 API token
    4. 手动 token：如果自动获取失败，回退到手动输入

    特点：
    - 支持命令行 token 直接传入（--token 参数）
    - 支持完全自动化（SSO 登录 + 自动 token 提取）
    - 支持分阶段回退（自动 SSO → 手动 cookies 导出 → 手动 token 输入）
    - 支持浏览器选择持久化
    """

    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts, moodle_url: MoodleURL):
        super().__init__(config, opts, moodle_url)
        self.preferred_browser = None
        self._export_module = None
        self._cookies_path = None

    def pre_configure(self) -> None:
        """
        前置配置：选择浏览器

        如果已有偏好的浏览器设置，则使用已保存的选择；
        否则让用户选择并记录选择
        """
        try:
            logging.info(_('📋 选择浏览器...', '📋 Selecting browser...'))
            self.preferred_browser = BrowserSelector.select_or_load(self.config)
            logging.info(_('✓ 浏览器选择: {browser}', '✓ Browser selected: {browser}', browser=self.preferred_browser))
        except AuthenticationError as e:
            logging.error(_('❌ 浏览器选择失败: {error}', '❌ Browser selection failed: {error}', error=e))
            raise
        except Exception as e:
            logging.error(_('❌ 前置配置出错: {error}', '❌ Pre-configuration failed: {error}', error=e))
            raise AuthenticationError(_('前置配置出错: {error}', 'Pre-configuration failed: {error}', error=e)) from e

    def acquire_token(self) -> TokenAcquisitionResult:
        """
        获取 token - SSO 自动登录 + token 提取

        工作流程（带完整的回退机制）：
        1. 如果直接提供了 token，使用它
        2. 否则，尝试自动化流程：
           a. 加载 export_browser_cookies 模块
           b. SSO 自动登录获取 cookies
           c. 自动提取 API token
        3. 如果自动化失败，尝试回退：
           a. 从浏览器读取现有 cookies
           b. 再次尝试提取 API token
        4. 如果仍然失败，回退到手动 token 输入

        返回: TokenAcquisitionResult
        异常: AuthenticationError
        """
        # 优先级 1：直接提供的 token（如通过 --token 参数）
        if self.opts.token is not None:
            logging.info(_('🔑 使用命令行提供的 token', '🔑 Using token provided on the command line'))
            return TokenAcquisitionResult(
                token=self.opts.token,
                private_token=None,
                extra_properties={'preferred_browser': self.preferred_browser}
            )

        # 优先级 2：自动化流程
        logging.info(_('🚀 尝试完全自动获取 API token...', '🚀 Trying to get the API token fully automatically...'))
        logging.info(_('   策略：SSO 自动登录 + Playwright 自动获取 token', '   Strategy: SSO auto-login + Playwright token extraction'))
        logging.info(_('   优势：只要 SSO cookies 有效，完全无需手动操作！', '   Benefit: if SSO cookies are valid, no manual action is needed.'))
        print('')

        # 尝试自动化流程
        token, private_token = self._try_automatic_sso_flow()

        if token and private_token:
            return TokenAcquisitionResult(
                token=token,
                private_token=private_token,
                extra_properties={'preferred_browser': self.preferred_browser}
        )

        # 优先级 3：回退到手动 token 输入
        logging.info(_('⚙️  自动获取 token 失败，回退到手动输入', '⚙️  Automatic token acquisition failed; falling back to manual input'))
        token, private_token = self._get_manual_token()

        if not token:
            raise AuthenticationError(_('无法获取有效的 token（所有方法都失败）', 'Unable to get a valid token (all methods failed)'))

        return TokenAcquisitionResult(
            token=token,
            private_token=private_token,
            extra_properties={'preferred_browser': self.preferred_browser}
        )

    def _try_automatic_sso_flow(self) -> Tuple[Optional[str], Optional[str]]:
        """
        尝试完整的自动化 SSO 流程

        返回: (token, private_token) 或 (None, None) 表示失败
        """
        try:
            # 加载 export_browser_cookies 模块
            logging.info(_('📦 加载浏览器 cookie 导出模块...', '📦 Loading browser cookie export module...'))
            try:
                self._export_module = ExportBrowserCookiesHelper.load_export_module()
            except (FileNotFoundError, ImportError) as e:
                Log.warning(_('⚠️  无法加载导出模块: {error}', '⚠️  Unable to load export module: {error}', error=e))
                return None, None

            # 获取 cookies 保存路径
            from moodle_dl.utils import PathTools as PT
            self._cookies_path = PT.get_cookies_path(self.config.get_misc_files_path())

            # 步骤 1：SSO 自动登录
            if not self._perform_sso_auto_login():
                # SSO 自动登录失败，尝试从浏览器读取现有 cookies
                if not self._fallback_read_browser_cookies():
                    return None, None

            # 步骤 2：自动提取 API token
            logging.info(_('步骤 2：使用 Playwright 自动获取 API token...', 'Step 2: Using Playwright to get the API token automatically...'))
            token, private_token = self._extract_api_token()

            if token and private_token:
                Log.success(_('✅ 成功自动获取 API token！', '✅ Successfully obtained the API token automatically!'))
                Log.success(_('🎉 完全自动化完成，无需任何手动操作！', '🎉 Fully automated flow completed; no manual action needed!'))
                return token, private_token
            else:
                logging.warning(_('⚠️  自动提取 token 失败', '⚠️  Automatic token extraction failed'))
                return None, None

        except Exception as e:
            logging.error(_('❌ 自动化 SSO 流程出错: {error}', '❌ Automated SSO flow failed: {error}', error=e))
            return None, None

    def _perform_sso_auto_login(self) -> bool:
        """
        执行 SSO 自动登录

        返回: True 成功，False 失败
        """
        try:
            logging.info(_('步骤 1：使用 SSO 自动登录获取 cookies...', 'Step 1: Using SSO auto-login to get cookies...'))
            logging.info(
                _(
                    '   （从 {browser} 浏览器读取 SSO cookies，并在 Playwright 中恢复 Moodle 登录状态）',
                    '   (Reading SSO cookies from {browser} and restoring Moodle login state in Playwright)',
                    browser=self.preferred_browser,
                )
            )

            use_headless = _should_use_headless_sso()

            if not use_headless:
                logging.info('')
                logging.info(_('🌐 已启用有头模式（Headful Mode）', '🌐 Headful Mode is enabled'))
                logging.info(_('   - 浏览器窗口将可见，你可以手动操作', '   - The browser window will be visible, and you can interact with it manually'))
                logging.info(_('   - 适用于多账号选择、验证码输入等场景', '   - Useful for account selection, CAPTCHA, MFA, and similar steps'))
                logging.info(_('   - 如需无头模式，可设置 MOODLE_DL_HEADLESS=1（或兼容写法 MOODLE_DL_HEADFUL=0）', '   - To use headless mode, set MOODLE_DL_HEADLESS=1 (or MOODLE_DL_HEADFUL=0)'))
                logging.info('')
            else:
                logging.info(_('🌐 已启用无头模式（Headless Mode）', '🌐 Headless Mode is enabled'))
                logging.info(_('   - 不会显示浏览器窗口', '   - No browser window will be shown'))
                logging.info(_('   - 如遇账号选择、MFA 或重新授权，请改用默认有头模式', '   - If account selection, MFA, or reauthorization is needed, use the default headful mode'))

            from moodle_dl.auto_sso_login import auto_login_with_sso_sync

            sso_login_success = auto_login_with_sso_sync(
                moodle_domain=self.moodle_url.domain,
                cookies_path=self._cookies_path,
                preferred_browser=self.preferred_browser,
                headless=use_headless,
                timeout=60000,
                auth_manager=self.config.get_auth_manager()
            )

            if sso_login_success:
                Log.success(_('✅ SSO 自动登录成功！已获取新的 cookies', '✅ SSO auto-login succeeded. New cookies were obtained.'))
                return True
            else:
                Log.warning(_('⚠️  SSO 自动登录失败', '⚠️  SSO auto-login failed'))
                return False

        except Exception as e:
            logging.error(_('❌ SSO 自动登录出错: {error}', '❌ SSO auto-login error: {error}', error=e))
            return False

    def _fallback_read_browser_cookies(self) -> bool:
        """
        回退：从浏览器读取现有 cookies

        当 SSO 自动登录失败时调用此方法，尝试从浏览器读取已存在的 cookies
        
        **v2: 完全数据库化**：浏览器 → 数据库（永不出现 cookie txt）

        返回: True 成功，False 失败
        """
        if not self._export_module:
            logging.warning(_('⚠️  export_module 未加载，无法读取浏览器 cookies', '⚠️  export_module is not loaded; cannot read browser cookies'))
            return False

        try:
            logging.info(_('尝试从浏览器读取现有 cookies（回退方案）...', 'Trying to read existing cookies from the browser (fallback)...'))
            logging.info(_('  💡 v2: 直接存入数据库（无需临时文件）', '  💡 v2: saving directly to the database (no temporary file)'))

            # v2: 直接从浏览器获取 cookies 列表
            cookies_list = self._export_module.get_cookies_from_browser(
                domain=self.moodle_url.domain,
                browser_name=self.preferred_browser,
            )

            if cookies_list:
                # 转换为 Playwright 格式
                cookies = []
                for cookie in cookies_list:
                    cookies.append({
                        'name': cookie.name,
                        'value': cookie.value,
                        'domain': cookie.domain,
                        'path': cookie.path or '/',
                        'expires': cookie.expires if cookie.expires else 0,
                        'secure': cookie.secure,
                        'httponly': bool(cookie.has_nonstandard_attr('HttpOnly')),
                        'samesite': cookie.get_nonstandard_attr('SameSite', 'Lax')
                    })

                # ✅ 直接保存到数据库（无文件中转）
                auth_manager = self.config.get_auth_manager()
                session_id = auth_manager.save_sso_cookies(cookies)

                if session_id:
                    Log.success(_('✅ 从浏览器成功读取 cookies', '✅ Successfully read cookies from the browser'))
                    Log.success(_('✅ Cookies 已直接保存到数据库: {session_id}', '✅ Cookies were saved directly to the database: {session_id}', session_id=session_id))
                    logging.info(_('   共 {count} 个 cookies', '   {count} cookies in total', count=len(cookies)))
                    return True
                else:
                    logging.error(_('❌ Cookies 保存到数据库失败', '❌ Failed to save cookies to the database'))
                    return False
            else:
                Log.warning(_('⚠️  从浏览器读取 cookies 失败', '⚠️  Failed to read cookies from the browser'))
                return False

        except Exception as e:
            logging.error(_('❌ 从浏览器读取 cookies 时出错: {error}', '❌ Error while reading cookies from the browser: {error}', error=e))
            import traceback
            traceback.print_exc()
            return False

    def _extract_api_token(self) -> Tuple[Optional[str], Optional[str]]:
        """
        提取 API token

        使用 Playwright 从数据库的 cookies 中提取 API token
        
        **v2: 直接从数据库读取 cookies，无需文件**

        返回: (token, private_token) 或 (None, None) 表示失败
        """
        if not self._export_module:
            logging.error(_('❌ 缺少必要的模块', '❌ Required module is missing'))
            return None, None

        try:
            # v2: 从数据库获取最新的 cookie_batch 会话
            logging.debug(_('🔍 [Token提取] 开始从数据库获取 cookie_batch 会话...', '🔍 [Token extraction] Looking up cookie_batch session in the database...'))
            auth_manager = self.config.get_auth_manager()
            session = auth_manager.get_valid_session(session_type='cookie_batch')

            if not session:
                logging.error(_('❌ [Token提取] 数据库中没有有效的 cookies 会话', '❌ [Token extraction] No valid cookies session found in the database'))
                logging.debug(_('🔍 [Token提取] 尝试查询所有 cookie_batch 会话...', '🔍 [Token extraction] Querying all cookie_batch sessions...'))
                all_sessions = auth_manager.get_all_sessions(session_type='cookie_batch')
                logging.debug(_('🔍 [Token提取] 找到 {count} 个 cookie_batch 会话', '🔍 [Token extraction] Found {count} cookie_batch session(s)', count=len(all_sessions)))
                return None, None

            logging.debug(_('🔍 [Token提取] 找到有效会话: session_id={session_id}, created_at={created_at}', '🔍 [Token extraction] Found valid session: session_id={session_id}, created_at={created_at}', session_id=session.get("session_id"), created_at=session.get("created_at")))

            # 从数据库获取 cookies
            logging.debug(_('🔍 [Token提取] 从数据库加载 cookies (session_id={session_id})...', '🔍 [Token extraction] Loading cookies from database (session_id={session_id})...', session_id=session["session_id"]))
            cookies = auth_manager.get_session_cookies(session['session_id'])

            if not cookies:
                logging.error(_('❌ [Token提取] 数据库中没有 cookies', '❌ [Token extraction] No cookies found in the database'))
                logging.debug(_('🔍 [Token提取] session_id={session_id} 没有关联的 cookies', '🔍 [Token extraction] session_id={session_id} has no associated cookies', session_id=session["session_id"]))
                return None, None

            logging.info(_('📦 [Token提取] 从数据库加载 {count} 个 cookies', '📦 [Token extraction] Loaded {count} cookies from the database', count=len(cookies)))
            
            # 详细记录 Cookie 信息
            logging.debug(_('🔍 [Token提取] Cookie 详情:', '🔍 [Token extraction] Cookie details:'))
            moodle_session_count = sum(1 for c in cookies if c.get('name') == 'MoodleSession')
            logging.debug(f'  - MoodleSession cookies: {moodle_session_count}')
            logging.debug(_('  - 总 cookies 数: {count}', '  - Total cookies: {count}', count=len(cookies)))
            if cookies:
                sample_cookie = cookies[0]
                logging.debug(_('  - Cookie 示例: name={name}, domain={domain}, secure={secure}', '  - Cookie example: name={name}, domain={domain}, secure={secure}', name=sample_cookie.get("name"), domain=sample_cookie.get("domain"), secure=sample_cookie.get("secure")))

            # 使用新的 API：直接传入 cookies 列表
            logging.debug(_('🔍 [Token提取] 调用 Playwright 提取 token (domain={domain})...', '🔍 [Token extraction] Calling Playwright to extract token (domain={domain})...', domain=self.moodle_url.domain))
            token, private_token = self._export_module.extract_api_token_with_playwright_from_cookies(
                self.moodle_url.domain, cookies
            )

            if token and private_token:
                logging.info(_('✅ [Token提取] 成功提取 API token（从数据库 cookies）', '✅ [Token extraction] Successfully extracted API token (from database cookies)'))
                logging.debug(_('🔍 [Token提取] Token 长度: {token_len}, Private token 长度: {private_len}', '🔍 [Token extraction] Token length: {token_len}, private token length: {private_len}', token_len=len(token), private_len=len(private_token)))
                return token, private_token
            else:
                logging.warning(_('⚠️  [Token提取] 未能成功提取 API token', '⚠️  [Token extraction] Failed to extract API token'))
                logging.debug(
                    _(
                        '🔍 [Token提取] Playwright 返回: token={token_status}, private_token={private_status}',
                        '🔍 [Token extraction] Playwright returned: token={token_status}, private_token={private_status}',
                        token_status=_('有值', 'present') if token else 'None',
                        private_status=_('有值', 'present') if private_token else 'None',
                    )
                )
                return None, None

        except Exception as e:
            logging.error(_('❌ [Token提取] 提取 API token 时出错: {error}', '❌ [Token extraction] Error while extracting API token: {error}', error=e))
            logging.debug(_('🔍 [Token提取] 错误类型: {error_type}', '🔍 [Token extraction] Error type: {error_type}', error_type=type(e).__name__))
            import traceback
            logging.debug(_('🔍 [Token提取] 完整错误堆栈:\n{traceback}', '🔍 [Token extraction] Full traceback:\n{traceback}', traceback=traceback.format_exc()))
            traceback.print_exc()
            return None, None

    def _get_manual_token(self) -> Tuple[Optional[str], Optional[str]]:
        """
        手动获取 token - 作为最后的回退方案

        用户手动操作浏览器并复制 token URL

        返回: (token, private_token) 或 (None, None) 表示失败
        """
        try:
            SSOReferenceHelper.show_manual_token_help(self.moodle_url)

            token_address = input(_('然后在此处插入链接地址:   ', 'Paste the copied link address here:   '))

            if not token_address:
                logging.error(_('❌ token 地址为空', '❌ Token address is empty'))
                return None, None

            # 解析 token
            token_result = MoodleService.extract_token(token_address)
            
            if token_result is None:
                Log.error(_('❌ 无效的 token URL', '❌ Invalid token URL'))
                logging.error(_('无法从 URL 提取 token: {url}', 'Unable to extract token from URL: {url}', url=token_address))
                return None, None
            
            token, private_token = token_result

            if not token:
                Log.error(_('❌ 无效的 token URL', '❌ Invalid token URL'))
                logging.error(_('无法从 URL 提取 token: {url}', 'Unable to extract token from URL: {url}', url=token_address))
                return None, None

            logging.info(_('✅ 成功获取手动 token', '✅ Manual token acquired successfully'))
            return token, private_token

        except Exception as e:
            logging.error(_('❌ 手动获取 token 时出错: {error}', '❌ Error while getting manual token: {error}', error=e))
            return None, None
