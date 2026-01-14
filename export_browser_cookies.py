#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
 Moodle cookies

:pip install browser-cookie3
"""

import os
import sys
import glob
import platform
import asyncio
import http.cookiejar
import re
import base64
from typing import TYPE_CHECKING

try:
    import browser_cookie3
except ImportError:
    print(": browser-cookie3")
    print(":pip install browser-cookie3")
    sys.exit(1)

# Playwright 是可选依赖，用于 SSO 登录
async_playwright = None

if TYPE_CHECKING:
    # 仅在类型检查时导入，避免运行时错误
    from playwright.async_api import async_playwright as _async_playwright  # type: ignore[import-not-found]
else:
    try:
        from playwright.async_api import async_playwright as _async_playwright  # type: ignore[import-not-found]
        async_playwright = _async_playwright
    except ImportError:
        pass


def normalize_cookie_for_playwright(cookie: dict) -> dict:
    """
    标准化 cookie 格式为 Playwright 要求的格式（防御性编程）
    
    处理不同来源的 cookies 字段差异：
    - 确保 secure 和 httpOnly 是布尔值（不是整数 0/1）
    - 统一字段命名：httpOnly（不是 httponly），sameSite（不是 samesite）
    - 确保 expires 符合 Playwright 规范：
      - **会话 cookie**：不包含 expires 字段（而不是 -1）
      - **持久 cookie**：expires 为正整数秒级时间戳
    - 移除非 Playwright 字段（如 cookie_id）
    
    Args:
        cookie: 原始 cookie 字典
        
    Returns:
        标准化后的 cookie 字典（Playwright 格式）
    """
    cleaned = cookie.copy()
    
    # 移除数据库专用字段
    cleaned.pop('cookie_id', None)
    
    # 处理 expires 字段（Playwright 规范：会话 cookie 不应包含 expires 字段）
    # - Playwright Cookie.expires: Unix time seconds (float/int), optional
    # - 若传入 -1/0/None，可能导致 cookie 被视为已过期或被忽略
    expires_value = cleaned.get('expires')
    try:
        if expires_value is None or expires_value == '' or expires_value == -1:
            cleaned.pop('expires', None)
        elif isinstance(expires_value, str):
            expires_str = expires_value.strip()
            if expires_str == '' or expires_str == '0' or expires_str == '-1':
                cleaned.pop('expires', None)
            else:
                cleaned['expires'] = int(float(expires_str))
        elif isinstance(expires_value, (int, float)):
            if expires_value <= 0:
                cleaned.pop('expires', None)
            elif expires_value > 10000000000:
                # 毫秒级时间戳 → 转换为秒级
                cleaned['expires'] = int(expires_value / 1000)
            else:
                cleaned['expires'] = int(expires_value)
        else:
            cleaned.pop('expires', None)
    except Exception:
        # 任何异常都降级为会话 cookie
        cleaned.pop('expires', None)
    
    # 统一 secure 字段为布尔值
    if 'secure' in cleaned:
        cleaned['secure'] = bool(cleaned['secure'])
    
    # 统一 httpOnly 字段（支持 httponly 和 httpOnly）
    if 'httponly' in cleaned or 'httpOnly' in cleaned:
        http_only_value = cleaned.pop('httponly', cleaned.get('httpOnly', False))
        cleaned['httpOnly'] = bool(http_only_value)
    
    # 统一 sameSite 字段（支持 samesite 和 sameSite）
    # 注意：不要强行设置默认 sameSite='Lax'。
    # 对 OIDC/SSO 相关 cookie 来说，错误的 SameSite 会导致登录态无法在重定向中生效。
    if 'samesite' in cleaned or 'sameSite' in cleaned:
        same_site_value = cleaned.pop('samesite', cleaned.get('sameSite', None))
        if isinstance(same_site_value, str):
            s = same_site_value.strip().lower()
            if s == 'lax':
                cleaned['sameSite'] = 'Lax'
            elif s == 'strict':
                cleaned['sameSite'] = 'Strict'
            elif s == 'none':
                cleaned['sameSite'] = 'None'
            else:
                cleaned.pop('sameSite', None)
        else:
            cleaned.pop('sameSite', None)
    
    # 确保必需字段存在且类型正确
    cleaned.setdefault('path', '/')
    cleaned.setdefault('secure', False)
    cleaned.setdefault('httpOnly', False)
    
    return cleaned


def find_browser_cookie_path(browser_name: str) -> str:
    """
     cookie 

    Args:
        browser_name:  (zen, waterfox, arc )

    Returns:
        cookie , None
    """
    system = platform.system()

    # 
    browser_paths = {
        'zen': {
            'Darwin': '~/Library/Application Support/Zen/Profiles/*/cookies.sqlite',
            'Linux': ['~/.zen/Profiles/*/cookies.sqlite',
                     '~/.var/app/app.zen_browser.zen/zen/Profiles/*/cookies.sqlite'],  # Flatpak
            'Windows': os.path.join(os.getenv('APPDATA', ''), 'Zen', 'Profiles', '*', 'cookies.sqlite')
        },
        'waterfox': {
            'Darwin': '~/Library/Application Support/Waterfox/Profiles/*.default*/cookies.sqlite',
            'Linux': '~/.waterfox/Profiles/*.default*/cookies.sqlite',
            'Windows': os.path.join(os.getenv('APPDATA', ''), 'Waterfox', 'Profiles', '*.default*', 'cookies.sqlite')
        },
        'arc': {
            'Darwin': '~/Library/Application Support/Arc/User Data/*/Cookies',
            'Linux': None,  # Arc  Linux
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

    # ( Flatpak)
    if isinstance(paths, list):
        for path_pattern in paths:
            expanded = os.path.expanduser(path_pattern)
            matches = glob.glob(expanded)
            if matches:
                # 
                return max(matches, key=os.path.getmtime)
    else:
        # 
        expanded = os.path.expanduser(paths)
        matches = glob.glob(expanded)
        if matches:
            # ( profile)
            return max(matches, key=os.path.getmtime)

    return None


def _repair_firefox_cookies_db():
    """
    Firefoxcookies.sqlite

    ,.bak
    FirefoxWAL
    """
    import platform
    import os

    system = platform.system()

    # Firefox Profile
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

    # defaultdefault-release profile
    for profile_dir in os.listdir(profile_base):
        if 'default' not in profile_dir.lower():
            continue

        profile_path = os.path.join(profile_base, profile_dir)
        cookies_main = os.path.join(profile_path, 'cookies.sqlite')
        cookies_bak = os.path.join(profile_path, 'cookies.sqlite.bak')
        cookies_wal = os.path.join(profile_path, 'cookies.sqlite-wal')

        # 
        main_exists = os.path.exists(cookies_main) and os.path.getsize(cookies_main) > 0
        bak_exists = os.path.exists(cookies_bak) and os.path.getsize(cookies_bak) > 1024  # > 1KB

        if not main_exists and bak_exists:
            # ,
            try:
                import shutil
                shutil.copy(cookies_bak, cookies_main)

                # WAL()
                if os.path.exists(cookies_wal):
                    os.remove(cookies_wal)

                return True
            except Exception as e:
                continue

    return False


def get_cookies_from_browser(domain: str, browser_name='chrome'):
    """
     cookies, cookie ()
    
    Returns:
        List[browser_cookie3.Cookie]  None
    """
    print(f" {browser_name}  cookies...")
    
    # Firefox:
    if browser_name.lower() == 'firefox':
        try:
            if _repair_firefox_cookies_db():
                print(f"  i  Firefox cookies")
        except Exception as e:
            pass  # ,browser_cookie3
    
    try:
        #  browser_cookie3 
        # : (, )
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
            # 
            'zen': (browser_cookie3.firefox, True),  # Zen  Firefox 
            'waterfox': (browser_cookie3.firefox, True),  # Waterfox  Firefox 
            'arc': (browser_cookie3.chrome, True),  # Arc  Chrome 
        }
        
        # 
        for method_name in ['opera_gx']:
            if hasattr(browser_cookie3, method_name):
                browser_methods[method_name] = (getattr(browser_cookie3, method_name), False)
        
        # 
        if browser_name in browser_methods:
            method, needs_custom_path = browser_methods[browser_name]
            
            if needs_custom_path:
                #  cookie 
                print(f"   {browser_name}  cookie ...")
                cookie_path = find_browser_cookie_path(browser_name)
                
                if not cookie_path:
                    print(f"  {browser_name}  cookie ")
                    return None
                
                print(f"    cookie : {cookie_path}")
                cj = method(cookie_file=cookie_path)
            else:
                # 
                cj = method()
        else:
            print(f" :{browser_name}")
            return None
        
        # 
        cookies_list = []
        #  Moodle ()
        moodle_main_domain = domain.split('.')[-2] + '.' + domain.split('.')[-1] if '.' in domain else domain
        
        for cookie in cj:
            #  Moodle  cookies  SSO cookies
            # : Moodle  cookies, cookies
            cookie_domain = cookie.domain.lstrip('.')
            
            #  Moodle  cookies
            if moodle_main_domain in cookie_domain:
                cookies_list.append(cookie)
            #  SSO cookies()
            elif any(sso in cookie_domain.lower() for sso in [
                'microsoft', 'google', 'okta', 'shibboleth',
                'saml', 'oauth', 'login', 'auth', 'sso'
            ]):
                cookies_list.append(cookie)
        
        if not cookies_list:
            print(f"  {domain}  cookies")
            return None
        
        print(f"  {len(cookies_list)}  cookies")
        
        #  cookies(Moodle session  SSO cookies)
        print("\n Cookies:")
        shown_cookies = set()
        for cookie in cookies_list:
            #  MoodleSession
            if 'moodle' in cookie.name.lower() and 'session' in cookie.name.lower():
                if cookie.name not in shown_cookies:
                    print(f"   {cookie.name}: {cookie.value[:30]}...")
                    shown_cookies.add(cookie.name)
            #  Moodle  cookies( SSO)
            elif moodle_main_domain not in cookie.domain.lstrip('.'):
                if cookie.name not in shown_cookies:
                    print(f"   {cookie.name} ({cookie.domain}): {cookie.value[:20]}...")
                    shown_cookies.add(cookie.name)
        
        return cookies_list
        
    except Exception as e:
        print(f" : {e}")
        return None


def export_cookies_from_browser(domain: str, output_file: str, browser_name='chrome'):
    """Export cookies from browser to a file"""
    
    print(f" {browser_name}  cookies...")
    
    #  cookies 
    cookies_list = get_cookies_from_browser(domain, browser_name)
    
    if not cookies_list:
        return False
    
    try:
        #  Moodle 
        moodle_main_domain = domain.split('.')[-2] + '.' + domain.split('.')[-1] if '.' in domain else domain
        
        # 
        if os.path.exists(output_file):
            backup_file = output_file + '.backup'
            os.rename(output_file, backup_file)
            print(f" : {backup_file}")
        
        #  Netscape cookie 
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
        
        print(f"  {len(cookies_list)}  cookies : {output_file}")
        return True
        
    except Exception as e:
        print(f" : {e}")
        return False


def test_cookies(domain: str, cookies_file: str) -> bool:
    """
    Test if cookies are valid
    
    Verify cookies by accessing Moodle URL to check if login is successful
    
    Args:
        domain: Moodle domain
        cookies_file: cookies file path
        
    Returns:
        bool: whether cookies are valid
    """
    try:
        import requests
        from http.cookiejar import MozillaCookieJar

        session = requests.Session()

        # Load cookies
        cookie_jar = MozillaCookieJar(cookies_file)
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
        session.cookies = cookie_jar

        # Check if MoodleSession exists
        has_moodle_session = any('moodle' in cookie.name.lower() and 'session' in cookie.name.lower() for cookie in cookie_jar)

        # Get main Moodle domain
        moodle_main_domain = domain.split('.')[-2] + '.' + domain.split('.')[-1] if '.' in domain else domain

        # Check for SSO cookies (cookies from non-Moodle domains)
        has_sso_cookies = any(
            moodle_main_domain not in cookie.domain.lstrip('.') and
            cookie.domain not in ['localhost', '127.0.0.1']
            for cookie in cookie_jar
        )

        # Test Moodle access
        moodle_url = f'https://{domain}/' if not domain.startswith('http') else domain
        response = session.get(moodle_url, timeout=10, allow_redirects=True)

        # Check response
        from urllib.parse import urlparse
        original_domain = urlparse(moodle_url).netloc
        final_domain = urlparse(response.url).netloc

        if 'login/logout.php' in response.text:
            print("✓ Cookies validation successful!")
            return True
        elif 'login/index.php' in response.url and original_domain == final_domain:
            # Redirected back to login page = cookies invalid
            print("✗ Cookies validation failed")
            print(f"    Redirected to login page: {domain}")
            return False
        elif original_domain != final_domain:
            # Domain changed = SSO flow
            if has_moodle_session and has_sso_cookies:
                print("✓ Cookies validation successful (SSO scenario with necessary cookies)")
                print(f"   Redirected to: SSO provider ({final_domain})")
                print("    SSO flow completed")
                return True
            else:
                print(f"✗ SSO validation failed ({final_domain}), incomplete cookies info")
                print(f"   MoodleSession: {'✓' if has_moodle_session else '✗'}")
                print(f"   SSO cookies: {'✓' if has_sso_cookies else '✗'}")
                return False
        else:
            print("? Validation uncertain ")
            print(f"    URL: {response.url}")
            # If MoodleSession exists, consider valid
            if has_moodle_session:
                print("    MoodleSession detected in cookies")
                return True
            return False

    except Exception as e:
        print(f"✗ Validation failed: {e}")
        return False


def convert_netscape_to_playwright(cookies_file: str) -> list:
    """
    NetscapecookiesPlaywright

    Args:
        cookies_file: Netscapecookies

    Returns:
        Playwrightcookies
    """
    try:
        import http.cookiejar

        cookie_jar = http.cookiejar.MozillaCookieJar(cookies_file)
        cookie_jar.load(ignore_discard=True, ignore_expires=True)

        playwright_cookies = []
        for cookie in cookie_jar:
            playwright_cookie = {
                'name': cookie.name,
                'value': cookie.value,
                'domain': cookie.domain,
                'path': cookie.path,
                'httpOnly': bool(cookie.has_nonstandard_attr('HttpOnly')),
                'secure': cookie.secure,
            }

            # Playwright：会话 cookie 不应包含 expires
            if cookie.expires is not None and cookie.expires > 0:
                expires_value = cookie.expires
                if expires_value > 10000000000:
                    expires_value = int(expires_value / 1000)
                else:
                    expires_value = int(expires_value)
                playwright_cookie['expires'] = expires_value

            same_site = cookie.get_nonstandard_attr('SameSite', 'Lax')
            if same_site:
                playwright_cookie['sameSite'] = same_site

            playwright_cookies.append(playwright_cookie)

        return playwright_cookies
    except Exception as e:
        print(f" cookies: {e}")
        return []


def save_playwright_cookies_to_netscape(playwright_cookies: list, output_file: str) -> bool:
    """
    PlaywrightcookiesNetscape

    Args:
        playwright_cookies: Playwrightcookies
        output_file: 

    Returns:
        
    """
    try:
        with open(output_file, 'w') as f:
            # 
            f.write('# Netscape HTTP Cookie File\n')
            f.write('# This file is generated by moodle-dl.  Do not edit.\n\n')

            for cookie in playwright_cookies:
                domain = cookie.get('domain', '')
                flag = 'TRUE' if domain.startswith('.') else 'FALSE'
                path = cookie.get('path', '/')
                secure = 'TRUE' if cookie.get('secure', False) else 'FALSE'
                expires = cookie.get('expires', 0)
                # expires:Playwright-1session cookie
                if expires == -1:
                    expires = 0
                name = cookie.get('name', '')
                value = cookie.get('value', '')

                f.write(f'{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n')

        return True
    except Exception as e:
        print(f" cookies: {e}")
        return False


#  : auto_refresh_session_with_sso()
#  auto_sso_login.py  auto_login_with_sso_sync() 
#  DRY ,


def extract_api_token_with_playwright(domain: str, cookies_file: str):
    """
    Playwright + cookiesMoodle API token

    tokenURL,SSO
    moodledl://token=...

    Args:
        domain: Moodle
        cookies_file: cookies

    Returns:
        tuple: (token, privatetoken) , (None, None)
    """
    print("\nPlaywrightAPI token...")

    try:
        if async_playwright is None:
            raise ImportError("Playwright not installed. Install with: pip install playwright")

        # cookiesPlaywright
        print("  -> cookies...")
        cookie_jar = http.cookiejar.MozillaCookieJar(cookies_file)
        cookie_jar.load(ignore_discard=True, ignore_expires=True)

        playwright_cookies = []
        for cookie in cookie_jar:
            # expires()
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
                'secure': bool(cookie.secure),  # 确保是布尔值（Playwright 要求）
            }

            same_site = cookie.get_nonstandard_attr('SameSite', 'Lax')
            if same_site:
                playwright_cookie['sameSite'] = same_site

            playwright_cookies.append(playwright_cookie)

        print(f"  ->  {len(playwright_cookies)} cookies")

        # tokenURL
        moodle_url = f'https://{domain}' if not domain.startswith('http') else domain
        token_url = f"{moodle_url}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&urlscheme=moodledl"

        # Playwright
        async def get_token():
            captured_urls = []

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                await context.add_cookies(playwright_cookies)

                page = await context.new_page()

                #  :MoodleSession
                # MoodleSession cookie
                print(f"  -> MoodleSession...")
                try:
                    await page.goto(f"{moodle_url}/my/", wait_until='domcontentloaded', timeout=15000)
                    await page.wait_for_timeout(1000)  # cookies
                    print(f"  -> Session")
                except Exception as e:
                    print(f"  -> : session ({str(e)[:50]}...)")

                # ()
                def handle_console(msg):
                    text = msg.text
                    if 'moodledl://' in text or 'moodlemobile://' in text:
                        captured_urls.append(text)
                        print(f"  -> : {text[:80]}...")

                page.on('console', handle_console)

                # ()
                def handle_request(request):
                    url = request.url
                    if 'moodledl://' in url or 'moodlemobile://' in url:
                        captured_urls.append(url)
                        print(f"  -> : {url[:80]}...")

                page.on('request', handle_request)

                # 
                def handle_response(response):
                    # Location header
                    location = response.headers.get('location', '')
                    if 'moodledl://' in location or 'moodlemobile://' in location:
                        captured_urls.append(location)
                        print(f"  -> : {location[:80]}...")

                page.on('response', handle_response)

                try:
                    # token URL,moodledl://
                    print(f"  -> token...")

                    # wait_for_load_statewait_until,
                    response = await page.goto(token_url, wait_until='load', timeout=30000)

                    # ,
                    await page.wait_for_timeout(2000)

                    # token URL
                    content = await page.content()
                    if 'moodledl://' in content or 'moodlemobile://' in content:
                        # HTMLtoken URL
                        token_match = re.search(r'(moodledl://token=[\w=]+)', content)
                        if not token_match:
                            token_match = re.search(r'(moodlemobile://token=[\w=]+)', content)
                        if token_match:
                            captured_urls.append(token_match.group(1))
                            print(f"  -> : {token_match.group(1)[:80]}...")

                except Exception as e:
                    # (moodledl://)
                    error_str = str(e)
                    print(f"  -> (): {error_str[:100]}...")

                    # token URL
                    if 'moodledl://' in error_str or 'moodlemobile://' in error_str:
                        match = re.search(r'(moodledl://token=[\w=]+)', error_str)
                        if not match:
                            match = re.search(r'(moodlemobile://token=[\w=]+)', error_str)
                        if match:
                            captured_urls.append(match.group(1))
                            print(f"  -> : {match.group(1)[:80]}...")

                await browser.close()

                # URL
                if captured_urls:
                    # token URL
                    for url in captured_urls:
                        if 'token=' in url:
                            return url
                return None

        # 
        token_redirect_url = asyncio.run(get_token())

        if not token_redirect_url:
            print("   tokenURL")
            return None, None

        # URLtoken
        print(f"  -> token...")
        match = re.search(r'token=([\w=]+)', token_redirect_url)
        if not match:
            print(f"   URLtoken")
            return None, None

        app_token = match.group(1)

        # Base64 token
        try:
            decoded = base64.b64decode(app_token).decode('utf-8')
            parts = decoded.split(':::')

            if len(parts) == 2:
                #  :Moodle mobile token "app_token:::web_service_token"
                # moodle-dl(parts[1])API token
                # moodle-dl
                mobile_app_token = parts[0]  # mobile app
                web_service_token = parts[1]   # Web Service API(API token)

                print(f"   API token")
                print(f"     Web Service Token: {web_service_token[:20]}...")
                print(f"     Mobile App Token: {mobile_app_token[:20]}...")

                # :tokenweb_service_token(moodle-dl)
                # privatetokenmobile_app_token(mobile app,)
                save_token_to_config(domain, web_service_token, mobile_app_token, cookies_file)

                return web_service_token, mobile_app_token
            else:
                print(f"   Token")
                return None, None

        except Exception as e:
            print(f"   token: {e}")
            return None, None

    except ImportError as e:
        print(f"   Playwright: {e}")
        print(f"  -> : pip install playwright && playwright install chromium")
        return None, None
    except Exception as e:
        print(f"   API token: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def extract_api_token_with_cookies(domain: str, cookies_file: str):
    """
    cookiesMoodle API token

    Playwright(),requests

    Args:
        domain: Moodle
        cookies_file: cookies

    Returns:
        tuple: (token, privatetoken) , (None, None)
    """
    # Playwright
    token, privatetoken = extract_api_token_with_playwright(domain, cookies_file)
    if token and privatetoken:
        return token, privatetoken

    # requests(SSO)
    print("\nHTTPAPI token...")
    print("(:SSO,)")

    try:
        import requests
        from http.cookiejar import MozillaCookieJar
        import re
        import base64

        # cookies
        session = requests.Session()
        cookie_jar = MozillaCookieJar(cookies_file)
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
        session.cookies = cookie_jar

        # tokenURL
        moodle_url = f'https://{domain}' if not domain.startswith('http') else domain
        token_url = f"{moodle_url}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&urlscheme=moodledl"

        print(f": {token_url}")

        # URL()
        # URL
        final_url = None
        try:
            response = session.get(token_url, allow_redirects=False, timeout=30)

            # 
            if response.status_code in (301, 302, 303, 307, 308):
                final_url = response.headers.get('Location', '')
                print(f": {final_url[:100]}...")
            elif response.status_code == 200:
                # 
                # JavaScript
                content = response.text
                js_match = re.search(r'window\.location\s*=\s*["\']([^"\']+)["\']', content)
                if js_match:
                    final_url = js_match.group(1)
                    print(f"JavaScript: {final_url[:100]}...")
                else:
                    print(f" ,: {response.status_code}")
                    print(f"   : {content[:200]}...")
                    return None, None

        except requests.exceptions.ConnectionError as e:
            # moodledl://
            error_str = str(e)
            match = re.search(r'(moodledl://token=[\w=]+)', error_str)
            if match:
                final_url = match.group(1)
                print(f"token URL: {final_url[:100]}...")
            else:
                print(f" token: {error_str[:200]}")
                return None, None
        except Exception as e:
            print(f" : {e}")
            return None, None

        if not final_url:
            print(f" URL")
            return None, None

        # URLtoken
        # : moodledl://token=BASE64STRING
        match = re.search(r'token=([\w=]+)', final_url)
        if not match:
            print(f" URLtoken: {final_url}")
            return None, None

        app_token = match.group(1)

        # Base64 token
        # : token:::privatetoken
        try:
            decoded = base64.b64decode(app_token).decode('utf-8')
            parts = decoded.split(':::')

            if len(parts) == 2:
                #  :Moodle mobile token "app_token:::web_service_token"
                # moodle-dl(parts[1])API token
                mobile_app_token = parts[0]  # mobile app
                web_service_token = parts[1]   # Web Service API(API token)

                print(f" API token")
                print(f"   Web Service Token: {web_service_token[:20]}...")
                print(f"   Mobile App Token: {mobile_app_token[:20]}...")

                # :tokenweb_service_token(moodle-dl)
                # privatetokenmobile_app_token(mobile app,)
                save_token_to_config(domain, web_service_token, mobile_app_token, cookies_file)

                return web_service_token, mobile_app_token
            else:
                print(f" Token: {decoded}")
                return None, None

        except Exception as e:
            print(f" token: {e}")
            return None, None

    except Exception as e:
        print(f" API token: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def extract_api_token_with_playwright_from_cookies(domain: str, cookies: list):
    """
    使用 Playwright + cookies 自动提取 Moodle API token
    
    **v2: 直接接收 cookies 列表，无需文件**
    
    Args:
        domain: Moodle 域名
        cookies: Playwright 格式的 cookies 列表
        
    Returns:
        tuple: (token, privatetoken) 或 (None, None) 表示失败
    """
    import logging
    
    logging.info("\n🔍 [Playwright] 开始使用 Playwright 提取 API token...")
    logging.debug(f'🔍 [Playwright] 输入参数: domain={domain}, cookies数量={len(cookies)}')
    print("\n🔍 使用 Playwright 自动获取 API token...")
    print(f"  -> 已加载 {len(cookies)} 个 cookies ...")
    
    try:
        if async_playwright is None:
            error_msg = "Playwright not installed. Install with: pip install playwright"
            logging.error(f'❌ [Playwright] {error_msg}')
            raise ImportError(error_msg)
        
        # 构建 token 提取 URL
        moodle_url = f'https://{domain}' if not domain.startswith('http') else domain
        token_url = f"{moodle_url}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&urlscheme=moodledl"
        logging.debug(f'🔍 [Playwright] Moodle URL: {moodle_url}')
        logging.debug(f'🔍 [Playwright] Token URL: {token_url}')

        def _extract_token_from_moodledl_url(moodledl_url: str):
            """从 moodledl://token=... URL 中解码出 (web_service_token, mobile_app_token)"""
            if 'token=' not in moodledl_url:
                return None, None
            match = re.search(r'token=([^&\\s]+)', moodledl_url)
            if not match:
                return None, None
            token_encoded = match.group(1)
            decoded = base64.b64decode(token_encoded).decode('utf-8')
            parts = decoded.split(':::')
            if len(parts) >= 2:
                mobile_app_token = parts[0]
                web_service_token = parts[1]
                return web_service_token, mobile_app_token
            return None, None

        def _try_requests_location_fallback() -> tuple:
            """
            最可靠的方式：不用 Playwright 事件捕获，直接用 requests 拿 launch.php 的 302 Location。
            你在另一个工具里看到的：
              [GET] launch.php => [302]
              [GET] moodledl://token=...
            其中 moodledl://... 本质就是 302 Location。
            """
            try:
                import requests
            except Exception as e:
                logging.warning(f'⚠️  [RequestsFallback] 无法导入 requests: {e}')
                return None, None

            sess = requests.Session()
            # 只注入 keats 域的 cookie 即可（其他域对这个请求无意义）
            jar = requests.cookies.RequestsCookieJar()
            for c in cleaned_cookies:
                try:
                    dom = c.get('domain') or ''
                    if 'keats.kcl.ac.uk' not in dom:
                        continue
                    jar.set(
                        name=c.get('name'),
                        value=c.get('value'),
                        domain=dom.lstrip('.'),
                        path=c.get('path') or '/',
                    )
                except Exception:
                    continue
            sess.cookies = jar

            logging.info('🔍 [RequestsFallback] 尝试用 requests 获取 launch.php 的 Location（allow_redirects=False）...')
            try:
                resp = sess.get(token_url, allow_redirects=False, timeout=20)
            except Exception as e:
                logging.warning(f'⚠️  [RequestsFallback] 请求失败: {e}')
                return None, None

            loc = resp.headers.get('Location') or resp.headers.get('location')
            logging.info(f'🔍 [RequestsFallback] 状态码: {resp.status_code}')
            if loc:
                logging.info(f'🔍 [RequestsFallback] Location: {loc[:200]}...')
            else:
                logging.warning('⚠️  [RequestsFallback] 响应没有 Location header（可能返回 200 HTML 或被重定向链拦截）')
                try:
                    logging.info(f'🔍 [RequestsFallback] 响应前200字符: {resp.text[:200].replace(chr(10), " ")}')
                except Exception:
                    pass
                return None, None

            if 'moodledl://' in loc or 'moodlemobile://' in loc:
                try:
                    return _extract_token_from_moodledl_url(loc)
                except Exception as e:
                    logging.warning(f'⚠️  [RequestsFallback] 解码 token 失败: {e}')
                    return None, None

            # 若 Location 仍指向 OIDC/登录页，说明会话未登录
            if 'login.microsoftonline.com' in loc or '/auth/oidc' in loc or '/login/' in loc:
                logging.warning('⚠️  [RequestsFallback] Location 指向登录/SSO，说明当前 cookies 不足以视为已登录')
            return None, None
        
        # 防御性编程：标准化所有 cookies 为 Playwright 格式
        logging.debug('🔍 [Playwright] 标准化 cookies 格式...')
        cleaned_cookies = []
        for i, c in enumerate(cookies):
            try:
                cleaned = normalize_cookie_for_playwright(c)
                cleaned_cookies.append(cleaned)
                if i < 3:  # 记录前3个 cookie 的详情
                    logging.debug(f'  Cookie {i+1}: name={cleaned.get("name")}, domain={cleaned.get("domain")}, secure={cleaned.get("secure")}')
            except Exception as e:
                logging.warning(f'⚠️  [Playwright] Cookie {i+1} 标准化失败: {e}')
        
        logging.debug(f'🔍 [Playwright] 成功标准化 {len(cleaned_cookies)}/{len(cookies)} 个 cookies')
        
        # 使用 Playwright 提取 token
        async def get_token():
            captured_urls = []
            page_final_url = None
            page_status = None
            last_location_headers = []
            
            async with async_playwright() as p:
                logging.debug('🔍 [Playwright] 启动 Chromium 浏览器...')
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                
                logging.debug(f'🔍 [Playwright] 添加 {len(cleaned_cookies)} 个 cookies 到浏览器上下文...')
                try:
                    await context.add_cookies(cleaned_cookies)
                    logging.debug('✅ [Playwright] Cookies 添加成功')
                except Exception as e:
                    logging.error(f'❌ [Playwright] 添加 cookies 失败: {e}')
                    logging.debug(f'🔍 [Playwright] 失败的 cookies 详情: {cleaned_cookies[:2]}')
                    raise

                # 立即验证 cookies 是否真的进入了上下文（这是判断重定向原因的关键）
                try:
                    ctx_cookies = await context.cookies(moodle_url)
                    moodle_session_in_ctx = any(c.get('name') == 'MoodleSession' for c in ctx_cookies)
                    # 这些信息对定位问题非常关键，提升到 INFO/WARNING 级别（无需 --verbose 也能看到）
                    logging.info(f'🔍 [Playwright] context.cookies({moodle_url}) = {len(ctx_cookies)}')
                    logging.info(f'🔍 [Playwright] context 内是否存在 MoodleSession: {moodle_session_in_ctx}')
                    # 进一步输出关键 cookie（能直接判断是否是“游客 MoodleSession”）
                    key_names = {'MoodleSession', 'MOODLEID', 'MOODLEID1_', 'MoodleSessionTest'}
                    key_cookies = [c for c in ctx_cookies if c.get('name') in key_names]
                    if key_cookies:
                        logging.info('🔍 [Playwright] context 内关键 cookie（最多10条）:')
                        for c in key_cookies[:10]:
                            logging.info(
                                f"  - name={c.get('name')} domain={c.get('domain')} path={c.get('path')} "
                                f"secure={c.get('secure')} httpOnly={c.get('httpOnly')} expires={c.get('expires')} "
                                f"value_prefix={(c.get('value') or '')[:12]}..."
                            )
                    if not moodle_session_in_ctx:
                        names = [c.get('name') for c in ctx_cookies if c.get('name')]
                        logging.warning('⚠️  [Playwright] context 内缺少 MoodleSession（很可能导致 /my/ 重定向到登录页）')
                        logging.warning(f'⚠️  [Playwright] context cookie 名称(前40个): {names[:40]}')
                except Exception as e:
                    logging.warning(f'⚠️  [Playwright] 读取 context.cookies() 失败: {e}')
                
                page = await context.new_page()
                
                # 步骤 1: 验证 Moodle 会话
                print(f"  -> 验证 Moodle 会话...")
                logging.debug(f'🔍 [Playwright] 步骤1: 访问 {moodle_url}/my/ 验证会话...')
                try:
                    response = await page.goto(f"{moodle_url}/my/", wait_until='domcontentloaded', timeout=15000)
                    page_status = response.status if response else None
                    page_final_url = page.url
                    logging.debug(f'🔍 [Playwright] 页面响应: status={page_status}, final_url={page_final_url[:80]}...')
                    # 直接把 /my/ 的首个响应头 Location 打出来（很多情况下这里就是 OIDC 重定向）
                    try:
                        if response is not None:
                            hdrs = response.headers or {}
                            loc = hdrs.get('location') or hdrs.get('Location')
                            if loc:
                                logging.info(f'🔍 [Playwright] /my/ 首响应 Location: {loc[:160]}...')
                    except Exception:
                        pass
                    
                    await page.wait_for_timeout(1000)  # 等待 cookies 生效
                    
                    # 检查是否成功登录
                    try:
                        page_title = await page.title()
                        logging.debug(f'🔍 [Playwright] 页面标题: {page_title[:50]}...')
                    except Exception as e:
                        # 常见：页面发生重定向导致 execution context 被销毁
                        logging.warning(f'⚠️  [Playwright] 读取页面标题失败（可能正在重定向）: {e}')
                        logging.info(f'🔍 [Playwright] /my/ 当前 URL: {page.url}')
                    
                    # 检查是否有重定向到登录页面
                    if 'login' in page_final_url.lower() or 'enrol' in page_final_url.lower() or 'microsoftonline.com' in page_final_url.lower():
                        logging.warning(f'⚠️  [Playwright] 检测到重定向到登录/注册页面: {page_final_url}')
                        # 额外打印一些页面线索（title + 少量内容），帮助判断是 OIDC 还是 Moodle 登录
                        try:
                            snippet = (await page.content())[:600].replace('\n', ' ')
                            logging.info(f'🔍 [Playwright] 登录页 HTML 片段(前600字符): {snippet}')
                        except Exception:
                            pass
                        print(f"  -> ⚠️  会话验证失败: 重定向到登录页面")
                    else:
                        logging.debug('✅ [Playwright] 会话验证成功')
                        print(f"  -> ✅ 会话验证成功")
                        
                except Exception as e:
                    error_msg = str(e)
                    logging.error(f'❌ [Playwright] 会话验证失败: {error_msg}')
                    logging.debug(f'🔍 [Playwright] 错误类型: {type(e).__name__}')
                    print(f"  -> ❌ 会话验证失败: {error_msg[:50]}...")
                
                # 步骤 2: 监听控制台消息
                def handle_console(msg):
                    text = msg.text
                    logging.debug(f'🔍 [Playwright] 控制台消息: {text[:100]}...')
                    if 'moodledl://' in text or 'moodlemobile://' in text:
                        captured_urls.append(text)
                        logging.info(f'✅ [Playwright] 从控制台捕获到 URL: {text[:80]}...')
                        print(f"  -> ✅ 从控制台捕获: {text[:80]}...")
                
                page.on('console', handle_console)
                
                # 步骤 3: 监听网络请求
                def handle_request(request):
                    url = request.url
                    logging.debug(f'🔍 [Playwright] 网络请求: {url[:100]}...')
                    if 'moodledl://' in url or 'moodlemobile://' in url:
                        captured_urls.append(url)
                        logging.info(f'✅ [Playwright] 从网络请求捕获到 URL: {url[:80]}...')
                        print(f"  -> ✅ 从网络请求捕获: {url[:80]}...")
                
                page.on('request', handle_request)
                
                # 步骤 4: 监听响应
                def handle_response(response):
                    url = response.url
                    status = response.status
                    logging.debug(f'🔍 [Playwright] 网络响应: {url[:100]}... status={status}')
                    # 关键：launch.php 往往返回 302/303，并把 moodledl://... 放在 Location header 里
                    try:
                        headers = response.headers or {}
                        location = headers.get('location') or headers.get('Location')
                        if location:
                            last_location_headers.append((url, status, location))
                            logging.debug(f'🔍 [Playwright] Location header: {location[:120]}...')
                            # 对关键重定向（keats/my/launch.php/oidc）提升到 INFO，方便你直接看到
                            if status in (301, 302, 303, 307, 308) and (
                                'keats.kcl.ac.uk' in url
                                or '/my/' in url
                                or 'admin/tool/mobile/launch.php' in url
                                or '/auth/oidc' in url
                            ):
                                logging.info(f'🔍 [Playwright] 关键重定向: status={status} url={url[:90]}... -> {location[:120]}...')
                            if 'moodledl://' in location or 'moodlemobile://' in location:
                                captured_urls.append(location)
                                logging.info(f'✅ [Playwright] 从响应 Location 捕获到 URL: {location[:80]}...')
                    except Exception:
                        pass
                    # 少见情况：URL 本身就是自定义 scheme（通常不会发生）
                    if 'moodledl://' in url or 'moodlemobile://' in url:
                        captured_urls.append(url)
                        logging.info(f'✅ [Playwright] 从响应 URL 捕获到 URL: {url[:80]}...')
                
                page.on('response', handle_response)
                
                # 步骤 5: 访问 token URL
                print(f"  -> 访问 token URL...")
                logging.debug(f'🔍 [Playwright] 步骤2: 访问 token URL: {token_url}')
                try:
                    response = await page.goto(token_url, wait_until='domcontentloaded', timeout=30000)
                    response_status = response.status if response else None
                    final_url = page.url
                    logging.debug(f'🔍 [Playwright] Token URL 响应: status={response_status}, final_url={final_url[:100]}...')
                    # 如果 response 自身就带 Location（重定向链末端），也记录一下
                    try:
                        if response is not None:
                            headers = response.headers or {}
                            location = headers.get('location') or headers.get('Location')
                            if location:
                                last_location_headers.append((response.url, response_status, location))
                                logging.info(f'🔍 [Playwright] Token goto() 返回的 Location: {location[:120]}...')
                                if 'moodledl://' in location or 'moodlemobile://' in location:
                                    captured_urls.append(location)
                                    logging.info(f'✅ [Playwright] 从 goto() Location 捕获到 URL: {location[:80]}...')
                    except Exception:
                        pass
                    
                    # 等待页面加载完成
                    await page.wait_for_timeout(2000)
                    
                    # 检查页面内容
                    try:
                        page_content = await page.content()
                        logging.debug(f'🔍 [Playwright] 页面内容长度: {len(page_content)} 字符')
                        if 'moodledl://' in page_content or 'moodlemobile://' in page_content:
                            logging.info('✅ [Playwright] 在页面内容中发现 token URL')
                    except Exception as e:
                        logging.debug(f'🔍 [Playwright] 读取页面内容失败: {e}')
                    
                except Exception as e:
                    error_msg = str(e)
                    if 'net::ERR_ABORTED' in error_msg or 'ERR_INVALID_URL' in error_msg:
                        logging.info('ℹ️  [Playwright] 预期的错误（URL scheme 不被浏览器支持）: ERR_ABORTED')
                        print(f"  -> ℹ️  预期的错误（URL scheme 不被浏览器支持）")
                    else:
                        logging.error(f'❌ [Playwright] 访问 token URL 失败: {error_msg}')
                        logging.debug(f'🔍 [Playwright] 错误类型: {type(e).__name__}')
                        print(f"  -> ❌ 访问失败: {error_msg[:50]}...")
                finally:
                    # 无论成功与否，都打印最近看到的 Location 头（高价值调试信息）
                    if last_location_headers:
                        logging.info(f'🔍 [Playwright] 最近捕获到的 Location headers（最多5条）:')
                        for (u, st, loc) in last_location_headers[-5:]:
                            logging.info(f'  - status={st} url={str(u)[:80]}... location={str(loc)[:120]}...')
                
                # 清理资源
                logging.debug('🔍 [Playwright] 清理浏览器资源...')
                for ctx in browser.contexts:
                    await ctx.close()
                await browser.close()
                
                logging.debug(f'🔍 [Playwright] 总共捕获到 {len(captured_urls)} 个 URL')
                return captured_urls
        
        logging.debug('🔍 [Playwright] 运行异步函数...')
        captured = asyncio.run(get_token())
        
        if not captured:
            logging.warning('⚠️  [Playwright] 未捕获到任何 token URL')
            print(f"  -> ⚠️  未捕获到 token URL")
            logging.debug('🔍 [Playwright] 可能的原因:')
            logging.debug('  1. Cookies 已过期或无效')
            logging.debug('  2. 页面未正确加载')
            logging.debug('  3. URL scheme 处理异常')
            logging.debug('  4. 网络请求被拦截')
            # 关键：Playwright 事件可能抓不到自定义 scheme 请求，使用 requests 直接读 302 Location 更可靠
            token2, private2 = _try_requests_location_fallback()
            if token2 and private2:
                logging.info('✅ [RequestsFallback] 成功通过 Location header 获取 token（无需 Playwright 捕获事件）')
                print("  -> ✅ 通过 HTTP Location 成功获取 token")
                print(f"   Web Service Token: {token2[:20]}...")
                print(f"   Mobile App Token: {private2[:20]}...")
                return token2, private2
            return None, None
        
        logging.info(f'✅ [Playwright] 捕获到 {len(captured)} 个 URL')
        print(f"  -> ✅ 捕获到 {len(captured)} 个 URL")
        
        # 解析 token
        logging.debug('🔍 [Playwright] 开始解析 token...')
        for i, url in enumerate(captured, 1):
            logging.debug(f'🔍 [Playwright] 解析 URL {i}/{len(captured)}: {url[:100]}...')
            if 'token=' in url:
                match = re.search(r'token=([^&\s]+)', url)
                if match:
                    token_encoded = match.group(1)
                    logging.debug(f'🔍 [Playwright] 提取到编码的 token: {token_encoded[:50]}...')
                    
                    try:
                        # 解码 base64
                        decoded = base64.b64decode(token_encoded).decode('utf-8')
                        logging.debug(f'🔍 [Playwright] Base64 解码成功，长度: {len(decoded)}')
                        parts = decoded.split(':::')  # 修复：应该是三个冒号，不是两个
                        logging.debug(f'🔍 [Playwright] 分割后部分数: {len(parts)}')
                        
                        if len(parts) >= 2:
                            mobile_app_token = parts[0]  # Mobile app token (private token)
                            web_service_token = parts[1]  # Web Service API token (API token)
                            
                            logging.info('✅ [Playwright] 成功解析 API token')
                            logging.debug(f'🔍 [Playwright] Web Service Token 长度: {len(web_service_token)}')
                            logging.debug(f'🔍 [Playwright] Mobile App Token 长度: {len(mobile_app_token)}')
                            print(f"  -> ✅ 成功解析 API token")
                            print(f"   Web Service Token: {web_service_token[:20]}...")
                            print(f"   Mobile App Token: {mobile_app_token[:20]}...")
                            
                            return web_service_token, mobile_app_token
                        else:
                            logging.warning(f'⚠️  [Playwright] Token 格式不正确，部分数不足: {len(parts)}')
                    except Exception as e:
                        logging.error(f'❌ [Playwright] Token 解析失败: {e}')
                        logging.debug(f'🔍 [Playwright] 错误类型: {type(e).__name__}')
                        logging.debug(f'🔍 [Playwright] 编码的 token: {token_encoded[:100]}...')
                        print(f"  -> ❌ Token 解析失败: {e}")
                        continue
        
        logging.warning('⚠️  [Playwright] 所有 URL 都无法解析出有效的 token')
        print(f"  -> ⚠️  无法解析 token")
        return None, None
        
    except Exception as e:
        error_msg = str(e)
        logging.error(f'❌ [Playwright] API token 提取过程出错: {error_msg}')
        logging.debug(f'🔍 [Playwright] 错误类型: {type(e).__name__}')
        print(f"  -> ❌ API token 提取失败: {error_msg}")
        import traceback
        full_traceback = traceback.format_exc()
        logging.debug(f'🔍 [Playwright] 完整错误堆栈:\n{full_traceback}')
        traceback.print_exc()
        return None, None


def save_token_to_config(domain: str, token: str, privatetoken: str, cookies_file: str):
    """
    tokenmoodle-dl

    Args:
        domain: Moodle
        token: API token
        privatetoken: Private token
        cookies_file: cookies()
    """
    try:
        import json

        # cookies
        config_dir = os.path.dirname(cookies_file)
        config_file = os.path.join(config_dir, 'config.json')

        if not os.path.exists(config_file):
            print(f"  : {config_file}")
            print(f"   Token,")
            print(f"   Token: {token}")
            print(f"   Private token: {privatetoken}")
            return

        # 
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # token
        config['token'] = token
        config['privatetoken'] = privatetoken

        # 
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)

        print(f" Token: {config_file}")

    except Exception as e:
        print(f"  token: {e}")
        print(f"   token:")
        print(f"   Token: {token}")
        print(f"   Private token: {privatetoken}")


def export_cookies_interactive(domain: str = None, output_file: str = None, ask_browser: bool = True, auto_get_token: bool = False):
    """
     cookies()

    Args:
        domain: Moodle ( moodle.example.com, None )
        output_file: ( Cookies.txt)
        ask_browser: ( True)
        auto_get_token:  API token ( False)

    Returns:
        bool:  cookies
    """
    # ,
    if domain is None:
        print(" Moodle ")
        print(": moodle.university.edu  elearning.school.com")
        domain = input("Moodle : ").strip()
        if not domain:
            print(" ")
            return False
    if output_file is None:
        output_file = os.path.join(os.getcwd(), 'Cookies.txt')

    print("=" * 80)
    print("Moodle Browser Cookies ")
    print("=" * 80)
    print(f": {domain}")
    print(f": {output_file}")
    print("=" * 80)

    if ask_browser:
        # 
        print("\n:")
        print("1. Chrome")
        print("2. Edge")
        print("3. Firefox")
        print("4. Safari")
        print("5. Chromium (Brave, Vivaldi, Arc, Opera )")
        print("6. Firefox (Zen, Waterfox, LibreWolf )")
        print("7. ")
        print("8. ")

        while True:
            try:
                choice = input("\n (1-8): ").strip()
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
                    # Chromium  - 
                    print("\n Chromium :")
                    print("1. Chrome")
                    print("2. Brave")
                    print("3. Vivaldi")
                    print("4. Opera")
                    print("5. Chromium")
                    print("6. Arc()")

                    chromium_choice = input("\n (1-6): ").strip()
                    chromium_map = {
                        '1': 'chrome',
                        '2': 'brave',      #   brave() 
                        '3': 'vivaldi',    #   vivaldi() 
                        '4': 'opera',      #   opera() 
                        '5': 'chromium',   #   chromium() 
                        '6': 'arc',        #  
                    }
                    selected = chromium_map.get(chromium_choice)

                    if selected:
                        browsers_to_try = [selected]
                        break
                    else:
                        print(" , 1-6")
                        continue

                elif choice == '6':
                    # Firefox  - 
                    print("\n Firefox :")
                    print("1. Firefox")
                    print("2. LibreWolf")
                    print("3. Zen Browser()")
                    print("4. Waterfox()")

                    firefox_choice = input("\n (1-4): ").strip()
                    firefox_map = {
                        '1': 'firefox',
                        '2': 'librewolf',  #   librewolf() 
                        '3': 'zen',        #  
                        '4': 'waterfox',   #  
                    }
                    selected = firefox_map.get(firefox_choice)

                    if selected:
                        browsers_to_try = [selected]
                        break
                    else:
                        print(" , 1-4")
                        continue
                elif choice == '7':
                    #  - 
                    browsers_to_try = [
                        'chrome', 'brave', 'vivaldi', 'opera', 'chromium', 'edge',
                        'firefox', 'librewolf',
                        'safari'
                    ]
                    print("\n:Chrome, Brave, Vivaldi, Opera, Chromium, Edge, Firefox, LibreWolf, Safari")
                    break
                elif choice == '8':
                    # 
                    print("\n :")
                    print("   o :chrome, firefox, brave, vivaldi, opera, edge, chromium, librewolf, safari")
                    print("   o :zen, waterfox, arc")
                    custom_browser = input("\n: ").strip().lower()
                    if custom_browser:
                        browsers_to_try = [custom_browser]
                        break
                    else:
                        print(" ")
                else:
                    print(" , 1-8")
            except (KeyboardInterrupt, EOFError):
                print("\n\n")
                return False
    else:
        # ()
        browsers_to_try = ['chrome', 'edge', 'firefox', 'safari']

    # 
    success = False
    selected_browser = None
    for browser in browsers_to_try:
        print(f"\n {browser} ...")
        if export_cookies_from_browser(domain, output_file, browser):
            success = True
            selected_browser = browser
            break

    if not success:
        print("\n" + "=" * 80)
        print(" ")
        print("\n cookies:")
        print("1.  'Get cookies.txt LOCALLY'")
        print(f"2.  https://{domain}")
        print("3. , cookies")
        print(f"4. : {output_file}")
        print("=" * 80)
        return False

    #   SSO  cookies
    #  SSO cookies ,
    print("\n  SSO  cookies...")
    print("   ( Microsoft/Google  SSO cookies ,)")

    try:
        #  SSO 
        # :,
        import importlib.util
        import sys

        # ( moodle-dl )
        try:
            from moodle_dl.auto_sso_login import auto_login_with_sso_sync
        except ImportError:
            # :
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

        #  SSO  cookies
        refresh_success = auto_login_with_sso_sync(
            moodle_domain=domain,
            cookies_path=output_file,
            preferred_browser=selected_browser,
            headless=True  # 
        )

        if not refresh_success:
            #  SSO  -  SSO cookies 
            print("\n" + "=" * 80)
            print("   SSO  - SSO cookies ")
            print("=" * 80)
            print("\n :")
            print(f"   {selected_browser} {domain}  SSO ")
            print(f"   ,SSO cookies ")
            print(f"   ,")
            print("\n (SSO cookies )")
            print("   ,")
            print("=" * 80)
            return False

    except Exception as e:
        print(f"\n   SSO : {e}")
        print("    cookies...")
        refresh_success = False

    #  cookies
    cookies_valid = test_cookies(domain, output_file)

    if cookies_valid:
        print("\n" + "=" * 80)
        print(" Cookies !")
        print("=" * 80)

        # API token - ,
        if auto_get_token or ask_browser:
            # API token,
            print("\nMoodle API token...")
            print("(API tokenWeb Service API)")
            token, privatetoken = extract_api_token_with_cookies(domain, output_file)
            if token and privatetoken:
                print(f" API token!")
            else:
                print(f"  API token,: moodle-dl --new-token --sso")

    return cookies_valid


def main():
    """Main entry point for export_browser_cookies"""
    # Parse command line arguments
    domain = None
    output_file = os.path.join(os.getcwd(), 'Cookies.txt')

    if len(sys.argv) > 1:
        domain = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]

    # Run the interactive export process
    success = export_cookies_interactive(domain, output_file)

    if not success:
        sys.exit(1)

    print("\nNext step: moodle-dl ")
    print(f"moodle-dl --path {os.path.dirname(output_file) or '.'}")

if __name__ == '__main__':
    main()
