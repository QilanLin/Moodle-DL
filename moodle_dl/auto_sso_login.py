#!/usr/bin/env python3
"""
自动 SSO 登录模块 - 使用 Playwright 有头浏览器

核心思路：
1. 从用户真实浏览器读取 SSO cookies（Microsoft/Google 等）
2. 使用 Playwright 有头浏览器加载这些 SSO cookies
3. 自动访问 Moodle 并完成 SSO 登录流程
4. 获取新的 MoodleSession cookie
5. 保存所有 cookies（包括刷新后的 MoodleSession）

优势：
- 只要 SSO cookies 不过期，就能完全自动化
- 无需用户手动登录
- MoodleSession 过期时自动刷新
"""

import asyncio
import logging
import os
import re
from typing import Tuple, List, Dict, Optional

from moodle_dl.utils import Log


def extract_all_cookies_from_browser(
    browser_name: str,
    moodle_domain: str,
    cookies_path: str
) -> List[Dict]:
    """
    从浏览器中提取所有 cookies（不过滤）
    
    **v2: 彻底移除文件读取，只从浏览器获取**
    
    核心原理：完整复制用户浏览器的所有 cookies 到 Playwright，
    这样 Playwright 就"继承"了用户的完整登录状态。
    
    **重要变更：**
    - v2: 不再读取 cookies 文件（cookies_path 参数保留但不再使用）
    - v2: 只从浏览器读取 cookies
    
    @param browser_name: 浏览器名称（firefox, chrome 等）
    @param moodle_domain: Moodle 域名（用于日志）
    @param cookies_path: [已废弃] cookies 文件路径（不再使用）
    @return: 所有 cookies 的列表
    """
    try:
        # v2: 直接从浏览器读取 cookies（永不读取文件）
        logging.info(f'💡 正在从浏览器直接读取所有 cookies...')
        all_cookies = _read_all_cookies_from_browser(browser_name)
        
        if all_cookies:
            logging.info(f'✓ 从浏览器成功读取 {len(all_cookies)} 个 cookies')
        else:
            logging.warning('⚠️  浏览器中没有找到 cookies')
            logging.info('   请确保浏览器已登录 Moodle，且 SSO cookies 有效')
        
        return all_cookies
        
    except Exception as e:
        logging.error(f'❌ 提取 cookies 时出错: {e}')
        return []


def _read_all_cookies_from_browser(browser_name: str) -> List[Dict]:
    """
    从浏览器数据库中读取所有 cookies（不过滤）

    @param browser_name: 浏览器名称
    @return: 所有 cookies 列表
    """
    try:
        import browser_cookie3

        # 获取浏览器的 cookie jar
        browser_methods = {
            'chrome': browser_cookie3.chrome,
            'firefox': browser_cookie3.firefox,
            'edge': browser_cookie3.edge,
            'brave': browser_cookie3.brave,
            'safari': browser_cookie3.safari,
        }

        if browser_name not in browser_methods:
            logging.warning(f'⚠️  不支持的浏览器: {browser_name}')
            return []

        cj = browser_methods[browser_name]()

        all_cookies = []
        for cookie in cj:
            # 处理 expires 字段（Playwright 只接受 -1 或正整数秒级时间戳）
            expires_value = -1  # 默认永不过期
            if cookie.expires is not None and cookie.expires > 0:
                # 如果是毫秒级时间戳（>10000000000），转换为秒级
                if cookie.expires > 10000000000:
                    expires_value = int(cookie.expires / 1000)
                else:
                    expires_value = int(cookie.expires)

            cookie_dict = {
                'name': cookie.name,
                'value': cookie.value,
                'domain': cookie.domain,
                'path': cookie.path,
                'expires': expires_value,
                'httpOnly': bool(cookie.has_nonstandard_attr('HttpOnly')),
                'secure': bool(cookie.secure),  # 确保是布尔值，不是整数
                'sameSite': cookie.get_nonstandard_attr('SameSite', 'Lax') or 'Lax',
            }
            all_cookies.append(cookie_dict)

        logging.info(f'✓ 从{browser_name}读取到 {len(all_cookies)} 个 cookies（所有域名）')
        return all_cookies

    except Exception as e:
        logging.error(f'❌ 从浏览器读取cookies失败: {e}')
        return []


def _read_sso_cookies_from_browser_DEPRECATED(browser_name: str, moodle_domain: str) -> List[Dict]:
    """
    从浏览器数据库中读取 SSO cookies

    @param browser_name: 浏览器名称
    @param moodle_domain: Moodle 域名（用于识别非 Moodle 的 cookies）
    @return: SSO cookies 列表
    """
    try:
        import browser_cookie3

        # 获取浏览器的 cookie jar
        browser_methods = {
            'chrome': browser_cookie3.chrome,
            'firefox': browser_cookie3.firefox,
            'edge': browser_cookie3.edge,
            'brave': browser_cookie3.brave,
            'safari': browser_cookie3.safari,
        }

        if browser_name not in browser_methods:
            logging.warning(f'⚠️  不支持的浏览器: {browser_name}')
            return []

        cj = browser_methods[browser_name]()

        # 提取 Moodle 主域名
        moodle_main_domain = '.'.join(moodle_domain.split('.')[-2:])

        # 常见的 SSO 提供商域名关键词
        sso_domains = [
            'microsoftonline.com',
            'microsoft.com',
            'live.com',
            'accounts.google.com',
            'google.com',
            'okta.com',
            'auth0.com',
            'shibboleth',
            'saml',
            'oauth',
            'login.',
            'auth.',
            'sso.',
        ]

        sso_cookies = []
        for cookie in cj:
            cookie_domain_lower = cookie.domain.lower()

            # 只保留 SSO 相关域名的 cookies
            is_sso_cookie = False

            # 1. 排除 Moodle 域名
            if moodle_main_domain in cookie_domain_lower:
                continue

            # 2. 检查是否匹配 SSO 提供商
            for sso_domain in sso_domains:
                if sso_domain in cookie_domain_lower:
                    is_sso_cookie = True
                    break

            if is_sso_cookie:
                cookie_dict = {
                    'name': cookie.name,
                    'value': cookie.value,
                    'domain': cookie.domain,
                    'path': cookie.path,
                    'expires': int(cookie.expires) if cookie.expires else -1,
                    'httpOnly': bool(cookie.has_nonstandard_attr('HttpOnly')),
                    'secure': cookie.secure,
                    'sameSite': cookie.get_nonstandard_attr('SameSite', 'Lax') or 'Lax',
                }
                sso_cookies.append(cookie_dict)
                logging.debug(f'✓ SSO cookie: {cookie.name} ({cookie.domain})')

        logging.info(f'✓ 从{browser_name}读取到 {len(sso_cookies)} 个 SSO cookies')
        return sso_cookies

    except Exception as e:
        logging.error(f'❌ 从浏览器读取cookies失败: {e}')
        return []


async def auto_login_with_sso(
    moodle_domain: str,
    cookies_path: str,
    preferred_browser: str = 'firefox',
    headless: bool = False,
    timeout: int = 30000,
    auth_manager=None
) -> bool:
    """
    使用 Playwright 有头浏览器自动完成 SSO 登录

    核心流程：
    1. 从浏览器读取现有的 SSO cookies（Microsoft/Google等）
    2. 启动 Playwright 浏览器（有头或无头）
    3. 加载 SSO cookies
    4. 访问 Moodle，触发 SSO 登录流程
    5. 等待 SSO 自动登录完成
    6. 提取新的 MoodleSession 和其他 cookies
    7. 保存到数据库

    @param moodle_domain: Moodle 域名（如 keats.kcl.ac.uk）
    @param cookies_path: 保存 cookies 的文件路径（向后兼容）
    @param preferred_browser: 首选浏览器（读取SSO cookies用）
    @param headless: 是否使用无头模式（默认False，使用有头浏览器）
    @param timeout: 页面加载超时时间（毫秒）
    @param auth_manager: AuthSessionManager 实例（用于数据库保存）
    @return: 成功返回 True
    """
    try:
        from playwright.async_api import async_playwright

        logging.info('🚀 正在启动自动 SSO 登录...')

        # 1. 提取所有 cookies（完整复制用户浏览器状态）
        all_cookies = extract_all_cookies_from_browser(
            preferred_browser, moodle_domain, cookies_path
        )

        if len(all_cookies) == 0:
            logging.warning('⚠️  没有找到任何 cookies')
            logging.info('💡 请先在浏览器中登录一次 Moodle（完成SSO认证）')
            logging.info('   然后 moodle-dl 将能够自动刷新 MoodleSession')
            return False

        logging.info(f'✓ 准备将 {len(all_cookies)} 个 cookies 迁移到 Playwright 浏览器')
        logging.info('   💡 原理：完整复制用户浏览器状态，实现自动化登录')

        # 2. 准备 Storage State（Playwright 的推荐方式）
        # 关键改进：使用 storageState 而不是手动 add_cookies
        # 这样 Playwright 会自动处理所有域名的 cookies
        storage_state = {
            'cookies': all_cookies,
            'origins': []  # 可选，用于存储 localStorage
        }

        logging.info(f'   准备 Storage State: {len(all_cookies)} 个 cookies')

        # 3. 启动 Playwright 浏览器并使用 Storage State
        async with async_playwright() as p:
            # 使用有头浏览器（headless=False）以便查看登录过程
            browser_type = p.firefox if preferred_browser == 'firefox' else p.chromium

            if headless:
                logging.info('🌐 启动无头浏览器...')
                browser = await browser_type.launch(headless=True)
            else:
                logging.info('🌐 启动有头浏览器（可见窗口，方便调试）...')
                browser = await browser_type.launch(
                    headless=False,
                    slow_mo=500  # 减慢操作，方便观察
                )

            # 创建浏览器上下文，使用 storageState 一次性加载所有 cookies
            try:
                context = await browser.new_context(
                    storage_state=storage_state,
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/115.0',
                    viewport={'width': 1920, 'height': 1080},
                    locale='en-GB',
                    timezone_id='Europe/London',
                )
                logging.info('✓ Storage State 已加载（所有 cookies 已注入）')
            except Exception as e:
                logging.warning(f'⚠️  Storage State 加载失败: {e}')
                logging.info('   回退到创建空白 context...')
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/115.0',
                    viewport={'width': 1920, 'height': 1080},
                    locale='en-GB',
                    timezone_id='Europe/London',
                )

            # 4. 访问 Moodle 主页，触发 SSO 登录
            page = await context.new_page()
            moodle_url = f'https://{moodle_domain}/' if not moodle_domain.startswith('http') else moodle_domain

            logging.info(f'🔗 正在访问 Moodle: {moodle_url}')
            logging.info('   等待 SSO 自动登录完成...')
            logging.info('   💡 原理：只要 SSO cookies 有效，将完全自动化完成登录')

            try:
                # 使用 domcontentloaded 而不是 load - 只等DOM加载，不等所有资源
                # 这样可以避免被第三方tracking scripts阻塞（Google Analytics等）
                # 对于SSO重定向来说，DOM加载完成就足够了
                response = await page.goto(moodle_url, wait_until='domcontentloaded', timeout=timeout)

                # 等待 SSO 重定向完成
                # 策略：追踪 URL 变化，检测是否经历了 SSO 重定向流程
                max_redirect_wait = 15  # 最多等待 15 秒让重定向完成
                visited_sso = False  # 是否访问过 SSO 提供商（Microsoft/Google）

                for i in range(max_redirect_wait):
                    await page.wait_for_timeout(1000)  # 每次等待 1 秒
                    current_url = page.url

                    # 检测是否在 SSO 提供商页面
                    if 'microsoft' in current_url.lower() or 'google' in current_url.lower():
                        visited_sso = True
                        logging.debug(f'🔐 检测到 SSO 重定向: {current_url}')

                    # 如果访问过 SSO 并且现在回到 Moodle 域名，说明重定向完成
                    if visited_sso and moodle_domain in current_url:
                        logging.debug(f'✓ SSO 重定向完成，已返回 Moodle: {current_url}')
                        break

                    # 如果一直没离开 Moodle（可能 cookies 仍然有效或已过期但没有重定向）
                    # 继续等待一会儿看是否会发生重定向
                    if not visited_sso and moodle_domain in current_url:
                        logging.debug(f'⏳ 等待可能的 SSO 重定向... (第{i+1}/{max_redirect_wait}秒)')
                    elif not visited_sso:
                        logging.debug(f'🔍 当前URL: {current_url}')

                # 最终检查
                current_url = page.url
                page_content = await page.content()

                logging.info(f'📍 最终URL: {current_url}')
                logging.debug(f'🔍 是否经历过 SSO 重定向: {visited_sso}')

                # 检查是否在登录/认证页面
                # 注意：停留在 Microsoft/Google OAuth 授权页面可能不是 cookies 过期
                # 而是需要额外的交互式验证（Playwright 无法自动处理）
                if 'login' in current_url.lower() or 'auth' in current_url.lower():
                    # 区分不同的登录页面类型
                    if 'microsoft' in current_url.lower() or 'google' in current_url.lower():
                        logging.warning('⚠️  Playwright 停留在 SSO 授权页面')
                        logging.info('   原因：需要额外的交互式验证（Playwright 自动化无法完成）')
                        logging.info('   但这不代表 SSO cookies 完全过期！')
                        logging.debug('   💡 Playwright 自动登录失败，将回退到浏览器导出的 cookies')
                    else:
                        logging.warning('⚠️  Playwright 停留在 Moodle 登录页面')
                        logging.info('   原因：SSO cookies 可能已过期，或需要重新验证')

                    # 保存当前页面截图（调试用）
                    screenshot_path = '/tmp/moodle_sso_login_failed.png'
                    try:
                        await page.screenshot(path=screenshot_path)
                        logging.debug(f'📸 已保存截图到: {screenshot_path}')
                    except:
                        pass

                    await browser.close()
                    # 返回 False，但这不意味着 cookies 完全无用
                    # 调用者应该回退到使用浏览器导出的 cookies
                    return False

                # 检查页面内容中的错误标志
                error_indicators = [
                    'Sign in to your account',  # Microsoft 登录页面
                    'Invalid login',  # Moodle 登录错误
                    'You are not logged in',  # 未登录
                    'enrol/index.php',  # 需要注册/登录
                ]

                has_error = any(indicator in page_content for indicator in error_indicators)

                if has_error and not visited_sso:
                    logging.warning('⚠️  页面显示未登录，且未经历 SSO 重定向')
                    logging.warning('⚠️  SSO cookies 可能已完全过期')
                    logging.info('')
                    logging.info('💡 解决方案：')
                    logging.info('   在浏览器中访问 keats.kcl.ac.uk 并完成 SSO 登录')
                    logging.info('   之后将能够完全自动化')
                    logging.info('')

                    await browser.close()
                    return False

                # 检查是否已登录（查找 logout 链接）
                if 'login/logout.php' in page_content or visited_sso:
                    if visited_sso:
                        Log.success('✅ SSO 自动登录成功！（经历完整 SSO 重定向）')
                    else:
                        Log.success('✅ SSO 自动登录成功！（使用现有 cookies）')

                    # 5. 提取更新后的 cookies（包括新的 MoodleSession）
                    updated_cookies = await context.cookies()
                    Log.info(f'📦 获取到 {len(updated_cookies)} 个 cookies')

                    # 显示关键 cookies（显示完整值来对比）
                    for cookie in updated_cookies:
                        if cookie['name'] == 'MoodleSession':
                            Log.info(f'   ✓ {cookie["name"]}: {cookie["value"]}')

                    # 6. 保存 cookies 到数据库（而不是文件）
                    if auth_manager:
                        # 使用数据库保存 cookies
                        session_id = auth_manager.save_sso_cookies(updated_cookies)
                        if session_id:
                            logging.info(f'💾 Cookies 已保存到数据库: 会话 {session_id}')
                            logging.info(f'   共 {len(updated_cookies)} 个 cookies')
                        else:
                            logging.error('❌ 保存 cookies 到数据库失败')
                            await browser.close()
                            return False
                    else:
                        # v2: 彻底移除文件回退，auth_manager 必须存在
                        logging.error('❌ SSO登录失败: 必须提供 AuthSessionManager')
                        logging.error('   这是v2架构的要求，数据库必须可用')
                        await browser.close()
                        return False

                    await browser.close()
                    return True

                else:
                    # 无法确定登录状态
                    logging.warning('⚠️  无法确定登录状态')
                    logging.info(f'   当前URL: {current_url}')
                    logging.info('   页面中未找到 logout 链接')
                    logging.info('   未检测到 SSO 重定向')

                    # 保存调试信息
                    debug_path = '/tmp/moodle_login_uncertain.html'
                    with open(debug_path, 'w', encoding='utf-8') as f:
                        f.write(page_content)
                    logging.debug(f'📝 已保存页面内容到: {debug_path}')

                    await browser.close()
                    return False

            except Exception as page_error:
                logging.error(f'❌ 页面加载出错: {page_error}')

                # 尝试获取当前状态
                try:
                    current_url = page.url
                    logging.info(f'📍 出错时的URL: {current_url}')

                    # 检查是否在 SSO 提供商页面
                    if 'microsoft' in current_url.lower() or 'google' in current_url.lower():
                        logging.info('💡 当前在 SSO 提供商页面')
                        logging.info('   这可能意味着需要重新认证')
                        logging.info('   建议：在浏览器中手动登录一次，然后重试')
                except:
                    pass

                await browser.close()
                return False

    except ImportError as e:
        logging.error(f'❌ 缺少依赖: {e}')
        logging.info('💡 请安装: pip install playwright browser-cookie3')
        logging.info('   然后运行: playwright install firefox')
        return False

    except Exception as e:
        logging.error(f'❌ 自动登录失败: {e}')
        import traceback
        logging.debug(traceback.format_exc())
        return False


# 同步包装函数
def auto_login_with_sso_sync(
    moodle_domain: str,
    cookies_path: str,
    preferred_browser: str = 'firefox',
    headless: bool = False,
    timeout: int = 30000,
    auth_manager=None
) -> bool:
    """
    同步版本的自动 SSO 登录

    @param moodle_domain: Moodle 域名
    @param cookies_path: cookies 保存路径（向后兼容）
    @param preferred_browser: 首选浏览器
    @param headless: 是否使用无头模式
    @param timeout: 页面加载超时时间（毫秒）
    @param auth_manager: AuthSessionManager 实例（用于数据库保存）
    @return: 成功返回 True
    """
    return asyncio.run(auto_login_with_sso(
        moodle_domain, cookies_path, preferred_browser, headless, timeout, auth_manager
    ))


if __name__ == '__main__':
    # 测试代码
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s: %(message)s'
    )

    success = auto_login_with_sso_sync(
        moodle_domain='keats.kcl.ac.uk',
        cookies_path='/tmp/test_cookies.txt',
        preferred_browser='firefox',
        headless=False  # 使用有头浏览器方便观察
    )

    if success:
        print('\n✅ 自动登录成功！')
    else:
        print('\n❌ 自动登录失败')
