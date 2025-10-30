#!/usr/bin/env python3
"""
测试 autologin 流程和 cookie 生成
"""

import sys
sys.path.insert(0, '/Users/linqilan/CodingProjects/Moodle-DL')

from moodle_dl.config import ConfigHelper
from moodle_dl.types import MoodleDlOpts
from moodle_dl.moodle.request_helper import RequestHelper
from moodle_dl.moodle.core_handler import CoreHandler
from moodle_dl.utils import PathTools as PT
import os

# 配置
opts = MoodleDlOpts(
    init=False, config=False, new_token=False,
    change_notification_mail=False, change_notification_telegram=False,
    change_notification_discord=False, change_notification_ntfy=False,
    change_notification_xmpp=False, manage_database=False,
    delete_old_files=False, log_responses=False,
    add_all_visible_courses=False, sso=False,
    username=None, password=None, token=None,
    path='/Users/linqilan/CodingProjects',
    max_parallel_api_calls=10, max_parallel_downloads=5,
    max_parallel_yt_dlp=3, download_chunk_size=1024*1024,
    ignore_ytdl_errors=False, without_downloading_files=False,
    max_path_length_workaround=False, allow_insecure_ssl=False,
    use_all_ciphers=False, skip_cert_verify=False,
    verbose=True, quiet=False, log_to_file=False, log_file_path=None
)

config = ConfigHelper(opts)
config.load()

token = config.get_token()
privatetoken = config.get_privatetoken()
moodle_url = config.get_moodle_URL()

print("=" * 80)
print("测试 Autologin 流程")
print("=" * 80)

# 创建 request_helper
request_helper = RequestHelper(config, opts, moodle_url, token)

# 获取 autologin key
print("\n1. 获取 autologin key...")
extra_data = {'privatetoken': privatetoken}
try:
    result = request_helper.post('tool_mobile_get_autologin_key', extra_data)
    print(f"   ✅ 成功获取 autologin key")
    print(f"   autologinurl: {result.get('autologinurl', 'N/A')}")
    print(f"   key: {result.get('key', 'N/A')[:30]}...")
except Exception as e:
    print(f"   ❌ 失败: {e}")
    sys.exit(1)

# 获取 user ID
print("\n2. 获取用户 ID...")
core_handler = CoreHandler(request_helper)
user_id, version = core_handler.fetch_userid_and_version()
print(f"   ✅ User ID: {user_id}")
print(f"   Moodle Version: {version}")

# 生成 cookies
print("\n3. POST 到 autologin URL 生成 cookies...")
post_data = {'key': result.get('key'), 'userid': user_id}
autologin_url = result.get('autologinurl')

cookies_path = PT.get_cookies_path(config.get_misc_files_path())
print(f"   Cookies 文件: {cookies_path}")

response, session = request_helper.post_URL(autologin_url, post_data, cookies_path)

print(f"   ✅ POST 响应状态码: {response.status_code}")
print(f"   最终 URL: {response.url}")
print(f"   Cookies 数量: {len(session.cookies)}")

# 显示生成的 cookies
print("\n4. 生成的 Cookies:")
for cookie in session.cookies:
    print(f"   - {cookie.name}: {cookie.value[:30]}... (domain: {cookie.domain}, secure: {cookie.secure})")

# 测试 cookies
print("\n5. 测试 cookies 是否有效...")
test_url = moodle_url.url_base
test_response, test_session = request_helper.get_URL(test_url, cookies_path)

print(f"   测试 URL: {test_url}")
print(f"   响应状态码: {test_response.status_code}")
print(f"   最终 URL: {test_response.url}")

# 检查是否包含 logout link
if 'login/logout.php' in test_response.text:
    print(f"   ✅ Cookies 有效（找到 logout 链接）")
else:
    print(f"   ❌ Cookies 无效（未找到 logout 链接）")

    # 检查是否被重定向到登录页
    if 'login' in test_response.url.lower():
        print(f"   ⚠️  被重定向到登录页")

    # 显示页面片段
    print("\n   页面内容片段（前500字符）:")
    print("   " + "-" * 76)
    print("   " + test_response.text[:500].replace('\n', '\n   '))
    print("   " + "-" * 76)

print("\n" + "=" * 80)
print("测试完成")
print("=" * 80)
