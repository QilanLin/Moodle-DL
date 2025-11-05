#!/usr/bin/env python3
"""
从浏览器自动导出 Moodle cookies

要求：pip install browser-cookie3
"""

import os
import sys
import glob
import platform

try:
    import browser_cookie3
except ImportError:
    print("错误：需要安装 browser-cookie3")
    print("运行：pip install browser-cookie3")
    sys.exit(1)


def find_browser_cookie_path(browser_name: str) -> str:
    """
    自动检测指定浏览器的 cookie 文件路径

    Args:
        browser_name: 浏览器名称 (zen, waterfox, arc 等)

    Returns:
        cookie 文件的绝对路径，如果找不到则返回 None
    """
    system = platform.system()

    # 定义不同浏览器在不同系统上的路径模式
    browser_paths = {
        'zen': {
            'Darwin': '~/Library/Application Support/zen/Profiles/*.default*/cookies.sqlite',
            'Linux': ['~/.zen/Profiles/*.default*/cookies.sqlite',
                     '~/.var/app/app.zen_browser.zen/zen/Profiles/*.default*/cookies.sqlite'],  # Flatpak
            'Windows': os.path.join(os.getenv('APPDATA', ''), 'zen', 'Profiles', '*.default*', 'cookies.sqlite')
        },
        'waterfox': {
            'Darwin': '~/Library/Application Support/Waterfox/Profiles/*.default*/cookies.sqlite',
            'Linux': '~/.waterfox/Profiles/*.default*/cookies.sqlite',
            'Windows': os.path.join(os.getenv('APPDATA', ''), 'Waterfox', 'Profiles', '*.default*', 'cookies.sqlite')
        },
        'arc': {
            'Darwin': '~/Library/Application Support/Arc/User Data/*/Cookies',
            'Linux': None,  # Arc 不支持 Linux
            'Windows': os.path.join(os.getenv('LOCALAPPDATA', ''), 'Arc', 'User Data', '*', 'Cookies')
        },
        'firefox': {
            'Darwin': '~/Library/Application Support/Firefox/Profiles/*.default*/cookies.sqlite',
            'Linux': '~/.mozilla/firefox/*.default*/cookies.sqlite',
            'Windows': os.path.join(os.getenv('APPDATA', ''), 'Mozilla', 'Firefox', 'Profiles', '*.default*', 'cookies.sqlite')
        },
        'librewolf': {
            'Darwin': '~/Library/Application Support/LibreWolf/Profiles/*.default*/cookies.sqlite',
            'Linux': '~/.librewolf/*.default*/cookies.sqlite',
            'Windows': os.path.join(os.getenv('APPDATA', ''), 'LibreWolf', 'Profiles', '*.default*', 'cookies.sqlite')
        }
    }

    if browser_name.lower() not in browser_paths:
        return None

    paths = browser_paths[browser_name.lower()].get(system)

    if not paths:
        return None

    # 处理多个可能的路径（如 Flatpak）
    if isinstance(paths, list):
        for path_pattern in paths:
            expanded = os.path.expanduser(path_pattern)
            matches = glob.glob(expanded)
            if matches:
                # 返回最近修改的文件
                return max(matches, key=os.path.getmtime)
    else:
        # 单个路径模式
        expanded = os.path.expanduser(paths)
        matches = glob.glob(expanded)
        if matches:
            # 返回最近修改的文件（通常是当前使用的 profile）
            return max(matches, key=os.path.getmtime)

    return None


def _repair_firefox_cookies_db():
    """
    修复Firefox的cookies.sqlite数据库

    如果主文件不存在或损坏，自动从.bak备份恢复
    这解决了Firefox关闭后WAL文件无法合并的问题
    """
    import platform
    import os

    system = platform.system()

    # 获取Firefox Profile路径
    if system == 'Darwin':  # macOS
        profile_base = os.path.expanduser('~/Library/Application Support/Firefox/Profiles')
    elif system == 'Linux':
        profile_base = os.path.expanduser('~/.mozilla/firefox')
    elif system == 'Windows':
        profile_base = os.path.join(os.getenv('APPDATA', ''), 'Mozilla', 'Firefox', 'Profiles')
    else:
        return False

    if not os.path.exists(profile_base):
        return False

    # 找到default或default-release profile
    for profile_dir in os.listdir(profile_base):
        if 'default' not in profile_dir.lower():
            continue

        profile_path = os.path.join(profile_base, profile_dir)
        cookies_main = os.path.join(profile_path, 'cookies.sqlite')
        cookies_bak = os.path.join(profile_path, 'cookies.sqlite.bak')
        cookies_wal = os.path.join(profile_path, 'cookies.sqlite-wal')

        # 检查是否需要修复
        main_exists = os.path.exists(cookies_main) and os.path.getsize(cookies_main) > 0
        bak_exists = os.path.exists(cookies_bak) and os.path.getsize(cookies_bak) > 1024  # > 1KB

        if not main_exists and bak_exists:
            # 主文件不存在或为空，但有有效的备份
            try:
                import shutil
                shutil.copy(cookies_bak, cookies_main)

                # 清理WAL文件（可能为空或损坏）
                if os.path.exists(cookies_wal):
                    os.remove(cookies_wal)

                return True
            except Exception as e:
                continue

    return False


def export_cookies_from_browser(domain: str, output_file: str, browser_name='chrome'):
    """从指定浏览器导出 cookies"""

    print(f"正在从 {browser_name} 导出 cookies...")

    # Firefox特殊处理：尝试修复数据库
    if browser_name.lower() == 'firefox':
        try:
            if _repair_firefox_cookies_db():
                print(f"  ℹ️  已自动修复Firefox cookies数据库")
        except Exception as e:
            pass  # 静默处理，如果修复失败就继续用browser_cookie3的默认逻辑

    try:
        # 定义浏览器到 browser_cookie3 方法的映射
        # 使用元组: (方法, 是否需要自定义路径)
        browser_methods = {
            'chrome': (browser_cookie3.chrome, False),
            'firefox': (browser_cookie3.firefox, False),
            'edge': (browser_cookie3.edge, False),
            'safari': (browser_cookie3.safari, False),
            'brave': (browser_cookie3.brave, False),
            'vivaldi': (browser_cookie3.vivaldi, False),
            'opera': (browser_cookie3.opera, False),
            'chromium': (browser_cookie3.chromium, False),
            'librewolf': (browser_cookie3.librewolf, False),
            # 需要自定义路径的浏览器
            'zen': (browser_cookie3.firefox, True),  # Zen 使用 Firefox 格式
            'waterfox': (browser_cookie3.firefox, True),  # Waterfox 使用 Firefox 格式
            'arc': (browser_cookie3.chrome, True),  # Arc 使用 Chrome 格式
        }

        # 动态添加其他可能支持的方法
        for method_name in ['opera_gx']:
            if hasattr(browser_cookie3, method_name):
                browser_methods[method_name] = (getattr(browser_cookie3, method_name), False)

        # 获取对应的方法
        if browser_name in browser_methods:
            method, needs_custom_path = browser_methods[browser_name]

            if needs_custom_path:
                # 自动检测 cookie 文件路径
                print(f"  正在检测 {browser_name} 的 cookie 文件路径...")
                cookie_path = find_browser_cookie_path(browser_name)

                if not cookie_path:
                    print(f"❌ 未找到 {browser_name} 的 cookie 文件")
                    print(f"   可能的原因：")
                    print(f"   1. {browser_name} 未安装")
                    print(f"   2. {browser_name} 的 profile 路径不标准")
                    print(f"\n   💡 解决方案：")
                    print(f"   • 确保已安装 {browser_name} 并至少运行过一次")
                    print(f"   • 或使用浏览器扩展 'Get cookies.txt LOCALLY' 手动导出")
                    return False

                print(f"  ✓ 找到 cookie 文件: {cookie_path}")
                cj = method(cookie_file=cookie_path)
            else:
                # 使用默认路径
                cj = method()
        else:
            print(f"❌ 不支持的浏览器：{browser_name}")
            print("💡 支持的浏览器：chrome, firefox, brave, vivaldi, opera, edge, chromium, librewolf, safari, zen, waterfox, arc")
            print("   建议：选择'自动检测所有浏览器'（选项 7）")
            return False

        # 转换为列表以便计数和过滤
        cookies_list = []
        # 提取 Moodle 的主域名（去掉子域名）
        moodle_main_domain = domain.split('.')[-2] + '.' + domain.split('.')[-1] if '.' in domain else domain

        for cookie in cj:
            # 保存 Moodle 相关的 cookies 和可能的 SSO cookies
            # 策略：保存包含 Moodle 域名的 cookies，以及其他常见认证域名的 cookies
            cookie_domain = cookie.domain.lstrip('.')

            # 保存 Moodle 域名的 cookies
            if moodle_main_domain in cookie_domain:
                cookies_list.append(cookie)
            # 保存可能的 SSO cookies（常见的认证提供商域名）
            elif any(sso in cookie_domain.lower() for sso in [
                'microsoft', 'google', 'okta', 'shibboleth',
                'saml', 'oauth', 'login', 'auth', 'sso'
            ]):
                cookies_list.append(cookie)

        if not cookies_list:
            print(f"❌ 未找到 {domain} 的 cookies")
            print(f"   请确保：")
            print(f"   1. 已在浏览器中登录 {domain}")
            print(f"   2. 浏览器正在运行或最近使用过")
            print(f"\n   💡 提示：")
            if browser_name == 'firefox':
                print(f"   • 如果你使用 Zen/Waterfox/LibreWolf 等替代浏览器，")
                print(f"     请选择'自动检测所有浏览器'或在 Firefox 中登录")
            elif browser_name == 'chrome':
                print(f"   • 如果你使用 Brave/Arc/Vivaldi 等 Chromium 浏览器，")
                print(f"     可能需要选择对应的具体浏览器")
            print(f"   • 或选择'自动检测所有浏览器'（选项 7）")
            return False

        # 备份现有文件
        if os.path.exists(output_file):
            backup_file = output_file + '.backup'
            os.rename(output_file, backup_file)
            print(f"✅ 已备份现有文件到: {backup_file}")

        # 写入 Netscape cookie 格式
        with open(output_file, 'w') as f:
            f.write('# Netscape HTTP Cookie File\n')
            f.write(f'# Exported from {browser_name} browser\n')
            f.write(f'# Domain: {domain}\n')
            f.write('# This file is generated by export_browser_cookies.py\n\n')

            for cookie in cookies_list:
                # Netscape cookie format:
                # domain, flag, path, secure, expiration, name, value
                cookie_domain = cookie.domain
                flag = 'TRUE' if cookie_domain.startswith('.') else 'FALSE'
                path = cookie.path
                secure = 'TRUE' if cookie.secure else 'FALSE'
                expires = cookie.expires if cookie.expires else 0
                name = cookie.name
                value = cookie.value

                f.write(f'{cookie_domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n')

        print(f"✅ 成功导出 {len(cookies_list)} 个 cookies 到: {output_file}")

        # 显示关键 cookies（Moodle session 和 SSO cookies）
        print("\n关键 Cookies:")
        shown_cookies = set()
        for cookie in cookies_list:
            # 显示 MoodleSession
            if 'moodle' in cookie.name.lower() and 'session' in cookie.name.lower():
                if cookie.name not in shown_cookies:
                    print(f"  ✓ {cookie.name}: {cookie.value[:30]}...")
                    shown_cookies.add(cookie.name)
            # 显示非 Moodle 域名的 cookies（可能是 SSO）
            elif moodle_main_domain not in cookie.domain.lstrip('.'):
                if cookie.name not in shown_cookies:
                    print(f"  ✓ {cookie.name} ({cookie.domain}): {cookie.value[:20]}...")
                    shown_cookies.add(cookie.name)

        return True

    except Exception as e:
        error_msg = str(e)
        print(f"❌ 导出失败: {error_msg}")

        # 针对Firefox特定错误提供详细的解决方案
        if 'Failed to find Firefox cookie file' in error_msg or \
           ('firefox' in browser_name.lower() and 'cookie' in error_msg.lower()):
            print(f"\n💡 Firefox Cookie 导出失败的常见原因与解决方案：")
            print(f"   1. Firefox 正在运行（最常见）")
            print(f"      → 解决：关闭 Firefox 后重试")
            print(f"         命令：killall Firefox && sleep 3")
            print(f"   2. Firefox cookies.sqlite 数据库损坏")
            print(f"      → Firefox 会自动创建 cookies.sqlite.bak 备份")
            print(f"      → moodle-dl 现在可以自动从备份恢复")
            print(f"      → 重新运行此命令应该可以成功")
            print(f"   3. WAL 文件异常")
            print(f"      → 关闭 Firefox 会清理 WAL 文件")
            print(f"      → 或手动删除：rm ~/Library/Application\\ Support/Firefox/Profiles/*/cookies.sqlite-wal")
            print(f"   4. Firefox 从未运行过或 Profile 损坏")
            print(f"      → 至少运行一次 Firefox 并访问 {domain}")
            print(f"\n⚠️  推荐步骤：")
            print(f"   1. killall Firefox  # 关闭 Firefox")
            print(f"   2. sleep 3          # 等待系统清理")
            print(f"   3. moodle-dl        # 重新运行此命令")
            print(f"\n   或者选择其他浏览器（Chrome/Edge/Safari）")

        return False

def test_cookies(domain: str, cookies_file: str):
    """测试导出的 cookies 是否有效"""
    print("\n正在测试 cookies 有效性...")

    try:
        import requests

        session = requests.Session()

        # 加载 cookies
        from http.cookiejar import MozillaCookieJar
        cookie_jar = MozillaCookieJar(cookies_file)
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
        session.cookies = cookie_jar

        # 检查是否包含关键 cookies（方案C - Cookie域名检测）
        has_moodle_session = any('moodle' in cookie.name.lower() and 'session' in cookie.name.lower() for cookie in cookie_jar)

        # 提取 Moodle 主域名
        moodle_main_domain = domain.split('.')[-2] + '.' + domain.split('.')[-1] if '.' in domain else domain

        # 检测是否有 SSO cookies（任何非 Moodle 域名的 cookies）
        has_sso_cookies = any(
            moodle_main_domain not in cookie.domain.lstrip('.') and
            cookie.domain not in ['localhost', '127.0.0.1']
            for cookie in cookie_jar
        )

        # 测试访问
        moodle_url = f'https://{domain}/' if not domain.startswith('http') else domain
        response = session.get(moodle_url, timeout=10)

        # 方案B - 域名比较检测 SSO 重定向
        from urllib.parse import urlparse
        original_domain = urlparse(moodle_url).netloc
        final_domain = urlparse(response.url).netloc

        if 'login/logout.php' in response.text:
            print("✅ Cookies 有效！已成功认证")
            return True
        elif 'login/index.php' in response.url and original_domain == final_domain:
            # 重定向到同域名的登录页 = cookies 无效
            print("❌ Cookies 无效，被重定向到登录页")
            print(f"   请确保在浏览器中已登录 {domain}")
            return False
        elif original_domain != final_domain:
            # 重定向到不同域名 = SSO 认证
            if has_moodle_session and has_sso_cookies:
                print("✅ Cookies 导出成功（包含 SSO 认证 cookies）")
                print(f"   注意：访问时会重定向到 SSO 提供商 ({final_domain})")
                print("   这是正常的 SSO 登录流程")
                return True
            else:
                print(f"⚠️  被重定向到 SSO 提供商 ({final_domain})，但缺少关键 cookies")
                print(f"   MoodleSession: {'✓' if has_moodle_session else '✗'}")
                print(f"   SSO cookies: {'✓' if has_sso_cookies else '✗'}")
                return False
        else:
            print("⚠️  无法确定 cookies 状态")
            print(f"   响应 URL: {response.url}")
            # 如果包含关键 cookies，仍然认为成功
            if has_moodle_session:
                print("   但 cookies 文件包含 MoodleSession，应该可以使用")
                return True
            return False

    except Exception as e:
        print(f"⚠️  测试失败: {e}")
        return False


def convert_netscape_to_playwright(cookies_file: str) -> list:
    """
    将Netscape格式的cookies转换为Playwright格式

    Args:
        cookies_file: Netscape格式cookies文件路径

    Returns:
        Playwright格式的cookies列表
    """
    try:
        import http.cookiejar

        cookie_jar = http.cookiejar.MozillaCookieJar(cookies_file)
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

            playwright_cookie = {
                'name': cookie.name,
                'value': cookie.value,
                'domain': cookie.domain,
                'path': cookie.path,
                'expires': expires_value,
                'httpOnly': bool(cookie.has_nonstandard_attr('HttpOnly')),
                'secure': cookie.secure,
            }

            same_site = cookie.get_nonstandard_attr('SameSite', 'Lax')
            if same_site:
                playwright_cookie['sameSite'] = same_site

            playwright_cookies.append(playwright_cookie)

        return playwright_cookies
    except Exception as e:
        print(f"❌ 转换cookies失败: {e}")
        return []


def save_playwright_cookies_to_netscape(playwright_cookies: list, output_file: str) -> bool:
    """
    将Playwright格式的cookies保存为Netscape格式

    Args:
        playwright_cookies: Playwright格式的cookies列表
        output_file: 输出文件路径

    Returns:
        是否保存成功
    """
    try:
        with open(output_file, 'w') as f:
            # 写入文件头
            f.write('# Netscape HTTP Cookie File\n')
            f.write('# This file is generated by moodle-dl.  Do not edit.\n\n')

            for cookie in playwright_cookies:
                domain = cookie.get('domain', '')
                flag = 'TRUE' if domain.startswith('.') else 'FALSE'
                path = cookie.get('path', '/')
                secure = 'TRUE' if cookie.get('secure', False) else 'FALSE'
                expires = cookie.get('expires', 0)
                # 处理expires：Playwright用-1表示session cookie
                if expires == -1:
                    expires = 0
                name = cookie.get('name', '')
                value = cookie.get('value', '')

                f.write(f'{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n')

        return True
    except Exception as e:
        print(f"❌ 保存cookies失败: {e}")
        return False


# ⚠️ 已废弃: auto_refresh_session_with_sso()
# 此函数已被 auto_sso_login.py 中的 auto_login_with_sso_sync() 取代
# 为了符合 DRY 原则，不再维护重复的实现


def extract_api_token_with_playwright(domain: str, cookies_file: str):
    """
    使用Playwright + cookies自动获取Moodle API token

    这个方法使用无头浏览器访问token获取URL，能够正确处理SSO重定向
    并捕获最终的moodledl://token=...重定向。

    Args:
        domain: Moodle域名
        cookies_file: cookies文件路径

    Returns:
        tuple: (token, privatetoken) 如果成功，否则 (None, None)
    """
    print("\n正在使用Playwright自动获取API token...")

    try:
        from playwright.async_api import async_playwright
        import asyncio
        import http.cookiejar
        import re
        import base64

        # 转换cookies到Playwright格式
        print("  → 加载cookies...")
        cookie_jar = http.cookiejar.MozillaCookieJar(cookies_file)
        cookie_jar.load(ignore_discard=True, ignore_expires=True)

        playwright_cookies = []
        for cookie in cookie_jar:
            # 处理expires字段（毫秒转秒）
            expires_value = -1
            if cookie.expires is not None and cookie.expires > 0:
                if cookie.expires > 10000000000:
                    expires_value = int(cookie.expires / 1000)
                else:
                    expires_value = int(cookie.expires)

            playwright_cookie = {
                'name': cookie.name,
                'value': cookie.value,
                'domain': cookie.domain,
                'path': cookie.path,
                'expires': expires_value,
                'httpOnly': bool(cookie.has_nonstandard_attr('HttpOnly')),
                'secure': cookie.secure,
            }

            same_site = cookie.get_nonstandard_attr('SameSite', 'Lax')
            if same_site:
                playwright_cookie['sameSite'] = same_site

            playwright_cookies.append(playwright_cookie)

        print(f"  → 已加载 {len(playwright_cookies)} 个cookies")

        # 构造token获取URL
        moodle_url = f'https://{domain}' if not domain.startswith('http') else domain
        token_url = f"{moodle_url}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&urlscheme=moodledl"

        # 使用Playwright访问
        async def get_token():
            captured_urls = []

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                await context.add_cookies(playwright_cookies)

                page = await context.new_page()

                # 🔧 关键修复：先访问Moodle主页来刷新session
                # 这确保我们有一个活跃的MoodleSession cookie
                print(f"  → 先访问Moodle主页以刷新session...")
                try:
                    await page.goto(f"{moodle_url}/my/", wait_until='domcontentloaded', timeout=15000)
                    await page.wait_for_timeout(1000)  # 等待cookies更新
                    print(f"  → Session已刷新")
                except Exception as e:
                    print(f"  → 警告: 刷新session失败 ({str(e)[:50]}...)")

                # 监听控制台消息（可能包含重定向信息）
                def handle_console(msg):
                    text = msg.text
                    if 'moodledl://' in text or 'moodlemobile://' in text:
                        captured_urls.append(text)
                        print(f"  → 从控制台捕获: {text[:80]}...")

                page.on('console', handle_console)

                # 监听请求（尝试捕获重定向）
                def handle_request(request):
                    url = request.url
                    if 'moodledl://' in url or 'moodlemobile://' in url:
                        captured_urls.append(url)
                        print(f"  → 从请求捕获: {url[:80]}...")

                page.on('request', handle_request)

                # 监听响应
                def handle_response(response):
                    # 检查Location header
                    location = response.headers.get('location', '')
                    if 'moodledl://' in location or 'moodlemobile://' in location:
                        captured_urls.append(location)
                        print(f"  → 从响应头捕获: {location[:80]}...")

                page.on('response', handle_response)

                try:
                    # 访问token URL，期望会重定向到moodledl://
                    print(f"  → 访问token获取页面...")

                    # 使用wait_for_load_state而不是wait_until，更灵活
                    response = await page.goto(token_url, wait_until='load', timeout=30000)

                    # 等待一小段时间，让所有事件触发
                    await page.wait_for_timeout(2000)

                    # 检查页面内容是否包含token URL
                    content = await page.content()
                    if 'moodledl://' in content or 'moodlemobile://' in content:
                        # 从HTML中提取token URL
                        token_match = re.search(r'(moodledl://token=[\w=]+)', content)
                        if not token_match:
                            token_match = re.search(r'(moodlemobile://token=[\w=]+)', content)
                        if token_match:
                            captured_urls.append(token_match.group(1))
                            print(f"  → 从页面内容捕获: {token_match.group(1)[:80]}...")

                except Exception as e:
                    # 预期可能会出错（无法导航到moodledl://）
                    error_str = str(e)
                    print(f"  → 页面加载出错（预期行为）: {error_str[:100]}...")

                    # 尝试从错误消息中提取token URL
                    if 'moodledl://' in error_str or 'moodlemobile://' in error_str:
                        match = re.search(r'(moodledl://token=[\w=]+)', error_str)
                        if not match:
                            match = re.search(r'(moodlemobile://token=[\w=]+)', error_str)
                        if match:
                            captured_urls.append(match.group(1))
                            print(f"  → 从错误消息捕获: {match.group(1)[:80]}...")

                await browser.close()

                # 返回捕获到的URL
                if captured_urls:
                    # 返回第一个有效的token URL
                    for url in captured_urls:
                        if 'token=' in url:
                            return url
                return None

        # 运行异步函数
        token_redirect_url = asyncio.run(get_token())

        if not token_redirect_url:
            print("  ❌ 未能捕获到token重定向URL")
            return None, None

        # 从URL中提取token
        print(f"  → 解析token...")
        match = re.search(r'token=([\w=]+)', token_redirect_url)
        if not match:
            print(f"  ❌ 无法从URL中提取token")
            return None, None

        app_token = match.group(1)

        # 解码Base64 token
        try:
            decoded = base64.b64decode(app_token).decode('utf-8')
            parts = decoded.split(':::')

            if len(parts) == 2:
                # ⚠️ 重要：Moodle mobile token格式为 "app_token:::web_service_token"
                # moodle-dl使用第二部分（parts[1]）作为API token
                # 所以我们需要交换顺序以匹配moodle-dl的预期
                mobile_app_token = parts[0]  # 用于mobile app
                web_service_token = parts[1]   # 用于Web Service API（这是真正的API token）

                print(f"  ✅ 成功提取API token")
                print(f"     Web Service Token: {web_service_token[:20]}...")
                print(f"     Mobile App Token: {mobile_app_token[:20]}...")

                # 保存时：token字段保存web_service_token（moodle-dl会使用这个）
                # privatetoken字段保存mobile_app_token（用于mobile app，如果需要的话）
                save_token_to_config(domain, web_service_token, mobile_app_token, cookies_file)

                return web_service_token, mobile_app_token
            else:
                print(f"  ❌ Token格式不正确")
                return None, None

        except Exception as e:
            print(f"  ❌ 解码token失败: {e}")
            return None, None

    except ImportError as e:
        print(f"  ❌ Playwright未安装: {e}")
        print(f"  → 请运行: pip install playwright && playwright install chromium")
        return None, None
    except Exception as e:
        print(f"  ❌ 获取API token失败: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def extract_api_token_with_cookies(domain: str, cookies_file: str):
    """
    使用导出的cookies自动获取Moodle API token

    优先使用Playwright方式（更可靠），失败时回退到requests方式

    Args:
        domain: Moodle域名
        cookies_file: cookies文件路径

    Returns:
        tuple: (token, privatetoken) 如果成功，否则 (None, None)
    """
    # 优先尝试Playwright方式
    token, privatetoken = extract_api_token_with_playwright(domain, cookies_file)
    if token and privatetoken:
        return token, privatetoken

    # 回退到requests方式（已知对SSO登录不太可靠）
    print("\n正在使用HTTP请求方式获取API token...")
    print("（注意：对于SSO登录，此方式可能失败）")

    try:
        import requests
        from http.cookiejar import MozillaCookieJar
        import re
        import base64

        # 加载cookies
        session = requests.Session()
        cookie_jar = MozillaCookieJar(cookies_file)
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
        session.cookies = cookie_jar

        # 构造token获取URL
        moodle_url = f'https://{domain}' if not domain.startswith('http') else domain
        token_url = f"{moodle_url}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&urlscheme=moodledl"

        print(f"访问: {token_url}")

        # 访问URL（不允许自动重定向）
        # 这样我们可以从响应头中获取重定向URL
        final_url = None
        try:
            response = session.get(token_url, allow_redirects=False, timeout=30)

            # 检查是否有重定向
            if response.status_code in (301, 302, 303, 307, 308):
                final_url = response.headers.get('Location', '')
                print(f"检测到重定向: {final_url[:100]}...")
            elif response.status_code == 200:
                # 检查响应内容中是否包含重定向
                # 有些实现会用JavaScript重定向
                content = response.text
                js_match = re.search(r'window\.location\s*=\s*["\']([^"\']+)["\']', content)
                if js_match:
                    final_url = js_match.group(1)
                    print(f"检测到JavaScript重定向: {final_url[:100]}...")
                else:
                    print(f"❌ 未检测到重定向，状态码: {response.status_code}")
                    print(f"   响应内容: {content[:200]}...")
                    return None, None

        except requests.exceptions.ConnectionError as e:
            # 有时候重定向到moodledl://会导致连接错误
            error_str = str(e)
            match = re.search(r'(moodledl://token=[\w=]+)', error_str)
            if match:
                final_url = match.group(1)
                print(f"从错误中提取token URL: {final_url[:100]}...")
            else:
                print(f"❌ 连接错误但无法提取token: {error_str[:200]}")
                return None, None
        except Exception as e:
            print(f"❌ 请求失败: {e}")
            return None, None

        if not final_url:
            print(f"❌ 无法获取重定向URL")
            return None, None

        # 从URL中提取token
        # 格式: moodledl://token=BASE64STRING
        match = re.search(r'token=([\w=]+)', final_url)
        if not match:
            print(f"❌ 无法从URL中提取token: {final_url}")
            return None, None

        app_token = match.group(1)

        # 解码Base64 token
        # 格式: token:::privatetoken
        try:
            decoded = base64.b64decode(app_token).decode('utf-8')
            parts = decoded.split(':::')

            if len(parts) == 2:
                # ⚠️ 重要：Moodle mobile token格式为 "app_token:::web_service_token"
                # moodle-dl使用第二部分（parts[1]）作为API token
                mobile_app_token = parts[0]  # 用于mobile app
                web_service_token = parts[1]   # 用于Web Service API（这是真正的API token）

                print(f"✅ 成功提取API token")
                print(f"   Web Service Token: {web_service_token[:20]}...")
                print(f"   Mobile App Token: {mobile_app_token[:20]}...")

                # 保存时：token字段保存web_service_token（moodle-dl会使用这个）
                # privatetoken字段保存mobile_app_token（用于mobile app，如果需要的话）
                save_token_to_config(domain, web_service_token, mobile_app_token, cookies_file)

                return web_service_token, mobile_app_token
            else:
                print(f"❌ Token格式不正确: {decoded}")
                return None, None

        except Exception as e:
            print(f"❌ 解码token失败: {e}")
            return None, None

    except Exception as e:
        print(f"❌ 获取API token失败: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def save_token_to_config(domain: str, token: str, privatetoken: str, cookies_file: str):
    """
    保存token到moodle-dl配置文件

    Args:
        domain: Moodle域名
        token: API token
        privatetoken: Private token
        cookies_file: cookies文件路径（用于定位配置目录）
    """
    try:
        import json

        # 配置文件应该在cookies文件同一目录
        config_dir = os.path.dirname(cookies_file)
        config_file = os.path.join(config_dir, 'config.json')

        if not os.path.exists(config_file):
            print(f"⚠️  配置文件不存在: {config_file}")
            print(f"   Token已获取但未保存，你需要手动配置")
            print(f"   Token: {token}")
            print(f"   Private token: {privatetoken}")
            return

        # 读取现有配置
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # 更新token
        config['token'] = token
        config['privatetoken'] = privatetoken

        # 保存配置
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)

        print(f"✅ Token已保存到配置文件: {config_file}")

    except Exception as e:
        print(f"⚠️  保存token到配置文件失败: {e}")
        print(f"   但token已成功获取:")
        print(f"   Token: {token}")
        print(f"   Private token: {privatetoken}")


def export_cookies_interactive(domain: str = None, output_file: str = None, ask_browser: bool = True, auto_get_token: bool = False):
    """
    交互式导出 cookies（可被其他模块调用）

    Args:
        domain: Moodle 域名（例如 moodle.example.com，如果为 None 则会提示用户输入）
        output_file: 输出文件路径（默认为当前目录的 Cookies.txt）
        ask_browser: 是否询问用户选择浏览器（默认 True）
        auto_get_token: 是否自动获取 API token 而不询问用户（默认 False）

    Returns:
        bool: 是否成功导出并验证 cookies
    """
    # 如果没有提供域名，询问用户
    if domain is None:
        print("请输入你的 Moodle 网站域名")
        print("示例: moodle.university.edu 或 elearning.school.com")
        domain = input("Moodle 域名: ").strip()
        if not domain:
            print("❌ 域名不能为空")
            return False
    if output_file is None:
        output_file = os.path.join(os.getcwd(), 'Cookies.txt')

    print("=" * 80)
    print("Moodle Browser Cookies 导出工具")
    print("=" * 80)
    print(f"域名: {domain}")
    print(f"输出文件: {output_file}")
    print("=" * 80)

    if ask_browser:
        # 询问用户选择浏览器
        print("\n请选择你使用的浏览器或内核：")
        print("1. Chrome")
        print("2. Edge")
        print("3. Firefox")
        print("4. Safari")
        print("5. Chromium 内核浏览器（Brave, Vivaldi, Arc, Opera 等）")
        print("6. Firefox 内核浏览器（Zen, Waterfox, LibreWolf 等）")
        print("7. 自动检测所有浏览器")
        print("8. 手动指定浏览器名称")

        while True:
            try:
                choice = input("\n请输入选项 (1-8): ").strip()
                if choice == '1':
                    browsers_to_try = ['chrome']
                    break
                elif choice == '2':
                    browsers_to_try = ['edge']
                    break
                elif choice == '3':
                    browsers_to_try = ['firefox']
                    break
                elif choice == '4':
                    browsers_to_try = ['safari']
                    break
                elif choice == '5':
                    # Chromium 内核浏览器 - 二级选择
                    print("\n请选择具体的 Chromium 内核浏览器：")
                    print("1. Chrome")
                    print("2. Brave")
                    print("3. Vivaldi")
                    print("4. Opera")
                    print("5. Chromium")
                    print("6. Arc（通过自定义路径支持）")

                    chromium_choice = input("\n请输入选项 (1-6): ").strip()
                    chromium_map = {
                        '1': 'chrome',
                        '2': 'brave',      # ✅ 有专门的 brave() 方法
                        '3': 'vivaldi',    # ✅ 有专门的 vivaldi() 方法
                        '4': 'opera',      # ✅ 有专门的 opera() 方法
                        '5': 'chromium',   # ✅ 有专门的 chromium() 方法
                        '6': 'arc',        # ✅ 通过自定义路径支持
                    }
                    selected = chromium_map.get(chromium_choice)

                    if selected:
                        browsers_to_try = [selected]
                        break
                    else:
                        print("❌ 无效选项，请输入 1-6")
                        continue

                elif choice == '6':
                    # Firefox 内核浏览器 - 二级选择
                    print("\n请选择具体的 Firefox 内核浏览器：")
                    print("1. Firefox")
                    print("2. LibreWolf")
                    print("3. Zen Browser（通过自定义路径支持）")
                    print("4. Waterfox（通过自定义路径支持）")

                    firefox_choice = input("\n请输入选项 (1-4): ").strip()
                    firefox_map = {
                        '1': 'firefox',
                        '2': 'librewolf',  # ✅ 有专门的 librewolf() 方法
                        '3': 'zen',        # ✅ 通过自定义路径支持
                        '4': 'waterfox',   # ✅ 通过自定义路径支持
                    }
                    selected = firefox_map.get(firefox_choice)

                    if selected:
                        browsers_to_try = [selected]
                        break
                    else:
                        print("❌ 无效选项，请输入 1-4")
                        continue
                elif choice == '7':
                    # 自动检测所有浏览器 - 包含所有支持的浏览器
                    browsers_to_try = [
                        'chrome', 'brave', 'vivaldi', 'opera', 'chromium', 'edge',
                        'firefox', 'librewolf',
                        'safari'
                    ]
                    print("\n将依次尝试：Chrome, Brave, Vivaldi, Opera, Chromium, Edge, Firefox, LibreWolf, Safari")
                    break
                elif choice == '8':
                    # 手动指定浏览器
                    print("\n✅ 支持的浏览器：")
                    print("   • 直接支持：chrome, firefox, brave, vivaldi, opera, edge, chromium, librewolf, safari")
                    print("   • 通过自定义路径支持：zen, waterfox, arc")
                    custom_browser = input("\n请输入浏览器名称: ").strip().lower()
                    if custom_browser:
                        browsers_to_try = [custom_browser]
                        break
                    else:
                        print("❌ 浏览器名称不能为空")
                else:
                    print("❌ 无效选项，请输入 1-8")
            except (KeyboardInterrupt, EOFError):
                print("\n\n取消导出")
                return False
    else:
        # 自动检测所有浏览器（向后兼容）
        browsers_to_try = ['chrome', 'edge', 'firefox', 'safari']

    # 尝试从选定的浏览器导出
    success = False
    selected_browser = None
    for browser in browsers_to_try:
        print(f"\n尝试从 {browser} 导出...")
        if export_cookies_from_browser(domain, output_file, browser):
            success = True
            selected_browser = browser
            break

    if not success:
        print("\n" + "=" * 80)
        print("❌ 导出失败")
        print("\n请手动导出 cookies：")
        print("1. 安装浏览器扩展 'Get cookies.txt LOCALLY'")
        print(f"2. 在浏览器中登录 https://{domain}")
        print("3. 点击扩展图标，导出 cookies")
        print(f"4. 保存到: {output_file}")
        print("=" * 80)
        return False

    # 🚀 使用完全自动化的 SSO 登录来刷新 cookies
    # 只要 SSO cookies 有效，完全无需手动操作
    print("\n🔄 正在使用自动 SSO 登录刷新 cookies...")
    print("   （只要 Microsoft/Google 的 SSO cookies 有效，将完全自动化）")

    try:
        # 导入自动 SSO 登录模块
        # 注意：这个脚本可能从不同位置被调用，需要动态导入
        import importlib.util
        import sys

        # 尝试直接导入（如果在 moodle-dl 环境中）
        try:
            from moodle_dl.auto_sso_login import auto_login_with_sso_sync
        except ImportError:
            # 回退：尝试从文件路径加载
            auto_sso_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                'moodle_dl', 'auto_sso_login.py'
            )
            if os.path.exists(auto_sso_path):
                spec = importlib.util.spec_from_file_location("auto_sso_login", auto_sso_path)
                auto_sso_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(auto_sso_module)
                auto_login_with_sso_sync = auto_sso_module.auto_login_with_sso_sync
            else:
                raise ImportError("Cannot find auto_sso_login module")

        # 使用自动 SSO 登录刷新 cookies
        refresh_success = auto_login_with_sso_sync(
            moodle_domain=domain,
            cookies_path=output_file,
            preferred_browser=selected_browser,
            headless=True  # 后台运行
        )

        if not refresh_success:
            # 自动 SSO 登录失败 - 说明 SSO cookies 完全过期
            print("\n" + "=" * 80)
            print("⚠️  自动 SSO 登录失败 - SSO cookies 已完全过期")
            print("=" * 80)
            print("\n💡 解决方案：")
            print(f"   在{selected_browser}浏览器中访问 {domain} 并完成 SSO 登录")
            print(f"   登录后，SSO cookies 会自动保存到浏览器")
            print(f"   然后重新运行此命令，将能够完全自动化")
            print("\n📌 这是唯一需要手动操作的场景（SSO cookies 完全过期时）")
            print("   之后的所有操作都将完全自动化，无需再次手动登录")
            print("=" * 80)
            return False

    except Exception as e:
        print(f"\n⚠️  自动 SSO 登录出错: {e}")
        print("   回退到测试现有 cookies...")
        refresh_success = False

    # 测试 cookies
    cookies_valid = test_cookies(domain, output_file)

    if cookies_valid:
        print("\n" + "=" * 80)
        print("✅ Cookies 导出成功并已验证！")
        print("=" * 80)

        # 获取API token - 始终自动获取，不询问用户
        if auto_get_token or ask_browser:
            # API token是必需的，直接自动获取
            print("\n正在自动获取Moodle API token...")
            print("（API token用于通过Web Service API下载课程内容）")
            token, privatetoken = extract_api_token_with_cookies(domain, output_file)
            if token and privatetoken:
                print(f"✅ 已成功获取并保存API token!")
            else:
                print(f"⚠️  API token获取失败，你可以稍后手动运行: moodle-dl --new-token --sso")

    return cookies_valid


def main():
    """命令行入口"""
    # 从命令行参数获取配置
    domain = None
    output_file = os.path.join(os.getcwd(), 'Cookies.txt')

    if len(sys.argv) > 1:
        domain = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]

    # 如果没有提供域名，export_cookies_interactive 会提示用户输入
    success = export_cookies_interactive(domain, output_file)

    if not success:
        sys.exit(1)

    print("\n下一步：运行 moodle-dl 下载内容")
    print(f"moodle-dl --path {os.path.dirname(output_file) or '.'}")

if __name__ == '__main__':
    main()
