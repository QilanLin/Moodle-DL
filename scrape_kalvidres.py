#!/usr/bin/env python3
"""
爬取 kalvidres 页面的文本和视频
"""

import requests
import re
import html
from http.cookiejar import MozillaCookieJar

# 配置
KALVIDRES_URL = "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619"
COOKIES_FILE = "/Users/linqilan/CodingProjects/Cookies.txt"

def load_cookies():
    """加载浏览器 cookies"""
    cookie_jar = MozillaCookieJar(COOKIES_FILE)
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    return cookie_jar

def fetch_page(url, cookies):
    """获取页面内容"""
    session = requests.Session()
    session.cookies = cookies

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    response = session.get(url, headers=headers, timeout=30)

    if response.status_code != 200:
        print(f"❌ 请求失败: {response.status_code}")
        return None

    # 检查是否被重定向到登录页
    if 'login' in response.url.lower() or 'enrol' in response.url.lower():
        print(f"❌ 被重定向到: {response.url}")
        print("   Cookies 可能已过期，请重新导出 cookies")
        return None

    return response.text

def extract_title(html_content):
    """提取页面标题"""
    match = re.search(r'<title>([^<]+)</title>', html_content)
    if match:
        return html.unescape(match.group(1))
    return None

def extract_errata(html_content):
    """提取 Errata 勘误文本"""
    # 查找包含 "Errata" 的部分
    pattern = r'Errata:(.*?)(?=<div class="activity-description|<iframe|$)'
    match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)

    if match:
        errata_html = match.group(1)
        # 清理 HTML 标签
        errata_text = re.sub(r'<[^>]+>', '', errata_html)
        # 解码 HTML 实体
        errata_text = html.unescape(errata_text)
        # 清理多余空白
        errata_text = re.sub(r'\s+', ' ', errata_text).strip()
        return errata_text

    return None

def extract_kaltura_iframe(html_content):
    """提取 Kaltura iframe URL"""
    # 查找 kaltura-player-iframe
    iframe_pattern = r'<iframe[^>]+class="kaltura-player-iframe"[^>]+src=(["\'])(?P<url>[^"\']+)\1'
    match = re.search(iframe_pattern, html_content)

    if match:
        iframe_url = html.unescape(match.group('url'))
        return iframe_url

    return None

def extract_kaltura_video_from_iframe(iframe_url, cookies):
    """从 iframe 中提取 Kaltura 视频 URL"""
    session = requests.Session()
    session.cookies = cookies

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }

    # 获取 iframe 内容
    response = session.get(iframe_url, headers=headers, timeout=30)
    iframe_html = response.text

    # 查找 entry_id (Kaltura 视频 ID)
    entry_id_match = re.search(r'entry_id["\']?\s*:\s*["\']([^"\']+)', iframe_html)
    partner_id_match = re.search(r'partner_id["\']?\s*:\s*["\']?(\d+)', iframe_html)
    uiconf_id_match = re.search(r'uiconf_id["\']?\s*:\s*["\']?(\d+)', iframe_html)

    if entry_id_match:
        entry_id = entry_id_match.group(1)
        partner_id = partner_id_match.group(1) if partner_id_match else 'unknown'
        uiconf_id = uiconf_id_match.group(1) if uiconf_id_match else 'unknown'

        return {
            'entry_id': entry_id,
            'partner_id': partner_id,
            'uiconf_id': uiconf_id,
            'kaltura_url': f'https://cdnapisec.kaltura.com/p/{partner_id}/sp/{partner_id}00/embedIframeJs/uiconf_id/{uiconf_id}/partner_id/{partner_id}?iframeembed=true&entry_id={entry_id}'
        }

    return None

def main():
    print("=" * 80)
    print("Kalvidres 页面爬取工具")
    print("=" * 80)
    print(f"\n目标 URL: {KALVIDRES_URL}\n")

    # 1. 加载 cookies
    print("1️⃣ 加载 cookies...")
    try:
        cookies = load_cookies()
        print(f"   ✅ 已加载 {len(cookies)} 个 cookies")
    except Exception as e:
        print(f"   ❌ 加载失败: {e}")
        print("\n请先导出浏览器 cookies:")
        print("   python3 export_browser_cookies.py")
        return

    # 2. 获取页面
    print("\n2️⃣ 获取页面内容...")
    html_content = fetch_page(KALVIDRES_URL, cookies)

    if not html_content:
        return

    print(f"   ✅ 成功获取 ({len(html_content)} 字符)")

    # 3. 提取页面标题
    print("\n3️⃣ 提取页面信息...")
    title = extract_title(html_content)
    if title:
        print(f"   📄 标题: {title}")

    # 4. 提取 Errata 文本
    print("\n4️⃣ 提取 Errata 勘误...")
    errata = extract_errata(html_content)

    if errata:
        print(f"   ✅ 找到 Errata 文本 ({len(errata)} 字符)")
        print("\n" + "─" * 80)
        print("Errata 内容:")
        print("─" * 80)
        print(errata[:500] + ("..." if len(errata) > 500 else ""))
        print("─" * 80)

        # 保存到文件
        errata_file = "/Users/linqilan/CodingProjects/Moodle-DL/errata_text.txt"
        with open(errata_file, 'w', encoding='utf-8') as f:
            f.write(errata)
        print(f"\n   💾 完整 Errata 已保存到: {errata_file}")
    else:
        print("   ⚠️  未找到 Errata 文本")

    # 5. 提取 Kaltura iframe
    print("\n5️⃣ 提取 Kaltura 视频...")
    iframe_url = extract_kaltura_iframe(html_content)

    if iframe_url:
        print(f"   ✅ 找到 Kaltura iframe")
        print(f"   🔗 iframe URL: {iframe_url[:80]}...")

        # 提取视频 ID
        print("\n6️⃣ 提取视频 ID...")
        kaltura_info = extract_kaltura_video_from_iframe(iframe_url, cookies)

        if kaltura_info:
            print(f"   ✅ Entry ID: {kaltura_info['entry_id']}")
            print(f"   ✅ Partner ID: {kaltura_info['partner_id']}")
            print(f"   ✅ Kaltura URL: {kaltura_info['kaltura_url']}")

            # 保存信息
            info_file = "/Users/linqilan/CodingProjects/Moodle-DL/kaltura_video_info.txt"
            with open(info_file, 'w', encoding='utf-8') as f:
                f.write(f"Video Title: {title}\n")
                f.write(f"Page URL: {KALVIDRES_URL}\n\n")
                f.write(f"Kaltura Entry ID: {kaltura_info['entry_id']}\n")
                f.write(f"Kaltura Partner ID: {kaltura_info['partner_id']}\n")
                f.write(f"Kaltura UI Conf ID: {kaltura_info['uiconf_id']}\n\n")
                f.write(f"Kaltura URL: {kaltura_info['kaltura_url']}\n\n")
                f.write(f"Download command:\n")
                f.write(f"yt-dlp --cookies {COOKIES_FILE} \"{KALVIDRES_URL}\" -o \"Week1-Intro.%(ext)s\"\n")

            print(f"\n   💾 视频信息已保存到: {info_file}")
        else:
            print("   ⚠️  无法提取视频 ID")
    else:
        print("   ❌ 未找到 Kaltura iframe")
        print("   可能原因：cookies 无效或页面结构变化")

    # 7. 保存完整 HTML
    html_file = "/Users/linqilan/CodingProjects/Moodle-DL/kalvidres_page_full.html"
    with open(html_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"\n7️⃣ 完整 HTML 已保存到: {html_file}")

    print("\n" + "=" * 80)
    print("✅ 爬取完成")
    print("=" * 80)

    # 提供下载建议
    if iframe_url:
        print("\n📥 下载视频方法:")
        print("\n方法 1 - 使用 yt-dlp (推荐):")
        print(f"  yt-dlp --cookies {COOKIES_FILE} \"{KALVIDRES_URL}\" -o \"Week1-Intro.%(ext)s\"")

        print("\n方法 2 - 使用 moodle-dl:")
        print("  ./run_moodle_dl.sh")

        print("\n" + "=" * 80)

if __name__ == '__main__':
    main()
