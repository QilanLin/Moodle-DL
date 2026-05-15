# -*- coding: utf-8 -*-
"""
Moodle 配置向导 - 交互式初始化

使用新的认证器体系（BaseAuthenticator）来处理所有 token 获取流程
"""

import sys
import logging
from getpass import getpass

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.moodle_service import MoodleService
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import MoodleDlOpts, MoodleURL
from moodle_dl.utils import Log

# 导入新的认证器体系
from moodle_dl.cli.authenticators import (
    NormalAuthenticator,
    SSOAuthenticator,
    AuthenticationError,
    ConfigurationTransactionError,
)
from moodle_dl.cli.localization import tr as _


class MoodleWizard:
    """
    Moodle 配置向导

    使用认证器体系处理 token 获取，提供统一的认证流程
    """

    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts):
        self.config = config
        self.opts = opts

    def interactively_acquire_token(self, use_stored_url: bool = False) -> str:
        """
        交互式获取 token - 新的统一入口

        工作流程：
        1. 获取 Moodle URL（如果需要）
        2. 根据 opts 选择认证器（SSO 或普通）
        3. 执行认证器的完整流程（前置配置 → 获取 token → 原子性提交）
        4. 返回 token

        参数:
            use_stored_url: 是否使用存储的 URL

        返回:
            token: 有效的 Moodle API token

        异常:
            AuthenticationError: 认证失败
            ConfigurationTransactionError: 配置提交失败
        """
        try:
            # 步骤 1：获取 Moodle URL
            logging.info(_('📋 获取 Moodle URL...', '📋 Getting Moodle URL...'))
            moodle_url = self.interactively_get_moodle_url(use_stored_url)

            # 步骤 2：选择认证器
            if self.opts.sso or self.opts.token is not None:
                logging.info(_('🔑 使用 SSO 认证器', '🔑 Using SSO authenticator'))
                authenticator = SSOAuthenticator(self.config, self.opts, moodle_url)
            else:
                logging.info(_('🔑 使用普通登录认证器', '🔑 Using normal login authenticator'))
                authenticator = NormalAuthenticator(self.config, self.opts, moodle_url)

            # 步骤 3：执行认证流程（包括前置配置、获取 token、原子性提交）
            logging.info(_('🚀 开始认证流程...', '🚀 Starting authentication flow...'))
            token = authenticator.execute()

            return token

        except AuthenticationError as e:
            logging.error(_('❌ 认证失败: {error}', '❌ Authentication failed: {error}', error=e))
            Log.error(_('认证失败: {error}', 'Authentication failed: {error}', error=e))
            raise
        except ConfigurationTransactionError as e:
            logging.error(_('❌ 配置提交失败: {error}', '❌ Failed to save configuration: {error}', error=e))
            Log.error(_('配置提交失败: {error}', 'Failed to save configuration: {error}', error=e))
            raise
        except Exception as e:
            logging.error(_('❌ 未预期的错误: {error}', '❌ Unexpected error: {error}', error=e))
            Log.error(_('未预期的错误: {error}', 'Unexpected error: {error}', error=e))
            raise

    def interactively_get_moodle_url(self, use_stored_url: bool) -> MoodleURL:
        if use_stored_url:
            return self.config.get_moodle_URL()

        from moodle_dl.config import normalize_moodle_url

        url_ok = False
        while not url_ok:
            url_ok = True
            moodle_url_input = input(
                _('Moodle 的 URL（可省略 http(s)://）:   ', 'Moodle URL (http(s):// may be omitted):   ')
            ).strip()

            # 规范化 URL（自动添加 https:// 如果缺少协议）
            moodle_url = normalize_moodle_url(moodle_url_input)

            use_http = False
            if moodle_url.startswith('http://'):
                Log.warning(
                    _(
                        '警告：你输入了不安全的 URL！你确定该 Moodle 无法通过 `https://` 访问吗？'
                        + '你的所有数据将以不安全的方式传输！如果你的 Moodle 可以通过 `https://` 访问，'
                        + '请使用 `https://` 重新运行该过程以保护你的数据。',
                        'Warning: you entered an insecure URL. Are you sure this Moodle cannot be reached via `https://`? '
                        + 'All your data will be transferred insecurely. If your Moodle is available via `https://`, '
                        + 'rerun this process with `https://` to protect your data.'
                    )
                )
                use_http = True

        moodle_domain, moodle_path = MoodleService.split_moodle_url(moodle_url)
        return MoodleURL(use_http, moodle_domain, moodle_path)

    # ==================== 废弃方法（保留向后兼容性）====================

    def interactively_acquire_normal_token(self, use_stored_url: bool = False) -> str:
        """
        DEPRECATED: 使用 interactively_acquire_token() 代替

        这个方法已被重构到 NormalAuthenticator 中
        保留此方法仅用于向后兼容性
        """
        logging.warning('⚠️  interactively_acquire_normal_token() 已废弃，请使用 interactively_acquire_token()')
        return self.interactively_acquire_token(use_stored_url=use_stored_url)

    # ==================== 原始实现（已移至 NormalAuthenticator）====================
    # 以下代码已被代理到 NormalAuthenticator，仅保留供参考

    def _deprecated_interactively_acquire_normal_token(self, use_stored_url: bool = False) -> str:
        """
        Walks the user through executing a login into the Moodle-System to get
        the Token and saves it.
        @return: The Token for Moodle.
        """

        automated = False
        automatic_run_once = False
        if self.opts.username is not None and self.opts.password is not None:
            automated = True

        if not automated:
            print('[以下凭据不会被保存，仅临时用于生成登录令牌。]')

        moodle_token = None
        while moodle_token is None and not automatic_run_once:
            moodle_url = self.interactively_get_moodle_url(use_stored_url)

            if automated:
                automatic_run_once = True

            if self.opts.username is not None:
                moodle_username = self.opts.username
            else:
                moodle_username = input('Moodle 用户名:   ')

            if self.opts.password is not None:
                moodle_password = self.opts.password
            else:
                moodle_password = getpass('Moodle 密码 [无输出显示]:   ')

            try:
                moodle_token, moodle_privatetoken = MoodleService(self.config, self.opts).obtain_login_token(
                    moodle_username, moodle_password, moodle_url
                )

            except RequestRejectedError as error:
                Log.error(f'登录失败！({error}) 请重试。')
            except (ValueError, RuntimeError) as error:
                Log.error(f'与 Moodle 系统通信时出错！({error}) 请重试。')
            except ConnectionError as error:
                Log.error(str(error))

        if automated is True and moodle_token is None:
            sys.exit(1)

        self.config.set_tokens(moodle_token, moodle_privatetoken)
        self.config.set_moodle_URL(moodle_url)

        Log.success('令牌已成功保存！')

        # 普通登录也需要导出浏览器cookies来下载受保护的内容
        print('')
        Log.info('💡 提示：某些内容需要浏览器cookies才能下载')
        Log.info('   例如：Kaltura视频、描述中的受保护链接等')
        print('')

        try:
            # 动态导入 export_browser_cookies
            import importlib.util
            import os

            script_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                'export_browser_cookies.py',
            )

            if os.path.exists(script_path):
                spec = importlib.util.spec_from_file_location("export_browser_cookies", script_path)
                export_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(export_module)

                # 获取cookies保存路径
                from moodle_dl.utils import PathTools as PT

                cookies_path = PT.get_cookies_path(self.config.get_misc_files_path())

                # 询问用户是否要导出cookies
                from moodle_dl.utils import Cutie

                should_export = Cutie.prompt_yes_or_no(
                    Log.blue_str('是否现在从浏览器导出cookies（推荐）？'), default_is_yes=True
                )

                if should_export:
                    Log.info('正在从浏览器导出cookies...')
                    success = export_module.export_cookies_interactive(
                        domain=moodle_url.domain,
                        output_file=cookies_path,
                        ask_browser=True,
                        auto_get_token=False,  # 已有token，不需要再获取
                    )

                    if success:
                        Log.success('✅ Cookies导出成功！')
                    else:
                        Log.warning('⚠️  Cookies导出失败，你可以稍后在配置步骤7重新导出')
                else:
                    Log.info('跳过cookies导出，你可以稍后在配置步骤7导出')
            else:
                Log.warning('⚠️  未找到export_browser_cookies.py')
                Log.info('   你可以稍后在配置步骤7导出cookies')

        except ImportError as e:
            Log.warning(f'⚠️  缺少依赖库: {e}')
            Log.info('   提示：运行 `pip install browser-cookie3`')
            Log.info('   你可以稍后在配置步骤7导出cookies')
        except Exception as e:
            Log.warning(f'⚠️  导出cookies出错: {e}')
            Log.info('   你可以稍后在配置步骤7导出cookies')

        print('')

        return moodle_token

    def interactively_acquire_sso_token(self, use_stored_url: bool = False) -> str:
        """
        DEPRECATED: 使用 interactively_acquire_token() 代替

        这个方法已被重构到 SSOAuthenticator 中
        保留此方法仅用于向后兼容性
        """
        logging.warning('⚠️  interactively_acquire_sso_token() 已废弃，请使用 interactively_acquire_token()')
        return self.interactively_acquire_token(use_stored_url=use_stored_url)
