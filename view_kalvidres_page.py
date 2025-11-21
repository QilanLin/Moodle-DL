#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
访问 kalvidres 页面并查看内容
"""

import requests
from urllib.parse import urljoin
import time
import os

# Moodle 配置
MOODLE_URL = "https://keats.kcl.ac.uk"
TOKEN = "451d8ccfcac580505c984527356d9f67"
PRIVATETOKEN = "98f2c53a972c3d1394f59dde06b3e956"
USER_ID = 936054
KALVIDRES_URL = "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619"
COOKIE_FILE = "/Users/linqilan/CodingProjects/Moodle-DL/MoodleDL_Data/misc_files/cookie.txt"

# Moodle Mobile User-Agent
USER_AGENT = (
    'Mozilla/5.0 (Linux; Android 7.1.1; Moto G Play Build/NPIS26.48-43-2; wv) AppleWebKit/537.36'
    ' (KHTML, like Gecko) Version/4.0 Chrome/71.0.3578.99 Mobile Safari/537.36 MoodleMobile'
)

def get_autologin_key(moodle_url, token, privatetoken):
    """获取 autologin key"""
    api_url = urljoin(moodle_url, "/webservice/rest/server.php")

    params = {
        'moodlewsrestformat': 'json',
        'wsfunction': 'tool_mobile_get_autologin_key',
        'wstoken': token,
        'privatetoken': privatetoken
    }

    headers = {
        'User-Agent': USER_AGENT,
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    try:
        response = requests.post(api_url, data=params, headers=headers, timeout=30)

        if response.status_code == 200:
            data = response.json()

            if 'errorcode' in data:
                return None, data.get('message', 'Unknown error')

            return data, None
        else:
            return None, f"HTTP {response.status_code}"

    except Exception as e:
        return None, str(e)

def generate_cookies(autologin_data, userid):
    """生成 cookies"""
    if not autologin_data:
        return None, "No autologin data"

    autologin_url = autologin_data.get('autologinurl')
    key = autologin_data.get('key')

    post_data = {
        'key': key,
        'userid': userid
    }

    headers = {'User-Agent': USER_AGENT}

    try:
        session = requests.Session()
        response = session.post(autologin_url, data=post_data, headers=headers, timeout=30, allow_redirects=True)

        if session.cookies:
            return session, None
        else:
            return None, "No cookies generated"

    except Exception as e:
        return None, str(e)

def access_kalvidres_page(session, url):
    """访问 kalvidres 页面"""
    headers = {'User-Agent': USER_AGENT}

    try:
        response = session.get(url, headers=headers, timeout=30, allow_redirects=True)
        return response, None
    except Exception as e:
        return None, str(e)

def main():
    print("="*80)
    print("访问 Kalvidres 页面")
    print("="*80)
    print(f"目标 URL: {KALVIDRES_URL}\n")

    # 方法1: 尝试使用已存在的 cookies 文件
    if os.path.exists(COOKIE_FILE):
        print("✅ 找到现有的 cookies 文件，尝试使用...")
        session = requests.Session()

        # 注意：这里简化了 cookie 加载逻辑
        # 实际 moodle-dl 使用 MoodleDLCookieJar
        # 这里我们直接用 session 访问

        response = session.get(KALVIDRES_URL, timeout=30, allow_redirects=False)
        print(f"响应状态码: {response.status_code}")

        if response.status_code == 303 or response.status_code == 302:
            print(f"重定向到: {response.headers.get('Location', 'N/A')}")
    else:
        print("⚠️  未找到 cookies 文件\n")

    # 方法2: 生成新的 cookies
    print("\n尝试生成新的 cookies...")

    autologin_data, error = get_autologin_key(MOODLE_URL, TOKEN, PRIVATETOKEN)

    if error:
        print(f"❌ 获取 autologin key 失败: {error}")

        if "wait 6 minutes" in error.lower():
            print("\n⏰ 由于速率限制，无法生成新的 cookies")
            print("   moodle-dl 运行时会自动处理这个问题")

        # 尝试不用 cookies 直接访问（会失败，但可以看到需要什么）
        print("\n尝试不用 cookies 直接访问页面...")
        try:
            response = requests.get(KALVIDRES_URL, timeout=30, allow_redirects=False)
            print(f"响应状态码: {response.status_code}")

            if response.status_code in [302, 303]:
                location = response.headers.get('Location', '')
                print(f"重定向到: {location}")

                if 'login' in location.lower():
                    print("❌ 需要登录（需要 cookies）")

            # 显示页面的前500字符
            if response.text:
                print(f"\n页面内容预览（前500字符）:")
                print("-" * 80)
                print(response.text[:500])
                print("-" * 80)
        except Exception as e:
            print(f"❌ 访问失败: {str(e)}")

        return

    print("✅ 成功获取 autologin key")

    session, error = generate_cookies(autologin_data, USER_ID)

    if error:
        print(f"❌ 生成 cookies 失败: {error}")
        return

    print("✅ 成功生成 cookies")
    print(f"   Cookies 数量: {len(session.cookies)}")

    # 访问 kalvidres 页面
    print(f"\n正在访问 kalvidres 页面...")
    response, error = access_kalvidres_page(session, KALVIDRES_URL)

    if error:
        print(f"❌ 访问失败: {error}")
        return

    print(f"✅ 成功访问页面")
    print(f"   状态码: {response.status_code}")
    print(f"   最终 URL: {response.url}")

    # 分析页面内容
    print("\n" + "="*80)
    print("页面内容分析")
    print("="*80)

    html = response.text

    # 查找 iframe（Kaltura 播放器）
    if 'kaltura-player-iframe' in html:
        print("✅ 找到 Kaltura 播放器 iframe")

        import re
        iframe_match = re.search(r'<iframe[^>]+class="kaltura-player-iframe"[^>]+src=(["\'])([^"\']+)\1', html)
        if iframe_match:
            iframe_src = iframe_match.group(2)
            print(f"   iframe src: {iframe_src}")
    else:
        print("⚠️  未找到 Kaltura 播放器 iframe")

    # 查找视频标题
    if '<title>' in html:
        title_match = re.search(r'<title>([^<]+)</title>', html)
        if title_match:
            print(f"   页面标题: {title_match.group(1)}")

    # 显示页面长度
    print(f"   页面大小: {len(html)} 字符")

    # 保存 HTML 到文件
    output_file = "/Users/linqilan/CodingProjects/Moodle-DL/kalvidres_page.html"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n💾 完整 HTML 已保存到: {output_file}")

if __name__ == "__main__":
    main()
