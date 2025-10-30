#!/usr/bin/env python3
"""
直接测试 cookies 是否有效
"""

import sys
import os
sys.path.insert(0, '/Users/linqilan/CodingProjects/Moodle-DL')

from moodle_dl.utils import MoodleDLCookieJar, SslHelper

KALVIDRES_URL = "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619"
COOKIE_FILE = "/Users/linqilan/CodingProjects/Cookies.txt"

USER_AGENT = (
    'Mozilla/5.0 (Linux; Android 7.1.1; Moto G Play Build/NPIS26.48-43-2; wv) AppleWebKit/537.36'
    ' (KHTML, like Gecko) Version/4.0 Chrome/71.0.3578.99 Mobile Safari/537.36 MoodleMobile'
)

def test_cookies():
    """测试 cookies 加载和使用"""
    print("="*80)
    print("Cookie 调试测试")
    print("="*80)

    # 检查文件是否存在
    print(f"\n1. Cookies 文件: {COOKIE_FILE}")
    print(f"   存在: {os.path.exists(COOKIE_FILE)}")
    if os.path.exists(COOKIE_FILE):
        print(f"   大小: {os.path.getsize(COOKIE_FILE)} bytes")

    # 创建 session 和加载 cookies
    print(f"\n2. 创建 session 和加载 cookies...")
    session = SslHelper.custom_requests_session(False, False, False)
    session.cookies = MoodleDLCookieJar(COOKIE_FILE)

    if os.path.exists(COOKIE_FILE):
        session.cookies.load(ignore_discard=True, ignore_expires=True)
        print(f"   已加载 {len(session.cookies)} 个 cookies")

        # 显示所有 cookies
        print("\n   Cookies 列表:")
        for cookie in session.cookies:
            print(f"      - {cookie.name}: {cookie.value[:30]}... (domain: {cookie.domain})")

    # 测试访问页面
    print(f"\n3. 访问页面: {KALVIDRES_URL}")
    headers = {'User-Agent': USER_AGENT}
    response = session.get(KALVIDRES_URL, headers=headers, timeout=60)

    print(f"   状态码: {response.status_code}")
    print(f"   最终 URL: {response.url}")
    print(f"   Content-Type: {response.headers.get('Content-Type', 'N/A')}")

    # 检查是否被重定向
    if response.url != KALVIDRES_URL:
        print(f"   ⚠️  发生重定向")

    # 检查页面内容
    html = response.text

    if 'guest' in html.lower():
        print("   ⚠️  页面包含 'guest' 关键词")

    if 'You are currently using guest access' in html:
        print("   ❌ 仍然显示为访客访问")

    if 'kaltura-player-iframe' in html:
        print("   ✅ 找到 Kaltura 播放器 iframe!")
    else:
        print("   ❌ 未找到 Kaltura 播放器 iframe")

    # 检查响应 cookies
    print(f"\n4. 响应后的 cookies: {len(session.cookies)} 个")
    for cookie in session.cookies:
        print(f"      - {cookie.name}: {cookie.value[:30]}...")

    # 保存 HTML 用于分析
    output_file = "/Users/linqilan/CodingProjects/Moodle-DL/debug_cookies_page.html"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n💾 HTML 已保存到: {output_file}")

if __name__ == "__main__":
    test_cookies()
