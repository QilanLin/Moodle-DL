#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调查多账号检测功能为什么没有被触发

问题：用户选择了 Firefox，浏览器中有多个 Microsoft 账号，
但多账号选择提示没有出现
"""

import sqlite3
import json
from typing import List, Dict

# 导入需要测试的函数
import sys
sys.path.insert(0, '/Users/linqilan/CodingProjects/moodle/Moodle-DL')

from moodle_dl.auto_sso_login import (
    _detect_multiple_accounts,
    _parse_estsuserlist,
    extract_all_cookies_from_browser,
)


def analyze_cookies_from_db(db_path: str) -> List[Dict]:
    """从数据库加载并分析 cookies"""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 获取最新的 cookies
        cursor.execute("""
            SELECT cookies, browser_name, source
            FROM cookie_batches
            ORDER BY created_at DESC
            LIMIT 5
        """)

        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            cookies = json.loads(row['cookies'])
            results.append({
                'cookies': cookies,
                'browser_name': row['browser_name'],
                'source': row['source']
            })

        return results
    except Exception as e:
        print(f"❌ 从数据库加载 cookies 失败: {e}")
        return []


def analyze_microsoft_cookies(cookies: List[Dict]) -> None:
    """分析 Microsoft 相关的 cookies"""
    print("\n" + "=" * 60)
    print("Microsoft 相关 Cookies 分析")
    print("=" * 60)

    ms_cookies = []
    ests_auth_persistent_count = 0
    ests_auth_persistent_domains = []
    estsssotiles_value = None
    estsuserlist_value = None

    for cookie in cookies:
        domain = cookie.get('domain', '')
        name = cookie.get('name', '')
        value = cookie.get('value', '')

        # 检查是否是 Microsoft 域
        is_ms_domain = any(keyword in domain.lower() for keyword in ['microsoft', 'live.com', 'microsoftonline.com'])

        if is_ms_domain:
            ms_cookies.append(cookie)

            # 检查关键 cookies
            if name == 'ESTSAUTHPERSISTENT':
                ests_auth_persistent_count += 1
                ests_auth_persistent_domains.append(domain)
                print(f"\n✅ ESTSAUTHPERSISTENT 找到！")
                print(f"   域名: {domain}")
                print(f"   值前缀: {value[:40]}...")

            elif name == 'ESTSSSOTILES':
                ests_ssotiles_value = value
                print(f"\n✅ ESTSSSOTILES 找到！")
                print(f"   域名: {domain}")
                print(f"   值: {value}")
                print(f"   {'⚠️ 值为 1，表示账号选择器被触发！' if value == '1' else ''}")

            elif name == 'ESTSUSERLIST':
                estsuserlist_value = value
                print(f"\n✅ ESTSUSERLIST 找到！")
                print(f"   域名: {domain}")
                print(f"   值前缀: {value[:60]}...")

    print(f"\n📊 统计信息：")
    print(f"   Microsoft cookies 总数: {len(ms_cookies)}")
    print(f"   ESTSAUTHPERSISTENT 数量: {ests_auth_persistent_count}")
    print(f"   ESTSAUTHPERSISTENT 域名: {ests_auth_persistent_domains}")
    print(f"   ESTSSSOTILES 值: {ests_ssotiles_value}")
    print(f"   ESTSUSERLIST 存在: {'是' if estsuserlist_value else '否'}")

    return {
        'ests_auth_persistent_count': ests_auth_persistent_count,
        'ests_ssotiles_value': ests_ssotiles_value,
        'estsuserlist_value': estsuserlist_value,
        'ests_auth_persistent_domains': ests_auth_persistent_domains,
    }


def test_multi_account_detection(cookies: List[Dict], browser_name: str) -> None:
    """测试多账号检测功能"""
    print("\n" + "=" * 60)
    print("多账号检测测试")
    print("=" * 60)

    # 调用多账号检测函数
    accounts = _detect_multiple_accounts(cookies, browser_name)

    print(f"\n检测结果：")
    print(f"   检测到的账号数量: {len(accounts)}")

    if len(accounts) > 0:
        print(f"\n✅ 检测到多个账号！")
        for i, account in enumerate(accounts, 1):
            user_info = account.get('user_info', {})
            email = user_info.get('email', 'Unknown')
            display_name = user_info.get('display_name', '')
            domain = account.get('domain', '')

            print(f"\n   账号 {i}:")
            print(f"      邮箱: {email}")
            if display_name:
                print(f"      显示名: {display_name}")
            print(f"      域名: {domain}")
            print(f"      ESTSAUTHPERSISTENT 前缀: {account.get('ests_auth_persistent', '')[:40]}...")
    else:
        print(f"\n❌ 没有检测到多个账号")
        print(f"   可能原因：")
        print(f"   1. 只有一个 ESTSAUTHPERSISTENT cookie")
        print(f"   2. ESTSSSOTILES 不等于 1")
        print(f"   3. ESTSUSERLIST 解析失败或只有一个用户")


def test_estsuserlist_parsing(cookies: List[Dict]) -> None:
    """测试 ESTSUSERLIST 解析"""
    print("\n" + "=" * 60)
    print("ESTSUSERLIST 解析测试")
    print("=" * 60)

    users_list = _parse_estsuserlist(cookies)

    print(f"\n解析结果：")
    print(f"   用户数量: {len(users_list)}")

    if len(users_list) > 0:
        print(f"\n✅ 成功解析用户列表：")
        for i, user in enumerate(users_list, 1):
            email = user.get('email', 'Unknown')
            display_name = user.get('display_name', '')
            print(f"   用户 {i}: {email} ({display_name})")
    else:
        print(f"\n❌ 未能解析用户列表")
        print(f"   可能原因：")
        print(f"   1. ESTSUSERLIST cookie 不存在")
        print(f"   2. Base64 解码失败")
        print(f"   3. JSON 格式无效")


def test_extract_all_cookies(browser_name: str = 'firefox') -> None:
    """测试 extract_all_cookies_from_browser 函数"""
    print("\n" + "=" * 60)
    print("extract_all_cookies_from_browser 测试")
    print("=" * 60)

    print("\n📝 注意：如果 ESTSSSOTILES=1 但没有 ESTSUSERLIST，应该会看到警告信息")
    print("📝 警告会出现在下方：\n")

    try:
        # 这个函数会进行多账号检测
        cookies = extract_all_cookies_from_browser(
            browser_name=browser_name,
            moodle_domain='keats.kcl.ac.uk',
            cookies_path='/tmp/test_cookies.txt'
        )

        print(f"\n✅ 成功读取 {len(cookies)} 个 cookies")

    except Exception as e:
        print(f"\n❌ 读取 cookies 失败: {e}")
        import traceback
        traceback.print_exc()


def main():
    """主函数"""
    print("=" * 60)
    print("多账号检测问题调查工具")
    print("=" * 60)

    # 尝试从数据库加载 cookies
    db_paths = [
        '/Users/linqilan/.config/moodle-dl/cookies.db',
        '~/Library/Application Support/moodle-dl/cookies.db',
    ]

    cookies_data = None
    for db_path in db_paths:
        expanded_path = os.path.expanduser(db_path)
        if os.path.exists(expanded_path):
            print(f"\n📂 从数据库加载 cookies: {expanded_path}")
            cookies_data = analyze_cookies_from_db(expanded_path)
            break

    if not cookies_data or len(cookies_data) == 0:
        print("\n⚠️  数据库中没有找到 cookies")
        print("   尝试直接从浏览器读取...")
        test_extract_all_cookies('firefox')
        return

    # 分析最新的 cookies
    latest = cookies_data[0]
    cookies = latest['cookies']
    browser_name = latest['browser_name']
    source = latest['source']

    print(f"\n📊 Cookie 批次信息：")
    print(f"   来源: {source}")
    print(f"   浏览器: {browser_name}")
    print(f"   Cookies 数量: {len(cookies)}")

    # 分析 Microsoft cookies
    ms_info = analyze_microsoft_cookies(cookies)

    # 测试 ESTSUSERLIST 解析
    test_estsuserlist_parsing(cookies)

    # 测试多账号检测
    test_multi_account_detection(cookies, browser_name)

    # 诊断
    print("\n" + "=" * 60)
    print("诊断结果")
    print("=" * 60)

    should_trigger = (
        ms_info['ests_ssotiles_value'] == '1' or
        ms_info['ests_auth_persistent_count'] > 1
    )

    if should_trigger:
        print("\n✅ 应该触发多账号选择！")
        print(f"   原因：")
        if ms_info['ests_ssotiles_value'] == '1':
            print(f"   - ESTSSSOTILES = 1（账号选择器被触发）")
        if ms_info['ests_auth_persistent_count'] > 1:
            print(f"   - 有 {ms_info['ests_auth_persistent_count']} 个 ESTSAUTHPERSISTENT cookies")
    else:
        print("\n❌ 不应该触发多账号选择")
        print(f"   原因：")
        print(f"   - ESTSSSOTILES = {ms_info['ests_ssotiles_value']}（不是 1）")
        print(f"   - 只有 {ms_info['ests_auth_persistent_count']} 个 ESTSAUTHPERSISTENT cookies")


if __name__ == '__main__':
    import os
    main()
