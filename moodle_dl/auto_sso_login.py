#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
import glob
import logging
import os
import re
import urllib.parse
from typing import Tuple, List, Dict, Optional

from moodle_dl.utils import Log
from moodle_dl.cli.localization import tr as _


def _url_hostname_matches(url: str, expected_domain: str) -> bool:
    """
    Return True only when the URL hostname equals the expected domain.

    Microsoft login URLs can contain redirect_uri=https://keats... in the query
    string while the browser is still on login.microsoftonline.com, so a plain
    substring check is not reliable here.
    """
    if not url or not expected_domain:
        return False

    expected_parts = urllib.parse.urlparse(expected_domain)
    expected_host = expected_parts.hostname
    if expected_host is None:
        expected_host = (
            urllib.parse.urlparse(f'//{expected_domain}').hostname
            or expected_domain.split('/', 1)[0]
        )

    expected_host = expected_host.lower().strip('.')
    current_host = (urllib.parse.urlparse(url).hostname or '').lower().strip('.')

    return bool(current_host) and current_host == expected_host


def _is_sso_provider_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url or '').hostname or '').lower()
    return any(
        provider in host
        for provider in ('microsoft', 'google', 'login.live.com')
    )


def _is_account_selection_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url or '').hostname or '').lower()
    return host in (
        'login.microsoftonline.com',
        'login.live.com',
        'accounts.google.com',
    )


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
    - v3: 支持多账号选择（检测到多个 Microsoft 账号时提示用户选择）

    @param browser_name: 浏览器名称（firefox, chrome 等）
    @param moodle_domain: Moodle 域名（用于日志）
    @param cookies_path: [已废弃] cookies 文件路径（不再使用）
    @return: 所有 cookies 的列表
    """
    try:
        # v2: 直接从浏览器读取 cookies（永不读取文件）
        logging.info(_('💡 正在从浏览器直接读取所有 cookies...', '💡 Reading all cookies directly from the browser...'))
        all_cookies = _read_all_cookies_from_browser(browser_name)

        if all_cookies:
            logging.info(_('✓ 从浏览器成功读取 {count} 个 cookies', '✓ Successfully read {count} cookies from the browser', count=len(all_cookies)))
        else:
            logging.warning(_('⚠️  浏览器中没有找到 cookies', '⚠️  No cookies found in the browser'))
            logging.info(_('   请确保浏览器已登录 Moodle，且 SSO cookies 有效', '   Make sure the browser is logged in to Moodle and SSO cookies are valid'))
            return []

        # v3: 检测多账号情况
        accounts = _detect_multiple_accounts(all_cookies, browser_name)

        if len(accounts) > 1:
            # 检测到多个账号，让用户选择
            selected_account = _prompt_user_for_account_selection(accounts)
            # 过滤 cookies，只保留选中账号的
            all_cookies = _filter_cookies_by_account(all_cookies, selected_account)
            logging.info(_('✓ 已过滤为 {count} 个 cookies（选定账号）', '✓ Filtered to {count} cookies for the selected account', count=len(all_cookies)))

        return all_cookies

    except Exception as e:
        logging.error(_('❌ 提取 cookies 时出错: {error}', '❌ Error while extracting cookies: {error}', error=e))
        return []


def _find_browser_cookie_path(browser_name: str) -> Optional[str]:
    """
    查找需要自定义路径的浏览器的 cookie 文件路径
    
    @param browser_name: 浏览器名称（zen, waterfox, arc 等）
    @return: cookie 文件路径，如果找不到则返回 None
    """
    import platform
    import glob
    
    system = platform.system()
    
    # 需要自定义路径的浏览器配置
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
            'Linux': None,  # Arc 不支持 Linux
            'Windows': os.path.join(os.getenv('LOCALAPPDATA', ''), 'Arc', 'User Data', '*', 'Cookies')
        },
    }
    
    if browser_name.lower() not in browser_paths:
        return None
    
    paths = browser_paths[browser_name.lower()].get(system)
    
    if not paths:
        return None
    
    # 处理列表（Flatpak 等可能有多个路径）
    if isinstance(paths, list):
        for path_pattern in paths:
            expanded = os.path.expanduser(path_pattern)
            matches = glob.glob(expanded)
            if matches:
                # 返回最新的 profile
                return max(matches, key=os.path.getmtime)
    else:
        # 单个路径
        expanded = os.path.expanduser(paths)
        matches = glob.glob(expanded)
        if matches:
            # 返回最新的 profile
            return max(matches, key=os.path.getmtime)
    
    return None


def _read_all_cookies_from_browser(browser_name: str) -> List[Dict]:
    """
    从浏览器数据库中读取所有 cookies（不过滤）

    @param browser_name: 浏览器名称
    @return: 所有 cookies 列表
    """
    try:
        import browser_cookie3

        # 获取浏览器的 cookie jar
        # 对于需要自定义路径的浏览器（Zen, Waterfox, Arc），使用 Firefox/Chrome 方法但指定路径
        browser_methods = {
            'chrome': (browser_cookie3.chrome, False),
            'firefox': (browser_cookie3.firefox, False),
            'edge': (browser_cookie3.edge, False),
            'brave': (browser_cookie3.brave, False),
            'safari': (browser_cookie3.safari, False),
            # 需要自定义路径的浏览器（使用 Firefox/Chrome 方法但指定 cookie 文件路径）
            'zen': (browser_cookie3.firefox, True),  # Zen 是 Firefox 内核
            'waterfox': (browser_cookie3.firefox, True),  # Waterfox 是 Firefox 分支
            'arc': (browser_cookie3.chrome, True),  # Arc 是 Chrome 内核
        }

        if browser_name.lower() not in browser_methods:
            logging.warning(_('⚠️  不支持的浏览器: {browser}', '⚠️  Unsupported browser: {browser}', browser=browser_name))
            return []

        method, needs_custom_path = browser_methods[browser_name.lower()]
        
        if needs_custom_path:
            # 需要自定义路径的浏览器
            cookie_path = _find_browser_cookie_path(browser_name)
            if not cookie_path:
                logging.warning(_('⚠️  无法找到 {browser} 浏览器的 cookie 文件', '⚠️  Could not find the cookie file for {browser}', browser=browser_name))
                logging.info(_('   请确保浏览器已安装并已登录', '   Make sure the browser is installed and logged in'))
                return []
            logging.debug(_('   使用 cookie 文件: {path}', '   Using cookie file: {path}', path=cookie_path))
            cj = method(cookie_file=cookie_path)
        else:
            # 标准浏览器，使用默认路径
            cj = method()

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

        logging.info(_('✓ 从{browser}读取到 {count} 个 cookies（所有域名）', '✓ Read {count} cookies from {browser} (all domains)', browser=browser_name, count=len(all_cookies)))
        return all_cookies

    except Exception as e:
        logging.error(_('❌ 从浏览器读取cookies失败: {error}', '❌ Failed to read cookies from browser: {error}', error=e))
        return []


def _detect_multiple_accounts(cookies: List[Dict], browser_name: str) -> List[Dict]:
    """
    检测 cookies 中是否存在多个 Microsoft 账号

    **设计说明和限制：**
    根据 Microsoft 官方文档和研究发现：
    - ESTSAUTHPERSISTENT 通常在同一浏览器配置文件中只有一个（代表当前会话）
    - Microsoft 官方推荐使用分离的浏览器配置文件来管理多个账号
    - ESTSSSOTILES=1 会在需要时触发账号选择器 UI（Pick an Account）

    此功能能检测的场景：
    1. 当 Microsoft 账号选择器被触发（ESTSSSOTILES=1）
    2. 当多个 Microsoft 服务域有独立的会话 cookies
       （如 .microsoftonline.com 和 .live.com 各自有 ESTSAUTHPERSISTENT）

    Reference: https://learn.microsoft.com/en-us/entra/identity/authentication/concept-authentication-web-browser-cookies
             - ESTSAUTHPERSISTENT: "Contains user's session information to facilitate SSO. Persistent."
             - ESTSSSOTILES: "When present and not expired, with value 'ESTSSSOTILES=1',
                            it interrupts SSO... and presents tiles for user account selection."
             - ESTSUSERLIST: "Tracks Browser SSO user's list."

    Args:
        cookies: 所有 cookies 列表
        browser_name: 浏览器名称（用于日志）

    Returns:
        账号列表，每个账号包含标识信息和相关 cookies
    """
    from collections import defaultdict
    import base64
    import json

    # 第一步：检查 ESTSSSOTILES cookie（多账号指示器）
    sso_tiles_value = None
    for cookie in cookies:
        if cookie.get('name') == 'ESTSSSOTILES':
            sso_tiles_value = cookie.get('value', '')
            break

    # 第二步：收集所有 ESTSAUTHPERSISTENT cookies（会话标识符）
    # 每个账号会有不同的 ESTSAUTHPERSISTENT cookie
    ests_auth_persistent_cookies = []
    for cookie in cookies:
        name = cookie.get('name', '')
        domain = cookie.get('domain', '')

        # 收集持久会话 cookies（Microsoft 和 Live.com 域名）
        is_ms_domain = any(keyword in domain.lower() for keyword in ['microsoft', 'live.com', 'microsoftonline.com'])
        if name == 'ESTSAUTHPERSISTENT' and is_ms_domain:
            ests_auth_persistent_cookies.append(cookie)

    # 如果没有找到任何 Microsoft 会话 cookie，返回空列表
    if not ests_auth_persistent_cookies:
        return []

    # 第三步：尝试解析 ESTSUSERLIST 来获取用户信息
    # ESTSUSERLIST 包含浏览器 SSO 用户列表，可能有多个用户
    users_list = _parse_estsuserlist(cookies)

    # 第四步：判断是否需要多账号选择
    # 情况1：有多个 ESTSAUTHPERSISTENT cookies（不同域有不同会话）
    # 情况2：ESTSSSOTILES=1 且 ESTSUSERLIST 有多个用户（账号选择器场景）
    # 情况3：ESTSSSOTILES=1 但没有 ESTSUSERLIST（显示警告，继续使用当前 cookies）
    has_multiple_estsp = len(ests_auth_persistent_cookies) > 1
    has_multiple_users = sso_tiles_value == '1' and len(users_list) > 1
    has_sso_tiles_but_no_userlist = sso_tiles_value == '1' and len(users_list) == 0

    if has_sso_tiles_but_no_userlist:
        # ESTSSSOTILES=1 但没有 ESTSUSERLIST
        # 这表示可能有多个账号，但我们无法获取用户列表
        logging.warning(_('⚠️  检测到 Microsoft 账号选择器（ESTSSSOTILES=1）', '⚠️  Microsoft account picker detected (ESTSSSOTILES=1)'))
        logging.warning(_('⚠️  但无法获取账号列表（ESTSUSERLIST 不存在或解析失败）', '⚠️  Could not get the account list (ESTSUSERLIST missing or failed to parse)'))
        logging.warning(_('💡 如果登录失败，请尝试：', '💡 If login fails, try:'))
        logging.warning(_('   1. 在浏览器中手动选择要使用的账号', '   1. Manually select the account to use in the browser'))
        logging.warning(_('   2. 或者使用单独的浏览器配置文件登录单个账号', '   2. Or use a separate browser profile with only one account logged in'))
        logging.info(_('✓ 继续使用当前 cookies 尝试登录...', '✓ Continuing with the current cookies...'))
        # 不返回，继续使用当前的 cookies

    if not has_multiple_estsp and not has_multiple_users:
        # 只有一个账号，不需要选择
        return []

    # 如果只有一个 ESTSAUTHPERSISTENT 但有多个用户，需要使用 ESTSUSERLIST 创建虚拟账号
    if not has_multiple_estsp and has_multiple_users:
        accounts = {}
        # 为每个用户创建一个账号条目（共享同一个 ESTSAUTHPERSISTENT）
        base_ests_cookie = ests_auth_persistent_cookies[0]
        base_domain = base_ests_cookie.get('domain', '')
        base_value = base_ests_cookie.get('value', '')

        for i, user_info in enumerate(users_list):
            # 为每个用户创建唯一的 ID（使用索引区分）
            session_id = f"{base_value[:28]}_{i:02d}"
            accounts[session_id] = {
                'id': session_id,
                'ests_auth_persistent': base_value,  # 所有用户共享同一个会话 cookie
                'domain': base_domain,
                'user_info': user_info,
                'cookies': []  # 稍后填充
            }

        # 收集所有 Microsoft cookies（所有账号共享）
        all_ms_cookies = []
        for cookie in cookies:
            domain = cookie.get('domain', '')
            is_ms_cookie = any(keyword in domain.lower() for keyword in ['microsoft', 'live.com', 'microsoftonline.com'])
            if is_ms_cookie:
                all_ms_cookies.append(cookie)

        # 所有账号共享相同的 Microsoft cookies
        for session_id in accounts:
            accounts[session_id]['cookies'] = all_ms_cookies.copy()

        # 转换为列表
        account_list = []
        for session_id, account_info in accounts.items():
            display_info = account_info['user_info'] or {'email': f'Account {len(account_list) + 1}'}
            account_list.append({
                'id': session_id,
                'ests_auth_persistent': account_info['ests_auth_persistent'],
                'domain': account_info['domain'],
                'user_info': display_info,
                'cookies': account_info['cookies']
            })

        if len(account_list) > 1:
            logging.warning(_('⚠️  检测到 {count} 个 Microsoft 账号（来自 ESTSUSERLIST）', '⚠️  Detected {count} Microsoft accounts (from ESTSUSERLIST)', count=len(account_list)))

        return account_list

    # 第五步：按 ESTSAUTHPERSISTENT cookie 值分组所有 Microsoft 相关 cookies
    # （有多个 ESTSAUTHPERSISTENT cookies 的情况）
    accounts = {}
    for ests_cookie in ests_auth_persistent_cookies:
        session_id = ests_cookie.get('value', '')[:32]  # 使用前32个字符作为 ID
        accounts[session_id] = {
            'id': session_id,
            'ests_auth_persistent': ests_cookie.get('value', ''),
            'domain': ests_cookie.get('domain', ''),
            'user_info': None,
            'cookies': []
        }

    # 第六步：遍历所有 cookies，分配到对应账号
    for cookie in cookies:
        domain = cookie.get('domain', '')
        name = cookie.get('name', '')

        # 检查是否是 Microsoft 相关的 cookie
        is_ms_cookie = any(keyword in domain.lower() for keyword in ['microsoft', 'live.com', 'microsoftonline.com'])

        if not is_ms_cookie:
            continue

        # 将 cookie 添加到匹配域的账号
        matched = False
        for session_id, account_info in accounts.items():
            if domain == account_info['domain'] or domain.endswith(account_info['domain'].lstrip('.')):
                account_info['cookies'].append(cookie)
                matched = True
                break

        # 如果没有匹配，添加到所有账号（共享 cookies）
        if not matched:
            for session_id in accounts:
                accounts[session_id]['cookies'].append(cookie)

    # 第七步：关联用户信息（如果解析成功）
    if users_list:
        for i, user_info in enumerate(users_list):
            if i < len(accounts):
                session_id = list(accounts.keys())[i]
                accounts[session_id]['user_info'] = user_info

    # 转换为列表
    account_list = []
    for session_id, account_info in accounts.items():
        if account_info['ests_auth_persistent']:
            display_info = account_info['user_info'] or {'email': f'Account {len(account_list) + 1}'}
            account_list.append({
                'id': session_id,
                'ests_auth_persistent': account_info['ests_auth_persistent'],
                'domain': account_info['domain'],
                'user_info': display_info,
                'cookies': account_info['cookies']
            })

    if len(account_list) > 1:
        logging.warning(_('⚠️  检测到 {count} 个 Microsoft 账号在 {browser} 浏览器中', '⚠️  Detected {count} Microsoft accounts in {browser}', count=len(account_list), browser=browser_name))
        if sso_tiles_value == '1':
            logging.info(_('   检测到 ESTSSSOTILES=1，需要账号选择', '   ESTSSSOTILES=1 detected; account selection may be needed'))
        for i, account in enumerate(account_list, 1):
            user_display = account.get('user_info', {}).get('email', 'Unknown')
            logging.info(_('   账号 {index}: {user}', '   Account {index}: {user}', index=i, user=user_display))

    return account_list


def _parse_estsuserlist(cookies: List[Dict]) -> List[Dict]:
    """
    解析 ESTSUSERLIST cookie 来获取用户信息

    ESTSUSERLIST 包含浏览器 SSO 用户列表，格式通常是 base64 编码的 JSON

    Args:
        cookies: 所有 cookies 列表

    Returns:
        用户信息列表
    """
    import base64
    import json

    for cookie in cookies:
        if cookie.get('name') == 'ESTSUSERLIST':
            try:
                value = cookie.get('value', '')
                # 尝试 base64 解码
                decoded = base64.b64decode(value).decode('utf-8', errors='ignore')
                # 尝试解析 JSON
                data = json.loads(decoded)

                # 提取用户信息
                users = []
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            users.append({
                                'email': item.get('login_name') or item.get('email') or item.get('upn') or 'Unknown',
                                'display_name': item.get('display_name') or item.get('name') or ''
                            })
                elif isinstance(data, dict):
                    # 可能是包含 users 字段的对象
                    users_list = data.get('users') or data.get('Users') or []
                    for item in users_list:
                        users.append({
                            'email': item.get('login_name') or item.get('email') or item.get('upn') or 'Unknown',
                            'display_name': item.get('display_name') or item.get('name') or ''
                        })

                return users if users else []
            except Exception:
                # 解析失败，返回空列表
                pass

    return []


def _filter_cookies_by_account(all_cookies: List[Dict], selected_account: Dict) -> List[Dict]:
    """
    根据选定的账号过滤 cookies

    Args:
        all_cookies: 所有 cookies 列表
        selected_account: 用户选择的账号（包含其相关 cookies）

    Returns:
        过滤后的 cookies 列表
    """
    selected_domain = selected_account.get('domain', '')
    selected_ests_auth = selected_account.get('ests_auth_persistent', '')

    # 保留所有非 Microsoft 域名的 cookies
    # 以及选中账号的 Microsoft cookies
    filtered_cookies = []
    account_ms_domains = {selected_domain}

    # 收集选中账号的 Microsoft 域名（包括子域名）
    for cookie in selected_account.get('cookies', []):
        domain = cookie.get('domain', '')
        if 'microsoft' in domain.lower() or 'live.com' in domain or 'microsoftonline.com' in domain:
            account_ms_domains.add(domain)

    for cookie in all_cookies:
        domain = cookie.get('domain', '')
        name = cookie.get('name', '')
        is_ms_domain = any(keyword in domain.lower() for keyword in ['microsoft', 'live.com', 'microsoftonline.com'])

        if not is_ms_domain:
            # 非 Microsoft cookies 全部保留
            filtered_cookies.append(cookie)
        elif name == 'ESTSAUTHPERSISTENT':
            # 只保留选中账号的 ESTSAUTHPERSISTENT cookie
            if cookie.get('value', '') == selected_ests_auth:
                filtered_cookies.append(cookie)
        else:
            # 其他 Microsoft cookies 根据域名匹配
            if any(domain == acc_domain or domain.endswith(acc_domain.lstrip('.')) for acc_domain in account_ms_domains):
                filtered_cookies.append(cookie)

    return filtered_cookies


def _prompt_user_for_account_selection(accounts: List[Dict]) -> Dict:
    """
    提示用户选择要使用的账号

    Args:
        accounts: 检测到的账号列表

    Returns:
        用户选择的账号
    """
    logging.info('')
    logging.info('╔════════════════════════════════════════════════════════════╗')
    logging.info(_('║  检测到多个 Microsoft 账号登录                             ║', '║  Multiple Microsoft accounts were detected                 ║'))
    logging.info(_('║  请选择要用于 Moodle 登录的账号                             ║', '║  Select the account to use for Moodle login                 ║'))
    logging.info('╚════════════════════════════════════════════════════════════╝')
    logging.info('')

    for i, account in enumerate(accounts, 1):
        user_info = account.get('user_info', {})
        email = user_info.get('email', 'Unknown')
        display_name = user_info.get('display_name', '')

        # 使用 ESTSAUTHPERSISTENT 的前 24 个字符作为标识
        session_id = account.get('ests_auth_persistent', '')[:24]
        domain = account.get('domain', '')

        if display_name:
            logging.info(f'  [{i}] {display_name} ({email})')
        else:
            logging.info(f'  [{i}] {email}')
        logging.info(_('      域名: {domain}', '      Domain: {domain}', domain=domain))
        logging.info(_('      会话: {session_id}...', '      Session: {session_id}...', session_id=session_id))

    logging.info('')
    while True:
        try:
            choice = input(_('请输入账号编号 (1-{count}): ', 'Enter account number (1-{count}): ', count=len(accounts)))
            choice_num = int(choice.strip())
            if 1 <= choice_num <= len(accounts):
                selected = accounts[choice_num - 1]
                user_info = selected.get('user_info', {})
                email = user_info.get('email', _('账号 {index}', 'Account {index}', index=choice_num))
                logging.info(_('✓ 已选择: {email}', '✓ Selected: {email}', email=email))
                return selected
            else:
                logging.warning(_('⚠️  请输入 1 到 {count} 之间的数字', '⚠️  Enter a number between 1 and {count}', count=len(accounts)))
        except ValueError:
            logging.warning(_('⚠️  请输入有效的数字', '⚠️  Enter a valid number'))
        except (EOFError, KeyboardInterrupt):
            # 在非交互环境中，默认选择第一个账号
            logging.info(_('💡 非交互环境，自动选择第一个账号', '💡 Non-interactive environment; selecting the first account automatically'))
            return accounts[0]


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
            logging.warning(_('⚠️  不支持的浏览器: {browser}', '⚠️  Unsupported browser: {browser}', browser=browser_name))
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

        logging.info(_('✓ 从{browser}读取到 {count} 个 SSO cookies', '✓ Read {count} SSO cookies from {browser}', browser=browser_name, count=len(sso_cookies)))
        return sso_cookies

    except Exception as e:
        logging.error(_('❌ 从浏览器读取cookies失败: {error}', '❌ Failed to read cookies from browser: {error}', error=e))
        return []


async def _launch_playwright_browser(playwright_obj, preferred_browser: str, headless: bool):
    """
    原子函数: 启动 Playwright 浏览器
    
    职责: 单一 - 仅负责浏览器启动
    
    Args:
        playwright_obj: Playwright 实例
        preferred_browser: 首选浏览器 ('firefox' 或其他)
        headless: 是否无头模式
        
    Returns:
        Browser 实例
    """
    browser_type = playwright_obj.firefox if preferred_browser == 'firefox' else playwright_obj.chromium

    if headless:
        logging.info(_('🌐 启动无头浏览器...', '🌐 Launching headless browser...'))
        # 使用新的 headless 模式（真正的 Chrome/Firefox），不需要 chromium_headless_shell
        if preferred_browser == 'firefox':
            return await browser_type.launch(headless=True)
        else:
            return await playwright_obj.chromium.launch(
                headless=True,
                channel='chromium'  # 使用新的 headless 模式
            )
    else:
        logging.info(_('🌐 启动有头浏览器（可见窗口，方便调试）...', '🌐 Launching headful browser (visible window, useful for debugging)...'))
        return await browser_type.launch(
            headless=False,
            slow_mo=500  # 减慢操作，方便观察
        )


async def _navigate_to_moodle_and_wait(page, moodle_domain: str, moodle_url: str, timeout: int, headless: bool = False) -> tuple:
    """
    原子函数: 导航到 Moodle 并等待重定向完成

    职责: 单一 - 仅负责导航和等待

    Args:
        page: Playwright 页面对象
        moodle_domain: Moodle 域名
        moodle_url: 完整的 Moodle URL
        timeout: 超时时间（毫秒）
        headless: 是否为无头模式（影响等待时间）

    Returns:
        元组 (visited_sso, current_url, page_content)
    """
    logging.info(_('🔗 正在访问 Moodle: {url}', '🔗 Visiting Moodle: {url}', url=moodle_url))
    logging.info(_('   等待 SSO 自动登录完成...', '   Waiting for SSO auto-login to complete...'))
    logging.info(_('   💡 原理：只要 SSO cookies 有效，将完全自动化完成登录', '   💡 If SSO cookies are valid, login can complete automatically.'))

    # 有头模式下使用更长的等待时间（5 分钟），给用户足够时间选择账号
    max_wait = 300 if not headless else 15

    if not headless:
        logging.info('')
        logging.info(_('🌐 有头模式已启用', '🌐 Headful mode is enabled'))
        logging.info(_('   - 最多等待 {seconds} 秒（5分钟）', '   - Waiting up to {seconds} seconds (5 minutes)', seconds=max_wait))
        logging.info(_('   - 如果看到账号选择页面，请手动选择要使用的账号', '   - If you see an account picker, manually select the account to use'))
        logging.info(_('   - 选择完成后，程序会自动继续', '   - After selection, the program will continue automatically'))
        logging.info('')

    try:
        # 使用 domcontentloaded 而不是 load - 只等DOM加载，不等所有资源
        # 这样可以避免被第三方tracking scripts阻塞（Google Analytics等）
        # 对于SSO重定向来说，DOM加载完成就足够了
        await page.goto(moodle_url, wait_until='domcontentloaded', timeout=timeout)

        # 等待 SSO 重定向完成（原子函数）
        visited_sso = await _wait_for_sso_redirect(page, moodle_domain, max_wait, headless)

        # 获取最终状态
        current_url = page.url
        page_content = await page.content()

        logging.info(_('📍 最终URL: {url}', '📍 Final URL: {url}', url=current_url))
        logging.debug(_('🔍 是否经历过 SSO 重定向: {visited}', '🔍 Visited SSO redirect: {visited}', visited=visited_sso))

        return visited_sso, current_url, page_content

    except Exception as e:
        logging.error(_('❌ 导航出错: {error}', '❌ Navigation error: {error}', error=e))
        return False, page.url, ''


async def _check_final_login_status(page_content: str, current_url: str, visited_sso: bool, headless: bool = False) -> int:
    """
    原子函数: 检查最终的登录状态

    职责: 单一 - 仅检查登录是否成功

    Args:
        page_content: 页面 HTML 内容
        current_url: 当前 URL
        visited_sso: 是否经历过 SSO 重定向
        headless: 是否为无头模式

    Returns:
        登录状态码:
        1 - 登录成功（找到 logout 链接或访问过 SSO）
        0 - 登录状态未确定（或有头模式下在账号选择页面）
        -1 - 在登录页面或有错误
    """
    # 在有头模式下，如果检测到账号选择页面，返回 0（未确定）而不是 -1（失败）
    # 这样主循环会继续等待，给用户时间选择账号
    if not headless and _is_account_selection_url(current_url):
        logging.info(_('⏸️  仍在账号选择页面，继续等待...', '⏸️  Still on account selection page; continuing to wait...'))
        return 0  # 返回未确定，让循环继续

    # 检查是否在登录页面
    if (
        _is_sso_provider_url(current_url)
        or '/login' in current_url.lower()
        or 'accounts.microsoft' in current_url
        or 'accounts.google' in current_url
    ):
        logging.warning(_('⚠️  仍然在登录/认证页面，登录可能失败', '⚠️  Still on login/authentication page; login may have failed'))
        return -1

    # 检查错误标志。不要把裸数字 401/403 当成失败；Moodle dashboard
    # 的脚本、课程 ID 或资源 ID 中可能自然出现这些数字。
    error_indicator = _find_login_error_indicator(page_content)
    if error_indicator:
        logging.warning(_('⚠️  页面中检测到错误指示: {indicator}', '⚠️  Detected error indicator on page: {indicator}', indicator=error_indicator))
        if _is_headless_moodle_auth_replay_failure(current_url, page_content, headless):
            logging.info(_('💡 当前不是浏览器类型问题，而是无头模式下未能完成现有 SSO 状态恢复', '💡 This is not a browser type issue; headless mode could not restore the existing SSO state'))
            logging.info(_('   建议改用默认有头模式重新初始化：moodle-dl --init --sso', '   Recommended: rerun initialization in default headful mode: moodle-dl --init --sso'))
            logging.info(_('   如果设置了 MOODLE_DL_HEADLESS=1 或 MOODLE_DL_HEADFUL=0，请移除该设置', '   If MOODLE_DL_HEADLESS=1 or MOODLE_DL_HEADFUL=0 is set, remove that setting'))
        return -1

    # 检查成功标志
    if 'login/logout.php' in page_content or visited_sso:
        logging.debug(_('✅ 检测到登录成功标志', '✅ Login success indicator detected'))
        return 1

    # 状态未确定
    logging.debug(_('⚠️  无法确定登录状态', '⚠️  Unable to determine login status'))
    return 0


def _find_login_error_indicator(page_content: str) -> Optional[str]:
    """Return the matched login/auth error marker, if the page clearly has one."""
    textual_indicators = (
        'Sign in to your account',
        'Invalid login',
        'Authentication failed',
        'Your session has expired',
        'You are not logged in',
    )
    for indicator in textual_indicators:
        if indicator in page_content:
            return indicator

    auth_error_patterns = (
        (r'\b401\s*[:\-]?\s*Unauthorized\b', '401 Unauthorized'),
        (r'\bUnauthorized\s*[:\-]?\s*\(?401\)?\b', 'Unauthorized 401'),
        (r'\b403\s*[:\-]?\s*(Forbidden|Unauthorized|Access denied)\b', '403 Forbidden'),
        (r'\b(Forbidden|Access denied)\s*[:\-]?\s*\(?403\)?\b', 'Forbidden 403'),
    )
    for pattern, indicator in auth_error_patterns:
        if re.search(pattern, page_content, flags=re.I):
            return indicator

    return None


def _is_headless_moodle_auth_replay_failure(current_url: str, page_content: str, headless: bool) -> bool:
    """
    判断是否属于“无头模式下复用现有 SSO 状态失败”的典型场景。

    特征：
    - 当前是无头模式
    - 最终页面已经回到 Moodle 域
    - 页面里出现 401/403/Unauthorized 等授权失败标志
    - 不是停留在外部 SSO 提供商页面
    """
    if not headless or not current_url:
        return False

    parsed = urllib.parse.urlparse(current_url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    if any(provider in host for provider in ('login.microsoftonline.com', 'login.live.com', 'accounts.google.com')):
        return False

    if not host:
        return False

    auth_error_indicators = ('401', '403', 'Unauthorized')
    if not any(indicator in page_content for indicator in auth_error_indicators):
        return False

    return path.startswith('/my/') or '/my/' in path or '/course/' in path or path == '/'


async def _save_session_cookies(context, auth_manager) -> bool:
    """
    原子函数: 提取并保存 cookies 到数据库
    
    职责: 单一 - 仅负责 cookies 提取和保存
    
    Args:
        context: 浏览器上下文
        auth_manager: AuthSessionManager 实例
        
    Returns:
        True 如果保存成功, False 否则
    """
    if not auth_manager:
        logging.error(_('❌ SSO登录失败: 必须提供 AuthSessionManager', '❌ SSO login failed: AuthSessionManager is required'))
        logging.error(_('   这是v2架构的要求，数据库必须可用', '   This is required by the v2 architecture; the database must be available'))
        return False
    
    try:
        updated_cookies = await context.cookies()
        Log.info(_('📦 获取到 {count} 个 cookies', '📦 Retrieved {count} cookies', count=len(updated_cookies)))

        # 显示关键 cookies（显示完整值来对比）
        for cookie in updated_cookies:
            if cookie['name'] == 'MoodleSession':
                Log.info(f'   ✓ {cookie["name"]}: {cookie["value"]}')

        # 保存 cookies 到数据库
        session_id = auth_manager.save_sso_cookies(updated_cookies)
        if not session_id:
            logging.error(_('❌ 保存 cookies 到数据库失败', '❌ Failed to save cookies to the database'))
            return False

        logging.info(_('💾 Cookies 已保存到数据库: 会话 {session_id}', '💾 Cookies saved to database: session {session_id}', session_id=session_id))
        logging.info(_('   共 {count} 个 cookies', '   {count} cookies in total', count=len(updated_cookies)))
        return True
        
    except Exception as e:
        logging.error(_('❌ 保存 cookies 时出错: {error}', '❌ Error while saving cookies: {error}', error=e))
        return False


async def _wait_for_sso_redirect(page, moodle_domain: str, max_wait: int = 15, headless: bool = False) -> bool:
    """
    原子函数: 等待并检测 SSO 重定向完成

    职责: 单一 - 仅等待和检测重定向

    Args:
        page: Playwright 页面对象
        moodle_domain: Moodle 域名
        max_wait: 最多等待秒数
        headless: 是否为无头模式

    Returns:
        True 如果访问过 SSO 提供商, False 否则
    """
    visited_sso = False
    on_account_selection_page = False

    for i in range(max_wait):
        await page.wait_for_timeout(1000)  # 每次等待 1 秒
        current_url = page.url

        on_moodle_domain = _url_hostname_matches(current_url, moodle_domain)

        # 检测是否在 SSO 提供商页面
        if _is_sso_provider_url(current_url):
            if not visited_sso:
                visited_sso = True
                logging.debug(f'🔐 检测到 SSO 重定向: {current_url}')

            # 检测是否在账号选择页面（ESTSSSOTILES 或 account picker 相关）
            if not on_account_selection_page and _is_account_selection_url(current_url):
                on_account_selection_page = True
                if not headless:
                    logging.info('')
                    logging.info(_('⏸️  检测到账号选择页面', '⏸️  Account selection page detected'))
                    logging.info(_('   当前URL: {url}', '   Current URL: {url}', url=current_url))
                    logging.info(_('   💡 请在浏览器窗口中选择要使用的账号', '   💡 Select the account to use in the browser window'))
                    logging.info(_('   ⏳ 等待你完成选择...', '   ⏳ Waiting for you to finish selection...'))
                    logging.info('')

        # 如果访问过 SSO 并且现在回到 Moodle 域名，说明重定向完成
        if visited_sso and on_moodle_domain:
            logging.debug(f'✓ SSO 重定向完成，已返回 Moodle: {current_url}')
            if not headless and on_account_selection_page:
                logging.info(_('✅ 账号选择完成，继续执行...', '✅ Account selection completed; continuing...'))
            break

        if not visited_sso and on_moodle_domain:
            logging.debug(f'⏳ 等待可能的 SSO 重定向... (第{i+1}/{max_wait}秒)')
        elif not visited_sso:
            logging.debug(f'🔍 当前URL: {current_url}')

    return visited_sso


async def _setup_browser_context(browser, storage_state: dict):
    """
    原子函数: 创建浏览器上下文并加载 storage state

    职责: 单一 - 仅负责上下文创建和 storage state 加载

    Args:
        browser: Browser 实例
        storage_state: 要加载的 storage state

    Returns:
        BrowserContext 实例
    """
    context_options = {
        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/115.0',
        'viewport': {'width': 1920, 'height': 1080},
        'locale': 'en-GB',
        'timezone_id': 'Europe/London',
    }

    # 验证和过滤 cookies，确保每个 cookie 都有必要的字段
    # Playwright 要求每个 cookie 至少要有 domain 和 path
    validated_cookies = []
    skipped_count = 0

    for cookie in storage_state.get('cookies', []):
        # 检查必要的字段
        if not cookie.get('domain'):
            skipped_count += 1
            continue

        # 确保 path 字段存在（Playwright 要求）
        if 'path' not in cookie:
            cookie['path'] = '/'

        # 确保 name 和 value 存在
        if 'name' not in cookie or 'value' not in cookie:
            skipped_count += 1
            continue

        # 确保字段类型正确
        cookie['secure'] = bool(cookie.get('secure', False))
        cookie['httpOnly'] = bool(cookie.get('httpOnly', False))

        validated_cookies.append(cookie)

    if skipped_count > 0:
        logging.info(_('🧹 已跳过 {count} 个无效 cookies（缺少必要字段）', '🧹 Skipped {count} invalid cookies (missing required fields)', count=skipped_count))

    # 更新 storage state，只包含有效的 cookies
    validated_storage_state = {
        'cookies': validated_cookies,
        'origins': storage_state.get('origins', [])
    }

    logging.info(_('✓ 准备加载 {count} 个有效 cookies', '✓ Preparing to load {count} valid cookies', count=len(validated_cookies)))

    try:
        context = await browser.new_context(
            storage_state=validated_storage_state,
            **context_options
        )
        logging.info(_('✓ Storage State 已加载（所有有效 cookies 已注入）', '✓ Storage State loaded (all valid cookies injected)'))
        return context
    except Exception as e:
        logging.warning(_('⚠️  Storage State 加载失败: {error}', '⚠️  Storage State failed to load: {error}', error=e))
        logging.info(_('   回退到创建空白 context...', '   Falling back to a blank context...'))
        return await browser.new_context(**context_options)


async def _handle_uncertain_login_status(current_url: str, page_content: str):
    """
    原子函数: 处理无法确定登录状态的情况
    
    职责: 单一 - 仅处理未知登录状态的调试和日志
    """
    logging.warning(_('⚠️  无法确定登录状态', '⚠️  Unable to determine login status'))
    logging.info(_('   当前URL: {url}', '   Current URL: {url}', url=current_url))
    logging.info(_('   页面中未找到 logout 链接', '   No logout link was found on the page'))
    logging.info(_('   未检测到 SSO 重定向', '   No SSO redirect was detected'))

    # 保存调试信息
    debug_path = '/tmp/moodle_login_uncertain.html'
    try:
        with open(debug_path, 'w', encoding='utf-8') as f:
            f.write(page_content)
        logging.debug(_('📝 已保存页面内容到: {path}', '📝 Saved page content to: {path}', path=debug_path))
    except Exception as e:
        logging.debug(_('保存调试文件失败: {error}', 'Failed to save debug file: {error}', error=e))


async def _check_login_errors(page_content: str, visited_sso: bool) -> bool:
    """
    原子函数: 检查页面内容中的错误指示
    
    职责: 单一 - 仅检查是否存在登录错误标志
    
    Args:
        page_content: 页面 HTML 内容
        visited_sso: 是否经历过 SSO 重定向
        
    Returns:
        True 如果检测到错误，False 否则
    """
    error_indicators = [
        'Sign in to your account',  # Microsoft 登录页面
        'Invalid login',  # Moodle 登录错误
        'You are not logged in',  # 未登录
        'enrol/index.php',  # 需要注册/登录
    ]

    has_error = any(indicator in page_content for indicator in error_indicators)

    if has_error and not visited_sso:
        logging.warning(_('⚠️  页面显示未登录，且未经历 SSO 重定向', '⚠️  Page indicates not logged in, and no SSO redirect occurred'))
        logging.warning(_('⚠️  SSO cookies 可能已完全过期', '⚠️  SSO cookies may be fully expired'))
        logging.info('')
        logging.info(_('💡 解决方案：', '💡 Solution:'))
        logging.info(_('   在浏览器中访问 Moodle 并完成 SSO 登录', '   Visit Moodle in your browser and complete SSO login'))
        logging.info(_('   之后将能够完全自动化', '   After that, automation should work'))
        logging.info('')
        return True
    
    return False


async def _is_on_login_page(current_url: str, page) -> bool:
    """
    原子函数: 检查页面是否在登录/认证页面
    
    职责: 单一 - 仅检查是否停留在登录页
    
    Args:
        current_url: 当前页面 URL
        page: Playwright 页面对象
        
    Returns:
        True 如果在登录页，False 如果不在
    """
    if 'login' in current_url.lower() or 'auth' in current_url.lower():
        # 区分不同的登录页面类型
        if 'microsoft' in current_url.lower() or 'google' in current_url.lower():
            logging.warning(_('⚠️  Playwright 停留在 SSO 授权页面', '⚠️  Playwright is stuck on the SSO authorization page'))
            logging.info(_('   原因：需要额外的交互式验证（Playwright 自动化无法完成）', '   Reason: extra interactive verification is required and Playwright cannot complete it automatically'))
            logging.info(_('   但这不代表 SSO cookies 完全过期！', '   This does not necessarily mean the SSO cookies are fully expired.'))
            logging.debug(_('   💡 Playwright 自动登录失败，将回退到浏览器导出的 cookies', '   💡 Playwright auto-login failed; falling back to browser-exported cookies'))
        else:
            logging.warning(_('⚠️  Playwright 停留在 Moodle 登录页面', '⚠️  Playwright is stuck on the Moodle login page'))
            logging.info(_('   原因：SSO cookies 可能已过期，或需要重新验证', '   Reason: SSO cookies may be expired or re-verification may be required'))

        # 保存当前页面截图（调试用）
        screenshot_path = '/tmp/moodle_sso_login_failed.png'
        try:
            await page.screenshot(path=screenshot_path)
            logging.debug(_('📸 已保存截图到: {path}', '📸 Saved screenshot to: {path}', path=screenshot_path))
        except Exception:
            pass

        return True
    return False


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

        logging.info(_('🚀 正在启动自动 SSO 登录...', '🚀 Starting automatic SSO login...'))

        # 1. 提取所有 cookies（完整复制用户浏览器状态）
        all_cookies = extract_all_cookies_from_browser(
            preferred_browser, moodle_domain, cookies_path
        )

        if len(all_cookies) == 0:
            logging.warning(_('⚠️  没有找到任何 cookies', '⚠️  No cookies found'))
            logging.info(_('💡 请先在浏览器中登录一次 Moodle（完成SSO认证）', '💡 First log in to Moodle once in your browser (complete SSO authentication)'))
            logging.info(_('   然后 moodle-dl 将能够自动刷新 MoodleSession', '   Then moodle-dl will be able to refresh MoodleSession automatically'))
            return False

        logging.info(_('✓ 准备将 {count} 个 cookies 迁移到 Playwright 浏览器', '✓ Preparing to migrate {count} cookies into the Playwright browser', count=len(all_cookies)))
        logging.info(_('   💡 原理：完整复制用户浏览器状态，实现自动化登录', '   💡 This copies the browser session state to enable automated login'))

        # 2. 准备 Storage State（Playwright 的推荐方式）
        # 关键改进：使用 storageState 而不是手动 add_cookies
        # 这样 Playwright 会自动处理所有域名的 cookies
        storage_state = {
            'cookies': all_cookies,
            'origins': []  # 可选，用于存储 localStorage
        }

        logging.info(_('   准备 Storage State: {count} 个 cookies', '   Preparing Storage State: {count} cookies', count=len(all_cookies)))

        # 3. 启动 Playwright 浏览器并使用 Storage State（原子函数）
        async with async_playwright() as p:
            # 启动浏览器（原子函数）
            browser = await _launch_playwright_browser(p, preferred_browser, headless)
            
            # 创建浏览器上下文（原子函数）
            context = await _setup_browser_context(browser, storage_state)

            # 4. 访问 Moodle 主页，触发 SSO 登录（原子函数）
            page = await context.new_page()
            moodle_url = f'https://{moodle_domain}/' if not moodle_domain.startswith('http') else moodle_domain

            try:
                # _navigate_to_moodle_and_wait already waits up to 5 minutes in
                # headful mode. Re-running it would reload the account picker and
                # interrupt manual Microsoft account selection.
                max_attempts = 1

                for attempt in range(max_attempts):
                    # 导航并等待重定向完成（原子函数）
                    visited_sso, current_url, page_content = await _navigate_to_moodle_and_wait(
                        page, moodle_domain, moodle_url, timeout, headless
                    )

                    # 检查登录状态（原子函数）
                    login_status = await _check_final_login_status(page_content, current_url, visited_sso, headless)

                    if login_status == -1:
                        # 登录失败
                        if not headless and attempt < max_attempts - 1:
                            logging.info(_('⏳ 等待用户完成操作...（重试中）', '⏳ Waiting for you to finish the operation... (retrying)'))
                            continue
                        await browser.close()
                        return False

                    elif login_status == 1:
                        # 登录成功
                        if visited_sso:
                            Log.success(_('✅ SSO 自动登录成功！（经历完整 SSO 重定向）', '✅ SSO auto-login succeeded (full SSO redirect completed)'))
                        else:
                            Log.success(_('✅ SSO 自动登录成功！（使用现有 cookies）', '✅ SSO auto-login succeeded (using existing cookies)'))

                        # 5 & 6. 提取并保存 cookies（原子函数）
                        save_success = await _save_session_cookies(context, auth_manager)

                        await browser.close()
                        return save_success

                    else:
                        # 登录状态未确定
                        if not headless:
                            # 有头模式：继续等待用户操作
                            if attempt < max_attempts - 1:
                                logging.info(_('⏳ 继续等待...（第 {attempt}/{max_attempts} 次尝试）', '⏳ Continuing to wait... (attempt {attempt}/{max_attempts})', attempt=attempt + 1, max_attempts=max_attempts))
                                continue

                        # 无头模式或已达到最大尝试次数
                        await _handle_uncertain_login_status(current_url, page_content)
                        await browser.close()
                        return False

            except Exception as page_error:
                logging.error(_('❌ 页面加载出错: {error}', '❌ Page loading error: {error}', error=page_error))

                # 尝试获取当前状态
                try:
                    current_url = page.url
                    logging.info(_('📍 出错时的URL: {url}', '📍 URL at time of error: {url}', url=current_url))

                    # 检查是否在 SSO 提供商页面
                    if _is_sso_provider_url(current_url):
                        logging.info(_('💡 当前在 SSO 提供商页面', '💡 Currently on an SSO provider page'))
                        logging.info(_('   这可能意味着需要重新认证', '   This may mean re-authentication is required'))
                        logging.info(_('   建议：在浏览器中手动登录一次，然后重试', '   Recommendation: log in once manually in the browser, then retry'))
                except Exception:
                    pass

                await browser.close()
                return False

    except ImportError as e:
        logging.error(_('❌ 缺少依赖: {error}', '❌ Missing dependency: {error}', error=e))
        logging.info(_('💡 请安装: pip install playwright browser-cookie3', '💡 Install: pip install playwright browser-cookie3'))
        logging.info(_('   然后运行: playwright install firefox', '   Then run: playwright install firefox'))
        return False

    except Exception as e:
        error_str = str(e)
        # 检测 Playwright 浏览器未安装的错误
        if "Executable doesn't exist" in error_str and "ms-playwright" in error_str:
            logging.error(_('❌ 自动登录失败: {error}', '❌ Automatic login failed: {error}', error=e))
            logging.error('')
            logging.error('╔════════════════════════════════════════════════════════════╗')
            logging.error(_('║  Playwright 浏览器未安装！                                   ║', '║  Playwright browsers are not installed!                    ║'))
            logging.error('║                                                             ║')
            logging.error(_('║  请运行以下命令安装浏览器：                                  ║', '║  Run the following command to install a browser:            ║'))
            logging.error('║                                                             ║')
            logging.error('║     playwright install chromium                            ║')
            logging.error('║                                                             ║')
            logging.error(_('║  或者安装所有浏览器：                                        ║', '║  Or install all browsers:                                   ║'))
            logging.error('║                                                             ║')
            logging.error('║     playwright install                                      ║')
            logging.error('║                                                             ║')
            logging.error('║  <3 Playwright Team                                         ║')
            logging.error('╚════════════════════════════════════════════════════════════╝')
            logging.error('')
        else:
            logging.error(_('❌ 自动登录失败: {error}', '❌ Automatic login failed: {error}', error=e))
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
