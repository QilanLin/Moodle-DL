"""
统一的Cookies管理模块

提供全局的cookies刷新、验证和管理功能，遵循DRY原则。
所有需要使用browser cookies的模块都应该使用这里的函数。
"""

import importlib.util
import os
from typing import Optional, Tuple

from moodle_dl.utils import Log


class CookieManager:
    """
    Cookies管理器 - 统一处理cookies的导出、刷新和验证
    """

    def __init__(self, config, moodle_domain: str, cookies_path: str):
        """
        初始化CookieManager

        @param config: moodle-dl配置对象
        @param moodle_domain: Moodle域名 (如 keats.kcl.ac.uk)
        @param cookies_path: cookies文件路径
        """
        self.config = config
        self.moodle_domain = moodle_domain
        self.cookies_path = cookies_path
        self._export_module = None

    def _load_export_module(self):
        """加载export_browser_cookies模块（懒加载）"""
        if self._export_module is not None:
            return self._export_module

        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'export_browser_cookies.py',
        )

        if not os.path.exists(script_path):
            Log.error(f'❌ 无法找到export_browser_cookies.py: {script_path}')
            return None

        try:
            spec = importlib.util.spec_from_file_location("export_browser_cookies", script_path)
            self._export_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self._export_module)
            return self._export_module
        except Exception as e:
            Log.error(f'❌ 加载export_browser_cookies模块失败: {e}')
            return None

    def refresh_cookies(self, auto_get_token: bool = False, use_auto_sso: bool = True) -> bool:
        """
        自动刷新cookies - 智能选择刷新方式

        刷新策略：
        1. 优先使用自动 SSO 登录（use_auto_sso=True时）
           - 从浏览器读取 SSO cookies（Microsoft/Google等）
           - 使用 Playwright 有头浏览器自动完成 SSO 登录
           - 获取新的 MoodleSession cookie
           - 完全自动化，无需用户干预

        2. 回退到从浏览器导出（如果自动 SSO 失败）
           - 直接读取浏览器 cookie 数据库
           - 需要用户在浏览器中保持登录状态

        @param auto_get_token: 是否同时刷新API token (默认False，仅刷新cookies)
        @param use_auto_sso: 是否使用自动 SSO 登录（默认True）
        @return: 成功返回True，失败返回False
        """
        Log.warning('🔄 检测到cookies已过期，尝试自动刷新...')

        # 检查用户配置的首选浏览器
        # 使用 get_property_or() 避免 KeyError
        preferred_browser = self.config.get_property_or('preferred_browser', 'firefox')

        # 1. 优先尝试自动 SSO 登录（完全自动化）
        # 但需要检查是否已经在事件循环中（如在 Playwright 上下文中）
        if use_auto_sso:
            # 检测是否已经在事件循环中运行
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                # 已经在事件循环中（如 Playwright），使用 async 版本
                Log.info('💡 检测到正在运行中的事件循环')
                Log.info('🚀 使用异步 SSO 自动登录刷新cookies...')
                Log.info('   （只要 SSO cookies 有效，无需手动操作）')

                try:
                    # 在已运行的事件循环中，需要使用 asyncio.create_task
                    # 但由于当前函数不是 async，我们需要用线程池来运行
                    import concurrent.futures
                    from moodle_dl.auto_sso_login import auto_login_with_sso_sync

                    # 使用线程池在后台运行同步版本（它会创建新的事件循环）
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(
                            auto_login_with_sso_sync,
                            self.moodle_domain,
                            self.cookies_path,
                            preferred_browser,
                            True  # headless
                        )
                        success = future.result()

                    if success:
                        Log.success('✅ SSO 自动登录成功！Cookies已刷新')
                        return True
                    else:
                        Log.info('💡 SSO 登录失败，尝试从浏览器读取cookies...')

                except Exception as e:
                    Log.debug(f'SSO 登录出错: {e}')
                    Log.info('💡 回退到从浏览器读取cookies...')

            except RuntimeError:
                # 没有运行中的事件循环，可以使用 SSO 自动登录
                Log.info('🚀 尝试使用自动 SSO 登录刷新cookies...')
                Log.info('   （只要 SSO cookies 有效，无需手动操作）')

                try:
                    from moodle_dl.auto_sso_login import auto_login_with_sso_sync

                    success = auto_login_with_sso_sync(
                        moodle_domain=self.moodle_domain,
                        cookies_path=self.cookies_path,
                        preferred_browser=preferred_browser,
                        headless=True  # 使用无头模式（后台运行）
                    )

                    if success:
                        Log.success('✅ 自动 SSO 登录成功！Cookies已刷新')
                        return True
                    else:
                        Log.info('💡 自动 SSO 登录失败，尝试从浏览器读取cookies...')

                except Exception as e:
                    Log.debug(f'自动 SSO 登录出错: {e}')
                    Log.info('💡 回退到从浏览器读取cookies...')

        # 2. 回退：从浏览器读取 cookies
        export_module = self._load_export_module()
        if not export_module:
            self._show_manual_refresh_instructions()
            return False

        try:
            if preferred_browser:
                Log.info(f'📤 正在从{preferred_browser}导出cookies...')
                success = export_module.export_cookies_from_browser(
                    domain=self.moodle_domain,
                    output_file=self.cookies_path,
                    browser_name=preferred_browser
                )
            else:
                Log.info('📤 正在从浏览器自动导出cookies...')
                Log.info('   💡 提示：在config.json中设置 "preferred_browser" 可加快导出速度')
                success = export_module.export_cookies_interactive(
                    domain=self.moodle_domain,
                    output_file=self.cookies_path,
                    ask_browser=False,
                    auto_get_token=auto_get_token,
                )

            if success:
                Log.success('✅ Cookies自动刷新成功！')
                return True
            else:
                Log.warning('⚠️  自动导出cookies失败')
                self._show_manual_refresh_instructions()
                return False

        except Exception as e:
            Log.error(f'❌ 刷新cookies时出错: {e}')
            self._show_manual_refresh_instructions()
            return False

    def _show_manual_refresh_instructions(self):
        """显示手动刷新cookies的说明"""
        Log.info('')
        Log.info('请手动重新导出cookies：')
        Log.info('  方法1: 运行 moodle-dl --config（选择步骤7重新导出cookies）')
        Log.info('  方法2: 在config.json中添加 "preferred_browser": "firefox"（或你的浏览器）')
        Log.info('')

    @staticmethod
    def is_cookie_expired_response(url: str, content: str = '') -> bool:
        """
        检测响应是否表明cookies已过期

        通用的检测函数，可以识别多种cookies过期的特征：
        - 重定向到enrollment页面
        - 重定向到登录页面
        - "guest user" 错误
        - 需要authentication的错误

        @param url: 响应的最终URL
        @param content: 响应的HTML内容（可选）
        @return: 如果检测到cookies过期返回True
        """
        # 检查URL特征
        expired_url_patterns = [
            'enrol/index.php',
            '/login/',
            '/auth/',
        ]

        for pattern in expired_url_patterns:
            if pattern in url.lower():
                return True

        # 检查内容特征（如果提供了content）
        if content:
            expired_content_patterns = [
                'guest user',
                'not logged in',
                'login required',
                'authentication required',
                'session expired',
            ]

            content_lower = content.lower()
            for pattern in expired_content_patterns:
                if pattern in content_lower:
                    return True

        return False


def create_cookie_manager_from_client(client, config) -> CookieManager:
    """
    从RequestHelper客户端创建CookieManager

    便捷函数，用于从现有的moodle client创建cookie manager。

    @param client: RequestHelper实例
    @param config: 配置对象
    @return: CookieManager实例
    """
    from moodle_dl.utils import PathTools as PT

    cookies_path = PT.get_cookies_path(config.get_misc_files_path())
    moodle_domain = client.moodle_url.domain

    return CookieManager(config, moodle_domain, cookies_path)


def convert_netscape_cookies_to_playwright(cookies_path: str) -> list:
    """
    将Netscape格式的cookies转换为Playwright格式

    通用的转换函数，所有使用Playwright的模块都应该使用这个函数。

    @param cookies_path: Netscape格式cookies文件路径
    @return: Playwright格式的cookies列表
    """
    try:
        import http.cookiejar

        cookie_jar = http.cookiejar.MozillaCookieJar(cookies_path)
        cookie_jar.load(ignore_discard=True, ignore_expires=True)

        playwright_cookies = []
        for cookie in cookie_jar:
            # 处理expires字段
            expires_value = -1
            if cookie.expires is not None and cookie.expires > 0:
                if cookie.expires > 10000000000:
                    expires_value = int(cookie.expires / 1000)
                else:
                    expires_value = int(cookie.expires)

            # 🔧 Netscape格式不包含HttpOnly字段，但MoodleSession必须是httpOnly=True
            # 所以我们需要特殊处理MoodleSession cookie
            is_http_only = bool(cookie.has_nonstandard_attr('HttpOnly'))
            if cookie.name == 'MoodleSession' and not is_http_only:
                # MoodleSession必须是httpOnly cookie
                is_http_only = True

            # 🔧 确保secure字段是布尔值（Firefox可能返回0或1）
            is_secure = bool(cookie.secure) if cookie.secure is not None else False

            playwright_cookie = {
                'name': cookie.name,
                'value': cookie.value,
                'domain': cookie.domain,
                'path': cookie.path,
                'expires': expires_value,
                'httpOnly': is_http_only,
                'secure': is_secure,
                'sameSite': cookie.get_nonstandard_attr('SameSite', 'Lax') or 'Lax',
            }
            playwright_cookies.append(playwright_cookie)

        return playwright_cookies

    except Exception as e:
        Log.error(f'转换cookies格式失败: {e}')
        return []
