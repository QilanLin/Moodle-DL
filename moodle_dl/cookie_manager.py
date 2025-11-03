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

    def refresh_cookies(self, auto_get_token: bool = False) -> bool:
        """
        自动刷新cookies - 从浏览器重新导出

        这是一个通用函数，可以被任何模块调用来刷新过期的cookies。

        @param auto_get_token: 是否同时刷新API token (默认False，仅刷新cookies)
        @return: 成功返回True，失败返回False
        """
        Log.warning('🔄 检测到cookies已过期，尝试自动刷新...')

        export_module = self._load_export_module()
        if not export_module:
            self._show_manual_refresh_instructions()
            return False

        # 检查用户配置的首选浏览器
        preferred_browser = self.config.get_property('preferred_browser')

        try:
            if preferred_browser:
                Log.info(f'📤 正在从{preferred_browser}导出新的cookies...')
                success = export_module.export_cookies_from_browser(
                    domain=self.moodle_domain,
                    output_file=self.cookies_path,
                    browser=preferred_browser
                )
            else:
                Log.info('📤 正在从浏览器自动导出新的cookies...')
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

            playwright_cookie = {
                'name': cookie.name,
                'value': cookie.value,
                'domain': cookie.domain,
                'path': cookie.path,
                'expires': expires_value,
                'httpOnly': is_http_only,
                'secure': cookie.secure,
                'sameSite': cookie.get_nonstandard_attr('SameSite', 'Lax') or 'Lax',
            }
            playwright_cookies.append(playwright_cookie)

        return playwright_cookies

    except Exception as e:
        Log.error(f'转换cookies格式失败: {e}')
        return []
