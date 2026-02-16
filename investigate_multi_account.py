#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调查多账号检测功能为什么没有工作
"""

import sqlite3
import json
from typing import List, Dict

def _detect_multiple_accounts(cookies: List[Dict], browser_name: str) -> List[Dict]:
    """
    检测 cookies 中是否存在多个 Microsoft 账号
"""
    # 检查 ESTSSSOTILES cookie
    sso_tiles_value = None
    sso_tiles_domain = None
    for cookie in cookies:
        if cookie.get('name') == 'ESTSSSOTILES':
            sso_tiles_value = cookie.get('value', '')
            sso_tiles_domain = cookie.get('domain', '')
            break

    print(f"\n=== ESTSSSOTILES 检查 ===")
    print(f"值: {sso_tiles_value}")
    print(f"域名: {sso_tiles_domain}")

    # 收集 ESTSAUTHPERSISTENT cookies
    ests_auth_persistent_cookies = []
    for cookie in cookies:
        name = cookie.get('name', '')
        domain = cookie.get('domain', '')
        is_ms_domain = any(keyword in domain.lower() for keyword in ['microsoft', 'live.com', 'microsoftonline.com'])
        if name == 'ESTSAUTHPERSISTENT' and is_ms_domain:
            ests_auth_persistent_cookies.append(cookie)

    print(f"\n=== ESTSAUTHPERSISTENT 检查 ===")
    print(f"找到 {len(ests_auth_persistent_cookies)} 个 ESTSAUTHPERSISTENT cookies:")
    for i, cookie in enumerate(ests_auth_persistent_cookies, 1):
        print(f"  [{i}] 域名: {cookie.get('domain')}, 值前缀: {cookie.get('value', '')[:32]}...")

    # 单个账号 - 不需要选择
    if len(ests_auth_persistent_cookies) <= 1:
        print(f"\n⚠️ 只有 {len(ests_auth_persistent_cookies)} 个 ESTSAUTHPERSISTENT cookie，不会触发多账号选择")
        return []

    # 解析 ESTSUSERLIST
    users_list = _parse_estsuserlist(cookies)
    print(f"\n=== ESTSUSERLIST 解析 ===")
    print(f"找到 {len(users_list)} 个用户:")
    for i, user in enumerate(users_list, 1):
        print(f"  [{i}] 邮箱: {user.get('email')}, 显示名: {user.get('display_name')}")

    return []

def _parse_estsuserlist(cookies: List[Dict]) -> List[Dict]:
    """解析 ESTSUSERLIST cookie"""
    import base64

    for cookie in cookies:
        if cookie.get('name') == 'ESTSUSERLIST':
            try:
                value = cookie.get('value', '')
                print(f"\n=== ESTSUSERLIST Cookie ===")
                print(f"原始值: {value[:100]}...")

                decoded = base64.b64decode(value).decode('utf-8', errors='ignore')
                print(f"解码后: {decoded[:200]}...")

                import json
                data = json.loads(decoded)
                print(f"JSON 数据: {json.dumps(data, indent=2)[:500]}...")

                users = []
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            users.append({
                                'email': item.get('login_name') or item.get('email') or item.get('upn') or 'Unknown',
                                'display_name': item.get('display_name') or item.get('name') or ''
                            })
                elif isinstance(data, dict):
                    users_list = data.get('users') or data.get('Users') or []
                    for item in users_list:
                        users.append({
                            'email': item.get('login_name') or item.get('email') or item.get('upn') or 'Unknown',
                            'display_name': item.get('display_name') or item.get('name') or ''
                        })
                return users if users else []
            except Exception as e:
                print(f"解析 ESTSUSERLIST 失败: {e}")
    return []

def load_cookies_from_db(db_path: str) -> List[Dict]:
    """从数据库加载 cookies"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT cookies, browser_name FROM cookie_batches
        ORDER BY created_at DESC
        LIMIT 1
    """)

    row = cursor.fetchone()
    if row:
        cookies = json.loads(row['cookies'])
        browser_name = row['browser_name']
        conn.close()
        return cookies, browser_name

    conn.close()
    return [], ''

def main():
    db_path = "/Users/linqilan/.config/moodle-dl/cookies.db"

    print("=" * 60)
    print("调查多账号检测功能")
    print("=" * 60)

    cookies, browser_name = load_cookies_from_db(db_path)

    if not cookies:
        print("❌ 没有找到 cookies")
        return

    print(f"\n从数据库加载了 {len(cookies)} 个 cookies")
    print(f"浏览器: {browser_name}")

    # 检查所有 Microsoft 相关的 cookies
    print(f"\n=== 所有 Microsoft 相关 Cookies ===")
    ms_cookies = []
    for cookie in cookies:
        domain = cookie.get('domain', '')
        name = cookie.get('name', '')
        is_ms_domain = any(keyword in domain.lower() for keyword in ['microsoft', 'live.com', 'microsoftonline.com'])
        if is_ms_domain:
            ms_cookies.append(cookie)
            print(f"  {name} ({domain}): {cookie.get('value', '')[:50]}...")

    print(f"\n共找到 {len(ms_cookies)} 个 Microsoft 相关 cookies")

    # 调用多账号检测
    accounts = _detect_multiple_accounts(cookies, browser_name)

    print(f"\n=== 检测结果 ===")
    if accounts:
        print(f"检测到 {len(accounts)} 个账号")
    else:
        print("没有检测到多个账号")

if __name__ == '__main__':
    main()
