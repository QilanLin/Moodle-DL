# -*- coding: utf-8 -*-
"""
统一的Cookies管理模块

提供全局的cookies刷新、验证和管理功能，遵循DRY原则。
所有需要使用browser cookies的模块都应该使用这里的函数。

v2 改进：使用数据库存储 cookies 而不是文本文件
- cookies 存储在 auth_sessions 和 cookie_store 表
- 每次刷新都创建新的 session，形成版本链
- 完整的审计日志追踪
"""

import importlib.util
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from moodle_dl.utils import Log


class CookieManager:
    """
    Cookies管理器 - 统一处理cookies的导出、刷新和验证

    v2 版本：整合数据库存储
    - 优先使用数据库中的有效 session
    - 刷新时创建新 session 并存储到数据库
    - 保留向后兼容性（支持 Cookies.txt 回退）
    """

    def __init__(
        self,
        config,
        moodle_domain: str,
        cookies_path: str,
        db_file: str = None
    ):
        """
        初始化CookieManager

        @param config: moodle-dl配置对象
        @param moodle_domain: Moodle域名 (如 keats.kcl.ac.uk)
        @param cookies_path: cookies文件路径（向后兼容）
        @param db_file: SQLite 数据库文件路径（新增）

        数据库初始化失败时直接抛出异常，不使用 fallback
        """
        self.config = config
        self.moodle_domain = moodle_domain
        self.cookies_path = cookies_path
        self.db_file = db_file
        self._export_module = None
        self._auth_manager = None

        # 初始化认证管理器（必须成功，否则抛出异常）
        if db_file:
            from moodle_dl.auth_session_manager import AuthSessionManager
            self._auth_manager = AuthSessionManager(db_file)
            if not self._auth_manager:
                raise RuntimeError(
                    f'❌ 认证管理器初始化失败。数据库文件: {db_file}\n'
                    f'请检查数据库是否存在且可写。'
                )

    def get_cookies_from_db(self) -> Optional[List[Dict]]:
        """
        从数据库获取有效的 cookies

        @return: cookies 列表，或 None 如果不存在
        """
        if not self._auth_manager:
            return None

        session = self._auth_manager.get_valid_session(session_type='cookie_batch')
        if session:
            return self._auth_manager.get_session_cookies(session['session_id'])

        return None

    def save_cookies_to_db(
        self,
        cookies: List[dict],
        source: str = 'browser_export'
    ) -> Optional[str]:
        """
        将 cookies 保存到数据库

        @param cookies: cookies 列表
        @param source: 来源 (browser_export/autologin/sso 等)
        @return: session_id，或 None 如果保存失败
        """
        if not self._auth_manager:
            return None

        try:
            session_id = self._auth_manager.create_session(
                session_type='cookie_batch',
                source=source,
                cookies=cookies,
                ip_address=self._get_client_ip()
            )
            Log.debug(f'✓ Cookies 已保存到数据库（session_id={session_id}）')
            return session_id
        except Exception as e:
            Log.debug(f'保存 cookies 到数据库失败: {e}')
            return None

    def refresh_session_with_new_cookies(
        self,
        new_cookies: List[dict],
        source: str = 'browser_export'
    ) -> Optional[str]:
        """
        刷新认证会话为新的 cookies（创建新版本）

        @param new_cookies: 新的 cookies 列表
        @param source: 来源
        @return: 新 session_id，或 None 如果失败
        """
        if not self._auth_manager:
            return self.save_cookies_to_db(new_cookies, source)

        try:
            # 获取当前有效的 session
            old_session = self._auth_manager.get_valid_session(session_type='cookie_batch')

            if old_session:
                # 创建新版本的 session
                new_session_id = self._auth_manager.refresh_session(
                    old_session_id=old_session['session_id'],
                    new_cookies=new_cookies
                )
                return new_session_id
            else:
                # 没有旧 session，创建新 session
                return self.save_cookies_to_db(new_cookies, source)

        except Exception as e:
            Log.debug(f'刷新 session 失败: {e}')
            return None

    @staticmethod
    def _get_client_ip() -> str:
        """获取客户端 IP（简化版本）"""
        try:
            import socket
            return socket.gethostbyname(socket.gethostname())
        except (OSError, socket.gaierror) as e:
            logging.debug(f'获取客户端 IP 失败: {e}，使用默认值')
            return '127.0.0.1'

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
                    from moodle_dl.cli.authenticators import _should_use_headless_sso

                    # 使用线程池在后台运行同步版本（它会创建新的事件循环）
                    # 默认有头模式（与 --init --sso / --refresh-cookies 一致），
                    # 这样 Microsoft 多账号选择器、MFA、验证码都能手动处理。
                    # 如不希望下载中途弹窗，可设置 MOODLE_DL_HEADLESS=1。
                    use_headless = _should_use_headless_sso()
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(
                            auto_login_with_sso_sync,
                            self.moodle_domain,
                            self.cookies_path,
                            preferred_browser,
                            use_headless,
                            30000,  # timeout
                            self._auth_manager  # 传入 AuthSessionManager 保存到数据库
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
                    from moodle_dl.cli.authenticators import _should_use_headless_sso

                    # 默认有头模式（与 --init --sso / --refresh-cookies 一致），
                    # 这样 Microsoft 多账号选择器、MFA、验证码都能手动处理。
                    # 如不希望下载中途弹窗，可设置 MOODLE_DL_HEADLESS=1。
                    success = auto_login_with_sso_sync(
                        moodle_domain=self.moodle_domain,
                        cookies_path=self.cookies_path,
                        preferred_browser=preferred_browser,
                        headless=_should_use_headless_sso(),
                        auth_manager=self._auth_manager  # 传入 AuthSessionManager 保存到数据库
                    )

                    if success:
                        Log.success('✅ 自动 SSO 登录成功！Cookies已刷新')
                        return True
                    else:
                        Log.info('💡 自动 SSO 登录失败，尝试从浏览器读取cookies...')

                except Exception as e:
                    Log.debug(f'自动 SSO 登录出错: {e}')
                    Log.info('💡 回退到从浏览器读取cookies...')

        # 2. 回退：从浏览器读取 cookies（使用新的 extract_all_cookies_from_browser 支持 SSO 和多账号检测）
        Log.info(f'📤 正在从{preferred_browser}读取cookies...')
        Log.info('   💡 v2: 支持多账号检测，如有多个 Microsoft 账号会提示选择')

        try:
            # 直接调用 extract_all_cookies_from_browser（包含多账号检测）
            from moodle_dl.auto_sso_login import extract_all_cookies_from_browser

            cookies = extract_all_cookies_from_browser(
                browser_name=preferred_browser,
                moodle_domain=self.moodle_domain,
                cookies_path=self.cookies_path
            )

            if cookies and len(cookies) > 0:
                # 保存到数据库（如果需要的话也可以保存到文件）
                if self._auth_manager:
                    try:
                        session_id = self.refresh_session_with_new_cookies(
                            new_cookies=cookies,
                            source='browser_export'
                        )
                        if session_id:
                            Log.debug(f'✓ Cookies已保存到数据库（session_id={session_id}）')
                    except Exception as db_error:
                        Log.debug(f'⚠️  将cookies保存到数据库失败: {db_error}')

                Log.success(f'✅ Cookies自动刷新成功！({len(cookies)} 个cookies)')
                return True
            else:
                Log.warning('⚠️  从浏览器读取cookies失败')
                self._show_manual_refresh_instructions()
                return False

        except Exception as e:
            Log.error(f'❌ 从浏览器读取cookies时出错: {e}')
            self._show_manual_refresh_instructions()
            return False

        except Exception as e:
            Log.error(f'❌ 刷新cookies时出错: {e}')
            self._show_manual_refresh_instructions()
            return False

    def _load_cookies_from_file(self, file_path: str) -> Optional[List[Dict]]:
        """
        从 Netscape 格式的 cookies 文件中加载 cookies
        
        使用 Python 标准库 http.cookiejar.MozillaCookieJar 来正确解析 Netscape Cookie 文件。
        这是处理 Netscape Cookie 格式的最佳实践，能够：
        - 自动处理 TRUE/FALSE 布尔值
        - 正确解析所有字段类型
        - 处理各种边界情况
        - 官方维护，经过充分测试

        @param file_path: cookies 文件路径
        @return: cookies 列表（字典格式），或 None 如果加载失败
        """
        if not os.path.exists(file_path):
            Log.debug(f'Cookie 文件不存在: {file_path}')
            return None

        try:
            import http.cookiejar
            
            # 使用标准库的 MozillaCookieJar 加载 Netscape 格式
            cookie_jar = http.cookiejar.MozillaCookieJar(file_path)
            
            try:
                # 加载 cookies，忽略过期和丢弃标志
                cookie_jar.load(ignore_discard=True, ignore_expires=True)
            except http.cookiejar.LoadError as e:
                # 如果文件格式有问题，尝试手动解析（向后兼容）
                Log.warning(f'⚠️  标准库加载失败: {e}，尝试手动解析...')
                return self._fallback_manual_parse(file_path)
            
            # 将 CookieJar 转换为字典列表（用于数据库存储和 Playwright）
            cookies = []
            for cookie in cookie_jar:
                cookie_dict = {
                    'domain': cookie.domain,
                    'path': cookie.path,
                    'secure': 1 if cookie.secure else 0,
                    'expires': int(cookie.expires) if cookie.expires else None,
                    'name': cookie.name,
                    'value': cookie.value,
                    'httponly': 1 if cookie.has_nonstandard_attr('HttpOnly') else 0,
                    'samesite': 'Lax'  # 默认值，Netscape 格式不包含此字段
                }
                cookies.append(cookie_dict)
            
            Log.debug(f'✓ 使用标准库从文件加载了 {len(cookies)} 个 cookies')
            return cookies if cookies else None
            
        except Exception as e:
            Log.warning(f'⚠️  标准库加载 cookies 失败: {e}，尝试手动解析...')
            return self._fallback_manual_parse(file_path)

    def _fallback_manual_parse(self, file_path: str) -> Optional[List[Dict]]:
        """
        手动解析 Netscape Cookie 文件（作为标准库的后备方案）
        
        当标准库 MozillaCookieJar 无法解析时使用此方法。
        这可能发生在：
        - 文件格式不完全符合标准
        - 缺少文件头注释
        - 某些字段格式异常
        
        @param file_path: cookies 文件路径
        @return: cookies 列表，或 None 如果加载失败
        """
        cookies = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # 跳过注释和空行
                    if not line or line.startswith('#'):
                        continue

                    # 解析 Netscape 格式: domain flag path secure expiration name value
                    parts = line.split('\t')
                    if len(parts) >= 7:
                        # 转换 secure 字段：Netscape 格式使用 TRUE/FALSE 字符串
                        secure_str = parts[3].strip().upper()
                        if secure_str in ('TRUE', '1'):
                            secure = 1
                        elif secure_str in ('FALSE', '0'):
                            secure = 0
                        else:
                            # 尝试作为整数解析（兼容性）
                            try:
                                secure = int(secure_str)
                            except ValueError:
                                logging.debug(f'无法解析 secure 字段: {secure_str}，使用默认值 0')
                                secure = 0
                        
                        # 处理 expires 字段
                        try:
                            expires_val = int(parts[4]) if parts[4] and parts[4] != '0' else None
                        except ValueError:
                            logging.debug(f'无法解析 expires 字段: {parts[4]}，使用默认值 None')
                            expires_val = None
                        
                        cookies.append({
                            'domain': parts[0],
                            'path': parts[2],
                            'secure': secure,
                            'expires': expires_val,
                            'name': parts[5],
                            'value': parts[6],
                            'httponly': 1,  # Netscape 格式不包含 httponly，默认设为 1
                            'samesite': 'Lax'  # 默认值
                        })

            Log.debug(f'✓ 手动解析加载了 {len(cookies)} 个 cookies')
            return cookies if cookies else None

        except Exception as e:
            Log.debug(f'❌ 手动解析也失败: {e}')
            return None

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
    v2 改进：自动传入 db_file，支持数据库存储

    @param client: RequestHelper实例
    @param config: 配置对象
    @return: CookieManager实例
    """
    from moodle_dl.utils import PathTools as PT

    cookies_path = PT.get_cookies_path(config.get_misc_files_path())
    moodle_domain = client.moodle_url.domain

    # v2：获取数据库文件路径
    db_file = None
    try:
        misc_files_path = config.get_misc_files_path()
        db_file = PT.make_path(misc_files_path, 'moodle_state.db')
    except (OSError, KeyError, AttributeError) as e:
        logging.debug(f'获取数据库文件路径失败: {e}，将使用默认路径')

    return CookieManager(config, moodle_domain, cookies_path, db_file)


def convert_netscape_cookies_to_playwright(cookies_path: str) -> List[Dict[str, Any]]:
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
