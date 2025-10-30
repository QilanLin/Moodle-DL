#!/usr/bin/env python3
"""
使用与 moodle-dl 相同的代码逻辑测试 kalvidres 页面访问
"""

import os
import sys
import time
import requests
from http.cookiejar import MozillaCookieJar
from pathlib import Path

# 添加 moodle-dl 模块到 Python 路径
sys.path.insert(0, '/Users/linqilan/CodingProjects/Moodle-DL')

from moodle_dl.config import ConfigHelper
from moodle_dl.types import MoodleDlOpts
from moodle_dl.moodle.request_helper import RequestHelper
from moodle_dl.moodle.cookie_handler import CookieHandler
from moodle_dl.utils import PathTools as PT, MoodleDLCookieJar

# 配置
KALVIDRES_URL = "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619"

def setup_config():
    """设置配置"""
    # 创建默认的 MoodleDlOpts
    opts = MoodleDlOpts(
        init=False,
        config=False,
        new_token=False,
        change_notification_mail=False,
        change_notification_telegram=False,
        change_notification_discord=False,
        change_notification_ntfy=False,
        change_notification_xmpp=False,
        manage_database=False,
        delete_old_files=False,
        log_responses=False,
        add_all_visible_courses=False,
        sso=False,
        username=None,
        password=None,
        token=None,
        path='/Users/linqilan/CodingProjects',
        max_parallel_api_calls=10,
        max_parallel_downloads=5,
        max_parallel_yt_dlp=3,
        download_chunk_size=1024 * 1024,
        ignore_ytdl_errors=False,
        without_downloading_files=False,
        max_path_length_workaround=False,
        allow_insecure_ssl=False,
        use_all_ciphers=False,
        skip_cert_verify=False,
        verbose=True,
        quiet=False,
        log_to_file=False,
        log_file_path=None
    )
    config = ConfigHelper(opts)

    # 加载配置
    if config.is_present():
        config.load()
        print("✅ 配置文件已加载")
    else:
        print("❌ 配置文件不存在")
        return None, None

    return config, opts

def test_cookie_generation(config, opts):
    """测试 cookie 生成（使用 moodle-dl 的代码）"""
    print("\n" + "="*80)
    print("步骤 1: 生成 Moodle Cookies (使用 moodle-dl 代码)")
    print("="*80)

    try:
        token = config.get_token()
        privatetoken = config.get_privatetoken()
        moodle_url = config.get_moodle_URL()

        print(f"Token: {token[:20]}...")
        print(f"Private Token: {privatetoken[:20]}...")
        print(f"Moodle URL: {moodle_url.url_base}")

        # 创建 request_helper
        request_helper = RequestHelper(config, opts, moodle_url, token)

        # 获取用户 ID 和版本
        print("\n获取用户信息...")
        from moodle_dl.moodle.core_handler import CoreHandler
        core_handler = CoreHandler(request_helper)
        user_id, version = core_handler.fetch_userid_and_version()
        print(f"✅ 用户 ID: {user_id}")
        print(f"✅ Moodle 版本: {version}")

        # 创建 cookie_handler
        cookie_handler = CookieHandler(request_helper, version, config, opts)

        # 检查并获取 cookies
        print("\n尝试生成 cookies...")
        success = cookie_handler.check_and_fetch_cookies(privatetoken, user_id)

        if success:
            print("✅ Cookies 生成成功!")
            cookies_path = PT.get_cookies_path(config.get_misc_files_path())
            print(f"   Cookies 文件: {cookies_path}")

            if os.path.exists(cookies_path):
                print(f"   文件大小: {os.path.getsize(cookies_path)} bytes")
                return cookies_path, request_helper
        else:
            print("❌ Cookies 生成失败")
            return None, None

    except Exception as e:
        print(f"❌ 错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return None, None

def test_access_kalvidres_page(cookies_path, request_helper):
    """测试访问 kalvidres 页面（使用 moodle-dl 的方法）"""
    print("\n" + "="*80)
    print("步骤 2: 访问 Kalvidres 页面 (使用 moodle-dl 方法)")
    print("="*80)

    if not cookies_path or not os.path.exists(cookies_path):
        print("❌ Cookies 文件不存在，无法访问")
        return None

    try:
        # 使用 request_helper 的 get_URL 方法（和 moodle-dl 一样）
        print(f"正在访问: {KALVIDRES_URL}")
        print(f"使用 cookies: {cookies_path}")

        response, session = request_helper.get_URL(KALVIDRES_URL, cookies_path)

        print(f"✅ 响应状态码: {response.status_code}")
        print(f"   最终 URL: {response.url}")
        print(f"   Content-Type: {response.headers.get('Content-Type', 'N/A')}")

        return response.text

    except Exception as e:
        print(f"❌ 访问失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

def extract_kalvidres_content(html):
    """提取 kalvidres 页面内容（使用 moodle-dl 的正则表达式）"""
    print("\n" + "="*80)
    print("步骤 3: 提取页面内容 (使用 moodle-dl 的提取逻辑)")
    print("="*80)

    if not html:
        print("❌ 没有 HTML 内容")
        return

    import re
    import html as html_module

    print(f"HTML 长度: {len(html)} 字符")

    # 1. 提取页面标题
    title_match = re.search(r'<title>([^<]+)</title>', html)
    if title_match:
        print(f"✅ 页面标题: {title_match.group(1)}")

    # 2. 提取 Kaltura iframe（和 moodle-dl 的 kalvidres_lti.py 一样）
    print("\n查找 Kaltura 播放器 iframe...")
    iframe_pattern = r'<iframe[^>]+class="kaltura-player-iframe"[^>]+src=(["\'])(?P<url>[^"\']+)\1'
    iframe_match = re.search(iframe_pattern, html)

    if iframe_match:
        launch_url = html_module.unescape(iframe_match.group('url'))
        print(f"✅ 找到 Kaltura iframe!")
        print(f"   Launch URL: {launch_url[:100]}...")
    else:
        print("⚠️  未找到 Kaltura iframe")

        # 检查是否被重定向到登录页
        if 'login' in html.lower() or 'sign in' in html.lower():
            print("   ⚠️  页面包含登录提示，可能需要重新登录")

        # 显示页面片段用于调试
        print("\n页面内容片段（前1000字符）:")
        print("-" * 80)
        print(html[:1000])
        print("-" * 80)

    # 3. 查找 Errata 勘误信息
    print("\n查找 Errata 勘误信息...")
    errata_pattern = r'Errata:(.*?)(?=<div|<iframe|$)'
    errata_match = re.search(errata_pattern, html, re.DOTALL | re.IGNORECASE)

    if errata_match:
        errata_text = errata_match.group(1)
        print("✅ 找到 Errata 信息!")
        print("-" * 80)
        # 清理 HTML 标签
        errata_clean = re.sub(r'<[^>]+>', '', errata_text)
        errata_clean = html_module.unescape(errata_clean)
        errata_clean = errata_clean.strip()
        print(errata_clean[:500])
        print("-" * 80)
    else:
        print("⚠️  未找到 Errata 信息")

    # 4. 保存完整 HTML
    output_file = "/Users/linqilan/CodingProjects/Moodle-DL/kalvidres_page_real.html"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n💾 完整 HTML 已保存到: {output_file}")

def main():
    print("="*80)
    print("使用 moodle-dl 代码测试 Kalvidres 页面访问")
    print("="*80)

    # 1. 加载配置
    config, opts = setup_config()
    if not config:
        return

    # 2. 生成 cookies
    cookies_path, request_helper = test_cookie_generation(config, opts)

    if not cookies_path:
        print("\n⚠️  无法生成 cookies，但这可能是因为速率限制")
        print("   如果 moodle-dl 已经运行过，cookies 文件可能已经存在")

        # 检查是否有现有的 cookies 文件
        misc_files_path = config.get_misc_files_path()
        existing_cookies = PT.get_cookies_path(misc_files_path)

        if os.path.exists(existing_cookies):
            print(f"\n✅ 找到现有的 cookies 文件: {existing_cookies}")
            cookies_path = existing_cookies

            # 需要重新创建 request_helper
            token = config.get_token()
            moodle_url = config.get_moodle_URL()
            request_helper = RequestHelper(config, opts, moodle_url, token)
        else:
            print("\n❌ 没有现有的 cookies 文件")
            print("\n建议:")
            print("1. 等待 6 分钟后重试（避免速率限制）")
            print("2. 或者先运行一次 moodle-dl 生成 cookies")
            return

    # 3. 访问 kalvidres 页面
    html = test_access_kalvidres_page(cookies_path, request_helper)

    # 4. 提取内容
    if html:
        extract_kalvidres_content(html)

    print("\n" + "="*80)
    print("测试完成")
    print("="*80)

if __name__ == "__main__":
    main()
