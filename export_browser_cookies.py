#!/usr/bin/env python3
"""
 Moodle cookies

:pip install browser-cookie3
"""

import os
import sys
import glob
import platform

try:
    import browser_cookie3
except ImportError:
    print(": browser-cookie3")
    print(":pip install browser-cookie3")
    sys.exit(1)


def normalize_cookie_for_playwright(cookie: dict) -> dict:
    """
    标准化 cookie 格式为 Playwright 要求的格式（防御性编程）
    
    处理不同来源的 cookies 字段差异：
    - 确保 secure 和 httpOnly 是布尔值（不是整数 0/1）
    - 统一字段命名：httpOnly（不是 httponly），sameSite（不是 samesite）
    - 确保 expires 是有效值（-1 或正整数秒级时间戳）
    - 移除非 Playwright 字段（如 cookie_id）
    
    Args:
        cookie: 原始 cookie 字典
        
    Returns:
        标准化后的 cookie 字典（Playwright 格式）
    """
    cleaned = cookie.copy()
    
    # 移除数据库专用字段
    cleaned.pop('cookie_id', None)
    
    # 处理 expires 字段（Playwright 严格要求：-1 或正整数秒级时间戳）
    expires_value = cleaned.get('expires')
    if expires_value is None or expires_value == '':
        # None 或空字符串 → session cookie
        cleaned['expires'] = -1
    elif isinstance(expires_value, (int, float)):
        if expires_value <= 0 and expires_value != -1:
            # 负数（除了-1）→ session cookie
            cleaned['expires'] = -1
        elif expires_value > 10000000000:
            # 毫秒级时间戳 → 转换为秒级
            cleaned['expires'] = int(expires_value / 1000)
        else:
            # 正常的秒级时间戳
            cleaned['expires'] = int(expires_value)
    else:
        # 其他类型 → session cookie
        cleaned['expires'] = -1
    
    # 统一 secure 字段为布尔值
    if 'secure' in cleaned:
        cleaned['secure'] = bool(cleaned['secure'])
    
    # 统一 httpOnly 字段（支持 httponly 和 httpOnly）
    if 'httponly' in cleaned or 'httpOnly' in cleaned:
        http_only_value = cleaned.pop('httponly', cleaned.get('httpOnly', False))
        cleaned['httpOnly'] = bool(http_only_value)
    
    # 统一 sameSite 字段（支持 samesite 和 sameSite）
    if 'samesite' in cleaned or 'sameSite' in cleaned:
        same_site_value = cleaned.pop('samesite', cleaned.get('sameSite', 'Lax'))
        cleaned['sameSite'] = same_site_value or 'Lax'
    
    # 确保必需字段存在且类型正确
    cleaned.setdefault('path', '/')
    cleaned.setdefault('secure', False)
    cleaned.setdefault('httpOnly', False)
    cleaned.setdefault('sameSite', 'Lax')
    
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
            # expires
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
        from playwright.async_api import async_playwright
        import asyncio
        import http.cookiejar
        import re
        import base64

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

                #  :Moodlesession
                # MoodleSession cookie
                print(f"  -> Moodlesession...")
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
     Playwright + cookies  Moodle API token
    
    **v2:  cookies ,**
    
    Args:
        domain: Moodle 
        cookies: Playwright  cookies 
        
    Returns:
        tuple: (token, privatetoken) , (None, None)
    """
    print("\n Playwright  API token...")
    print(f"  ->  {len(cookies)}  cookies ...")
    
    try:
        from playwright.async_api import async_playwright
        import asyncio
        import re
        import base64
        
        #  token  URL
        moodle_url = f'https://{domain}' if not domain.startswith('http') else domain
        token_url = f"{moodle_url}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&urlscheme=moodledl"
        
        # 防御性编程：标准化所有 cookies 为 Playwright 格式
        cleaned_cookies = [normalize_cookie_for_playwright(c) for c in cookies]
        
        #  Playwright 
        async def get_token():
            captured_urls = []
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                await context.add_cookies(cleaned_cookies)
                
                page = await context.new_page()
                
                #  : Moodle  session
                print(f"  ->  Moodle  session...")
                try:
                    await page.goto(f"{moodle_url}/my/", wait_until='domcontentloaded', timeout=15000)
                    await page.wait_for_timeout(1000)  #  cookies 
                    print(f"  -> Session ")
                except Exception as e:
                    print(f"  -> :  session  ({str(e)[:50]}...)")
                
                # 
                def handle_console(msg):
                    text = msg.text
                    if 'moodledl://' in text or 'moodlemobile://' in text:
                        captured_urls.append(text)
                        print(f"  -> : {text[:80]}...")
                
                page.on('console', handle_console)
                
                # 
                def handle_request(request):
                    url = request.url
                    if 'moodledl://' in url or 'moodlemobile://' in url:
                        captured_urls.append(url)
                        print(f"  -> : {url[:80]}...")
                
                page.on('request', handle_request)
                
                print(f"  ->  token ...")
                try:
                    await page.goto(token_url, wait_until='domcontentloaded', timeout=30000)
                except Exception as e:
                    if 'net::ERR_ABORTED' in str(e) or 'ERR_INVALID_URL' in str(e):
                        print(f"  -> (): ")
                    else:
                        print(f"  -> : {e}")
                
                await page.wait_for_timeout(2000)  # 
                
                for context in browser.contexts:
                    await context.close()
                await browser.close()
                
                return captured_urls
        
        captured = asyncio.run(get_token())
        
        if not captured:
            print(f"  token ")
            return None, None
        
        print(f"  ->  {len(captured)}  URL")
        
        #  token
        for url in captured:
            if 'token=' in url:
                match = re.search(r'token=([^&\s]+)', url)
                if match:
                    token_encoded = match.group(1)
                    
                    try:
                        #  base64
                        decoded = base64.b64decode(token_encoded).decode('utf-8')
                        parts = decoded.split(':::')  # 修复：应该是三个冒号，不是两个
                        
                        if len(parts) >= 2:
                            mobile_app_token = parts[0]  # Mobile app token()
                            web_service_token = parts[1]  #  Web Service API( API token)
                            
                            print(f"  API token")
                            print(f"   Web Service Token: {web_service_token[:20]}...")
                            print(f"   Mobile App Token: {mobile_app_token[:20]}...")
                            
                            return web_service_token, mobile_app_token
                    except Exception as e:
                        print(f"  token : {e}")
                        continue
        
        print(f"  token")
        return None, None
        
    except Exception as e:
        print(f"  API token : {e}")
        import traceback
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
