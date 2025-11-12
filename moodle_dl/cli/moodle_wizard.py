import sys
from getpass import getpass

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.moodle_service import MoodleService
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import MoodleDlOpts, MoodleURL
from moodle_dl.utils import Log


class MoodleWizard:
    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts):
        self.config = config
        self.opts = opts

    def interactively_acquire_token(self, use_stored_url: bool = False) -> str:
        if self.opts.sso or self.opts.token is not None:
            self.interactively_acquire_sso_token(use_stored_url=use_stored_url)
        else:
            self.interactively_acquire_normal_token(use_stored_url=use_stored_url)

    def interactively_get_moodle_url(self, use_stored_url: bool) -> MoodleURL:
        if use_stored_url:
            return self.config.get_moodle_URL()

        url_ok = False
        while not url_ok:
            url_ok = True
            moodle_url = input('Moodle 的 URL:   ')

            use_http = False
            if moodle_url.startswith('http://'):
                Log.warning(
                    '警告：你输入了不安全的 URL！你确定该 Moodle 无法通过 `https://` 访问吗？'
                    + '你的所有数据将以不安全的方式传输！如果你的 Moodle 可以通过 `https://` 访问，'
                    + '请使用 `https://` 重新运行该过程以保护你的数据。'
                )
                use_http = True
            elif not moodle_url.startswith('https://'):
                Log.error('你的 Moodle URL 必须以 `https://` 开头')
                url_ok = False

        moodle_domain, moodle_path = MoodleService.split_moodle_url(moodle_url)
        return MoodleURL(use_http, moodle_domain, moodle_path)

    def interactively_acquire_normal_token(self, use_stored_url: bool = False) -> str:
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
        Walks the user through the receiving of a SSO token
        @return: The Token for Moodle.
        """

        moodle_url = self.interactively_get_moodle_url(use_stored_url)

        if self.opts.token is not None:
            moodle_token = self.opts.token
            moodle_privatetoken = None
        else:
            # 🚀 完全自动化的 token 获取流程（使用 SSO 自动登录）
            moodle_token = None
            moodle_privatetoken = None

            print('')
            Log.info('🚀 尝试完全自动获取 API token...')
            Log.info('   策略：SSO 自动登录 + Playwright 自动获取 token')
            Log.info('   优势：只要 SSO cookies 有效，完全无需手动操作！')
            print('')

            try:
                # 动态导入必要模块
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

                    # 🔄 步骤0：先让用户选择浏览器（如果还没配置）
                    preferred_browser = self.config.get_property_or('preferred_browser', None)

                    if not preferred_browser:
                        # 初次配置，需要询问用户选择浏览器
                        from moodle_dl.utils import Cutie

                        print('')
                        Log.blue('请选择你使用的浏览器：')
                        browser_choices = [
                            'Firefox',
                            'Chrome',
                            'Edge',
                            'Safari',
                            'Brave',
                            'Arc',
                            'Zen Browser',
                            'Waterfox',
                        ]
                        browser_choice = Cutie.select(browser_choices)

                        browser_map = {
                            0: 'firefox',
                            1: 'chrome',
                            2: 'edge',
                            3: 'safari',
                            4: 'brave',
                            5: 'arc',
                            6: 'zen',
                            7: 'waterfox',
                        }
                        preferred_browser = browser_map[browser_choice]
                        Log.info(f'✓ 已选择：{browser_choices[browser_choice]}')
                        print('')

                    # 🔄 步骤1：使用 SSO 自动登录获取/刷新 cookies
                    Log.info('步骤1：使用 SSO 自动登录获取 cookies...')
                    Log.info(f'   （从 {preferred_browser} 浏览器读取 SSO cookies，自动完成 Moodle 登录）')
                    Log.info('   💡 原理：只要 Microsoft/Google 的 SSO cookies 有效，完全自动化，无需手动操作')
                    print('')

                    # 使用自动 SSO 登录
                    from moodle_dl.auto_sso_login import auto_login_with_sso_sync

                    # 使用 Playwright 无头浏览器（后台运行，完全自动化）
                    # 原理：完整迁移用户浏览器的所有 cookies 到 Playwright
                    # 只要 SSO cookies 有效，Playwright 会自动完成整个 SSO 登录流程
                    # 无需弹出窗口，无需用户旁观或手动操作
                    sso_login_success = auto_login_with_sso_sync(
                        moodle_domain=moodle_url.domain,
                        cookies_path=cookies_path,
                        preferred_browser=preferred_browser,
                        headless=True,   # 使用无头模式（无GUI，后台运行，完全自动化）
                        timeout=60000    # 60秒超时（足够 SSO 重定向完成）
                    )

                    # 保存首选浏览器到配置文件（无论成功还是失败都保存）
                    self.config.set_property('preferred_browser', preferred_browser)

                    if sso_login_success:
                        Log.success('✅ SSO 自动登录成功！已获取新的 cookies')
                        Log.info(f'✅ 已保存浏览器选择（{preferred_browser}），将用于自动刷新cookies')
                    else:
                        Log.warning('⚠️  SSO 自动登录失败，尝试从浏览器读取现有 cookies...')

                        # 回退：从浏览器读取 cookies（使用用户已选择的浏览器）
                        success = export_module.export_cookies_from_browser(
                            domain=moodle_url.domain,
                            output_file=cookies_path,
                            browser_name=preferred_browser,
                        )

                        if not success:
                            Log.warning('⚠️  从浏览器读取 cookies 也失败了')
                            Log.info('   将使用手动方式获取 token...')
                            raise Exception('Cookie acquisition failed')

                    # 🔑 步骤2：使用 Playwright 自动获取 API token
                    print('')
                    Log.info('步骤2：使用 Playwright 自动获取 API token...')

                    moodle_token, moodle_privatetoken = export_module.extract_api_token_with_playwright(
                        moodle_url.domain, cookies_path
                    )

                    if moodle_token and moodle_privatetoken:
                        Log.success('✅ 成功自动获取 API token！')
                        Log.success('🎉 完全自动化完成，无需任何手动操作！')
                    else:
                        Log.warning('⚠️  自动获取 token 失败')
                        Log.info('   将使用手动方式获取 token...')
                else:
                    Log.warning('⚠️  未找到 export_browser_cookies.py，将使用手动方式获取 token...')

            except ImportError as e:
                Log.warning(f'⚠️  缺少依赖库: {e}')
                Log.info('   提示：运行 `pip install browser-cookie3 playwright && playwright install firefox`')
                Log.info('   现在将使用手动方式获取 token...')
            except Exception as e:
                Log.warning(f'⚠️  自动获取 token 出错: {e}')
                Log.info('   将使用手动方式获取 token...')

            # 如果自动获取失败，使用手动方式
            if moodle_token is None or moodle_privatetoken is None:
                print('')
                Log.warning('请使您选择的浏览器进行以下操作')
                print('1. 登录你的 Moodle 账户')
                print('2. 打开开发者控制台（按 F12）并转到 Network（网络）标签')
                print('3. 然后在你已登录的同一浏览器标签页中访问以下 URL：')

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

                token_address = input('然后在此处插入链接地址:   ')

                moodle_token, moodle_privatetoken = MoodleService.extract_token(token_address)
                if moodle_token is None:
                    raise ValueError('无效的 URL！')

        self.config.set_tokens(moodle_token, moodle_privatetoken)
        self.config.set_moodle_URL(moodle_url)

        Log.success('令牌已成功保存！')

        return moodle_token
