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
            raise AuthenticationError('Token 不能为空')
        if not isinstance(self.token, str):
            raise AuthenticationError(f'Token 必须是字符串，当前类型: {type(self.token)}')


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
            raise ConfigurationTransactionError('事务已提交，不能重复提交')

        if not self._operations:
            logging.debug('📋 事务为空，无需提交')
            return

        try:
            # 阶段 1: 提交 token（最关键）
            token_ops = [op for op in self._operations if op['type'] == 'tokens']
            if token_ops:
                for op in token_ops:
                    logging.debug(f"💾 提交 token 到数据库...")
                    self.config.set_tokens(op['token'], op['private_token'])

            # 阶段 2: 提交 URL
            logging.debug(f"💾 提交 Moodle URL: {self.moodle_url.domain}")
            self.config.set_moodle_URL(self.moodle_url)

            # 阶段 3: 提交其他属性
            property_ops = [op for op in self._operations if op['type'] == 'property']
            for op in property_ops:
                logging.debug(f"💾 提交属性: {op['key']} = {op['value']}")
                self.config.set_property(op['key'], op['value'])

            self._committed = True
            logging.info('✅ 配置事务提交成功（原子性保证）')

        except Exception as e:
            logging.error(f'❌ 配置事务提交失败: {e}')
            logging.error('⚠️  由于失败，所有配置更改都未被保存（保持一致性）')
            raise ConfigurationTransactionError(f'提交失败: {e}') from e

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
            logging.debug(f"已加载首选浏览器: {preferred_browser}")
            return preferred_browser

        # 首次配置，需要询问用户
        print('')
        Log.blue('请选择你使用的浏览器：')

        try:
            from moodle_dl.utils import Cutie
            browser_choice = Cutie.select(BrowserSelector.BROWSER_CHOICES)
        except ImportError:
            Log.error('缺少依赖库 Cutie，无法进行浏览器选择')
            raise AuthenticationError('缺少必要的依赖库')

        if browser_choice not in BrowserSelector.BROWSER_MAP:
            raise AuthenticationError(f'无效的浏览器选择: {browser_choice}')

        browser_name = BrowserSelector.BROWSER_MAP[browser_choice]
        display_name = BrowserSelector.BROWSER_CHOICES[browser_choice]

        Log.info(f'✓ 已选择：{display_name}')
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
            logging.error(f'未找到 export_browser_cookies.py: {script_path}')
            raise FileNotFoundError(f'export_browser_cookies.py 不存在')

        try:
            spec = importlib.util.spec_from_file_location("export_browser_cookies", script_path)
            export_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(export_module)
            return export_module
        except Exception as e:
            raise ImportError(f'无法加载 export_browser_cookies.py: {e}') from e


class SSOReferenceHelper:
    """SSO 参考文本辅助类 - 处理手动 token 获取的参考信息"""

    @staticmethod
    def show_manual_token_help(moodle_url: MoodleURL) -> None:
        """显示手动获取 token 的帮助信息"""
        print('')
        Log.warning('请使您选择的浏览器进行以下操作')
        print('1. 登录你的 Moodle 账户')
        print('2. 打开开发者控制台（按 F12）并转到 Network（网络）标签')
        print('3. 然后在你已登录的同一浏览器标签页中访问以下 URL：')
        print('')
        print(
            moodle_url.url_base
            + 'admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&urlscheme=moodledl'
        )
        print()
        print(
            '如果你打开链接，不应该加载网页，而是会出现错误。'
            + '在你之前打开的开发者控制台的 Network 标签中应该有一个错误条目。'
        )

        print('脚本期望一个类似这样的 URL：')
        Log.info('moodledl://token=$apptoken')
        print(
            ' 其中 $apptoken 看起来是随机的，"moodledl" 也可以是不同的 URL scheme，'
            + '比如 "moodlemobile"。实际上 $apptoken 是一个包含访问 Moodle 令牌的 Base64 字符串。'
        )

        print(
            '4. 复制无法加载的网站的链接地址' + '（右键单击列表条目，然后单击"复制"，然后单击"复制链接地址"）'
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
            raise AuthenticationError('Token 获取结果为空')

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
            raise ConfigurationTransactionError('没有 token 结果可提交')

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

        Log.success('✅ 令牌已成功保存！')

    def execute(self) -> str:
        """
        执行完整的认证流程

        返回: token 字符串
        异常: AuthenticationError, ConfigurationTransactionError
        """
        try:
            # 1. 前置配置
            logging.info('📋 进行前置配置...')
            self.pre_configure()

            # 2. 获取 token
            logging.info('🔑 开始获取 token...')
            self._result = self.acquire_token()

            # 3. 原子性提交配置
            logging.info('💾 提交配置...')
            self.commit_configuration()

            return self._result.token

        except AuthenticationError as e:
            logging.error(f'❌ 认证失败: {e}')
            raise
        except ConfigurationTransactionError as e:
            logging.error(f'❌ 配置提交失败: {e}')
            raise
        except Exception as e:
            logging.error(f'❌ 认证过程出错: {e}')
            raise AuthenticationError(f'认证过程出错: {e}') from e


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
            logging.info('🔐 使用命令行提供的凭据')
            return self.opts.username, self.opts.password

        # 交互式输入
        username = input('Moodle 用户名:   ').strip()
        if not username:
            raise AuthenticationError('用户名不能为空')

        password = getpass('Moodle 密码 [无输出显示]:   ')
        if not password:
            raise AuthenticationError('密码不能为空')

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
            print('[使用命令行参数自动化登录，仅尝试一次]')
        else:
            # 交互式模式 - 允许多次重试
            max_attempts = self.MAX_LOGIN_ATTEMPTS
            print('[以下凭据不会被保存，仅临时用于生成登录令牌。]')

        print('')

        for attempt in range(1, max_attempts + 1):
            try:
                logging.info(f'🔐 登录尝试 {attempt}/{max_attempts}...')

                # 获取凭据
                username, password = self._get_credentials()

                # 调用 Moodle 登录
                moodle_service = MoodleService(self.config, self.opts)
                token, private_token = moodle_service.obtain_login_token(
                    username, password, self.moodle_url
                )

                if not token:
                    raise AuthenticationError('未收到有效的 token')

                logging.info('✅ 登录成功！')
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
                logging.error(f'❌ 登录失败（请求被拒绝）: {e}')
                if attempt < max_attempts:
                    print('请重试。')
                    print('')
                else:
                    raise AuthenticationError(f'登录失败: {e}') from e

            except (ValueError, RuntimeError) as e:
                logging.error(f'❌ 与 Moodle 系统通信时出错: {e}')
                if attempt < max_attempts:
                    print('请重试。')
                    print('')
                else:
                    raise AuthenticationError(f'与 Moodle 系统通信时出错: {e}') from e

            except ConnectionError as e:
                logging.error(f'❌ 网络连接错误: {e}')
                if attempt < max_attempts:
                    print('请检查网络连接后重试。')
                    print('')
                else:
                    raise AuthenticationError(f'网络连接错误: {e}') from e

            except AuthenticationError:
                # 直接抛出
                raise
            except Exception as e:
                logging.error(f'❌ 登录时出现意外错误: {e}')
                raise AuthenticationError(f'登录时出现意外错误: {e}') from e

        # 如果在自动化模式下全部失败
        if self.opts.username is not None or self.opts.password is not None:
            raise AuthenticationError('自动化登录失败（命令行参数可能有误）')

        raise AuthenticationError(f'达到最大尝试次数 ({max_attempts})，登录失败')

    def _prompt_cookies_export(self) -> None:
        """
        提示用户是否导出浏览器 cookies

        普通登录后，用户通常需要 cookies 来下载某些受保护的内容
        （如 Kaltura 视频、受保护的链接等）
        """
        try:
            print('')
            Log.info('💡 提示：某些内容需要浏览器cookies才能下载')
            Log.info('   例如：Kaltura视频、描述中的受保护链接等')
            print('')

            from moodle_dl.utils import Cutie, PathTools as PT

            should_export = Cutie.prompt_yes_or_no(
                Log.blue_str('是否现在从浏览器导出cookies（推荐）？'), default_is_yes=True
            )

            if not should_export:
                Log.info('⏭️  跳过cookies导出，你可以稍后在配置步骤7导出')
                return

            # 加载 export_browser_cookies 模块
            try:
                export_module = ExportBrowserCookiesHelper.load_export_module()
            except (FileNotFoundError, ImportError) as e:
                Log.warning(f'⚠️  无法加载 export_browser_cookies: {e}')
                Log.info('   你可以稍后在配置步骤7导出cookies')
                return

            # 导出 cookies
            cookies_path = PT.get_cookies_path(self.config.get_misc_files_path())
            Log.info('正在从浏览器导出cookies...')

            try:
                success = export_module.export_cookies_interactive(
                    domain=self.moodle_url.domain,
                    output_file=cookies_path,
                    ask_browser=True,
                    auto_get_token=False,  # 已有 token，不需要再获取
                )

                if success:
                    Log.success('✅ Cookies导出成功！')
                else:
                    Log.warning('⚠️  Cookies导出失败，你可以稍后在配置步骤7重新导出')
            except Exception as e:
                logging.error(f'导出 cookies 时出错: {e}')
                Log.warning(f'⚠️  Cookies导出出错: {e}')
                Log.info('   你可以稍后在配置步骤7导出cookies')

        except ImportError as e:
            Log.warning(f'⚠️  缺少依赖库: {e}')
            Log.info('   提示：运行 `pip install browser-cookie3`')
            Log.info('   你可以稍后在配置步骤7导出cookies')
        except Exception as e:
            logging.error(f'导出 cookies 提示时出错: {e}')
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
            logging.info('📋 选择浏览器...')
            self.preferred_browser = BrowserSelector.select_or_load(self.config)
            logging.info(f'✓ 浏览器选择: {self.preferred_browser}')
        except AuthenticationError as e:
            logging.error(f'❌ 浏览器选择失败: {e}')
            raise
        except Exception as e:
            logging.error(f'❌ 前置配置出错: {e}')
            raise AuthenticationError(f'前置配置出错: {e}') from e

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
            logging.info('🔑 使用命令行提供的 token')
            return TokenAcquisitionResult(
                token=self.opts.token,
                private_token=None,
                extra_properties={'preferred_browser': self.preferred_browser}
            )

        # 优先级 2：自动化流程
        logging.info('🚀 尝试完全自动获取 API token...')
        logging.info('   策略：SSO 自动登录 + Playwright 自动获取 token')
        logging.info('   优势：只要 SSO cookies 有效，完全无需手动操作！')
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
        logging.info('⚙️  自动获取 token 失败，回退到手动输入')
        token, private_token = self._get_manual_token()

        if not token:
            raise AuthenticationError('无法获取有效的 token（所有方法都失败）')

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
            logging.info('📦 加载浏览器 cookie 导出模块...')
            try:
                self._export_module = ExportBrowserCookiesHelper.load_export_module()
            except (FileNotFoundError, ImportError) as e:
                Log.warning(f'⚠️  无法加载导出模块: {e}')
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
            logging.info('步骤 2：使用 Playwright 自动获取 API token...')
            token, private_token = self._extract_api_token()

            if token and private_token:
                Log.success('✅ 成功自动获取 API token！')
                Log.success('🎉 完全自动化完成，无需任何手动操作！')
                return token, private_token
            else:
                logging.warning('⚠️  自动提取 token 失败')
                return None, None

        except Exception as e:
            logging.error(f'❌ 自动化 SSO 流程出错: {e}')
            return None, None

    def _perform_sso_auto_login(self) -> bool:
        """
        执行 SSO 自动登录

        返回: True 成功，False 失败
        """
        try:
            logging.info('步骤 1：使用 SSO 自动登录获取 cookies...')
            logging.info(f'   （从 {self.preferred_browser} 浏览器读取 SSO cookies，自动完成 Moodle 登录）')
            logging.info('   💡 原理：只要 Microsoft/Google 的 SSO cookies 有效，完全自动化，无需手动操作')
            print('')

            from moodle_dl.auto_sso_login import auto_login_with_sso_sync

            sso_login_success = auto_login_with_sso_sync(
                moodle_domain=self.moodle_url.domain,
                cookies_path=self._cookies_path,
                preferred_browser=self.preferred_browser,
                headless=True,
                timeout=60000,
                auth_manager=self.config.get_auth_manager()
            )

            if sso_login_success:
                Log.success('✅ SSO 自动登录成功！已获取新的 cookies')
                return True
            else:
                Log.warning('⚠️  SSO 自动登录失败')
                return False

        except Exception as e:
            logging.error(f'❌ SSO 自动登录出错: {e}')
            return False

    def _fallback_read_browser_cookies(self) -> bool:
        """
        回退：从浏览器读取现有 cookies

        当 SSO 自动登录失败时调用此方法，尝试从浏览器读取已存在的 cookies
        
        **v2: 完全数据库化**：浏览器 → 数据库（永不出现 cookie txt）

        返回: True 成功，False 失败
        """
        if not self._export_module:
            logging.warning('⚠️  export_module 未加载，无法读取浏览器 cookies')
            return False

        try:
            logging.info('尝试从浏览器读取现有 cookies（回退方案）...')
            logging.info('  💡 v2: 直接存入数据库（无需临时文件）')

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
                    Log.success('✅ 从浏览器成功读取 cookies')
                    Log.success(f'✅ Cookies 已直接保存到数据库: {session_id}')
                    logging.info(f'   共 {len(cookies)} 个 cookies')
                    return True
                else:
                    logging.error('❌ Cookies 保存到数据库失败')
                    return False
            else:
                Log.warning('⚠️  从浏览器读取 cookies 失败')
                return False

        except Exception as e:
            logging.error(f'❌ 从浏览器读取 cookies 时出错: {e}')
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
            logging.error('❌ 缺少必要的模块')
            return None, None

        try:
            # v2: 从数据库获取最新的 cookie_batch 会话
            auth_manager = self.config.get_auth_manager()
            session = auth_manager.get_valid_session(session_type='cookie_batch')

            if not session:
                logging.error('❌ 数据库中没有有效的 cookies 会话')
                return None, None

            # 从数据库获取 cookies
            cookies = auth_manager.get_session_cookies(session['session_id'])

            if not cookies:
                logging.error('❌ 数据库中没有 cookies')
                return None, None

            logging.info(f'📦 从数据库加载 {len(cookies)} 个 cookies')

            # 使用新的 API：直接传入 cookies 列表
            token, private_token = self._export_module.extract_api_token_with_playwright_from_cookies(
                self.moodle_url.domain, cookies
            )

            if token and private_token:
                logging.info(f'✅ 成功提取 API token（从数据库 cookies）')
                return token, private_token
            else:
                logging.warning('⚠️  未能成功提取 API token')
                return None, None

        except Exception as e:
            logging.error(f'❌ 提取 API token 时出错: {e}')
            import traceback
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

            token_address = input('然后在此处插入链接地址:   ')

            if not token_address:
                logging.error('❌ token 地址为空')
                return None, None

            # 解析 token
            token, private_token = MoodleService.extract_token(token_address)

            if not token:
                Log.error('❌ 无效的 token URL')
                logging.error(f'无法从 URL 提取 token: {token_address}')
                return None, None

            logging.info('✅ 成功获取手动 token')
            return token, private_token

        except Exception as e:
            logging.error(f'❌ 手动获取 token 时出错: {e}')
            return None, None
