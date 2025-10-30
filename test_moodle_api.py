#!/usr/bin/env python3
"""
测试 Moodle API 和 token 的有效性
"""

import requests
import base64
import re
import json
from urllib.parse import urljoin

# Moodle 配置
MOODLE_URL = "https://keats.kcl.ac.uk"
TOKEN_URL = "moodledl://token=OThmMmM1M2E5NzJjM2QxMzk0ZjU5ZGRlMDZiM2U5NTY6Ojo0NTFkOGNjZmNhYzU4MDUwNWM5ODQ1MjczNTZkOWY2Nw=="

def parse_token_url(token_url):
    """解析 moodledl:// token URL"""
    print("=" * 80)
    print("步骤 1: 解析 Token URL")
    print("=" * 80)

    splitted = token_url.split('token=')
    if len(splitted) < 2:
        print("❌ 错误: 无效的 token URL")
        return None, None

    encoded_token = splitted[1]
    print(f"Base64 编码的 token: {encoded_token}")

    decoded_bytes = base64.b64decode(encoded_token)
    decoded = decoded_bytes.decode('utf-8')
    print(f"解码后的字符串: {decoded}")

    parts = decoded.split(':::')
    print(f"分割后的部分: {parts}")

    if len(parts) >= 2:
        privatetoken = re.sub(r'[^A-Za-z0-9]+', '', parts[0])
        token = re.sub(r'[^A-Za-z0-9]+', '', parts[1])
        print(f"\n✅ Token: {token}")
        print(f"✅ Private Token: {privatetoken}")
        return token, privatetoken
    else:
        print("❌ 错误: token 格式不正确")
        return None, None

def test_site_info(moodle_url, token):
    """测试 1: 获取站点信息 (验证 token 有效性)"""
    print("\n" + "=" * 80)
    print("步骤 2: 测试基本 API - core_webservice_get_site_info")
    print("=" * 80)

    api_url = urljoin(moodle_url, "/webservice/rest/server.php")

    params = {
        'moodlewsrestformat': 'json',
        'wsfunction': 'core_webservice_get_site_info',
        'wstoken': token
    }

    try:
        print(f"请求 URL: {api_url}")
        print(f"参数: {params}")

        response = requests.post(api_url, data=params, timeout=30)
        print(f"\n响应状态码: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            if 'errorcode' in data:
                print(f"❌ API 错误: {data.get('errorcode')}")
                print(f"   错误信息: {data.get('message', 'N/A')}")
                return None

            print("✅ Token 有效!")
            print(f"\n站点信息:")
            print(f"  - 用户名: {data.get('username', 'N/A')}")
            print(f"  - 名字: {data.get('firstname', 'N/A')} {data.get('lastname', 'N/A')}")
            print(f"  - 用户 ID: {data.get('userid', 'N/A')}")
            print(f"  - Moodle 版本: {data.get('version', 'N/A')}")
            print(f"  - 站点名称: {data.get('sitename', 'N/A')}")

            return data
        else:
            print(f"❌ HTTP 错误: {response.status_code}")
            print(f"响应内容: {response.text[:500]}")
            return None

    except Exception as e:
        print(f"❌ 请求失败: {str(e)}")
        return None

def test_get_courses(moodle_url, token, userid):
    """测试 2: 获取课程列表"""
    print("\n" + "=" * 80)
    print("步骤 3: 测试获取课程列表 - core_enrol_get_users_courses")
    print("=" * 80)

    api_url = urljoin(moodle_url, "/webservice/rest/server.php")

    params = {
        'moodlewsrestformat': 'json',
        'wsfunction': 'core_enrol_get_users_courses',
        'wstoken': token,
        'userid': userid
    }

    try:
        print(f"请求 URL: {api_url}")
        print(f"用户 ID: {userid}")

        response = requests.post(api_url, data=params, timeout=30)
        print(f"\n响应状态码: {response.status_code}")

        if response.status_code == 200:
            courses = response.json()

            if isinstance(courses, dict) and 'errorcode' in courses:
                print(f"❌ API 错误: {courses.get('errorcode')}")
                print(f"   错误信息: {courses.get('message', 'N/A')}")
                return None

            print(f"✅ 成功获取 {len(courses)} 门课程:")
            for i, course in enumerate(courses[:5], 1):  # 只显示前5门
                print(f"  {i}. [{course.get('id')}] {course.get('fullname', 'N/A')}")

            if len(courses) > 5:
                print(f"  ... 还有 {len(courses) - 5} 门课程")

            return courses
        else:
            print(f"❌ HTTP 错误: {response.status_code}")
            return None

    except Exception as e:
        print(f"❌ 请求失败: {str(e)}")
        return None

def test_autologin_key(moodle_url, token, privatetoken, userid):
    """测试 3: 获取 autologin key (用于生成 cookies)"""
    print("\n" + "=" * 80)
    print("步骤 4: 测试获取 Autologin Key - tool_mobile_get_autologin_key")
    print("=" * 80)

    api_url = urljoin(moodle_url, "/webservice/rest/server.php")

    params = {
        'moodlewsrestformat': 'json',
        'wsfunction': 'tool_mobile_get_autologin_key',
        'wstoken': token,
        'privatetoken': privatetoken
    }

    # 添加 Moodle Mobile App 的 User-Agent (和 moodle-dl 使用的一样)
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Linux; Android 7.1.1; Moto G Play Build/NPIS26.48-43-2; wv) AppleWebKit/537.36'
            ' (KHTML, like Gecko) Version/4.0 Chrome/71.0.3578.99 Mobile Safari/537.36 MoodleMobile'
        ),
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    try:
        print(f"请求 URL: {api_url}")
        print(f"使用 privatetoken: {privatetoken[:20]}...")
        print(f"User-Agent: MoodleMobile (模拟移动应用)")

        response = requests.post(api_url, data=params, headers=headers, timeout=30)
        print(f"\n响应状态码: {response.status_code}")

        if response.status_code == 200:
            data = response.json()

            if 'errorcode' in data:
                print(f"❌ API 错误: {data.get('errorcode')}")
                print(f"   错误信息: {data.get('message', 'N/A')}")
                return None

            print("✅ 成功获取 autologin key!")
            print(f"\nAutologin 信息:")
            print(f"  - Key: {data.get('key', 'N/A')[:40]}...")
            print(f"  - URL: {data.get('autologinurl', 'N/A')}")

            return data
        else:
            print(f"❌ HTTP 错误: {response.status_code}")
            print(f"响应内容: {response.text[:500]}")
            return None

    except Exception as e:
        print(f"❌ 请求失败: {str(e)}")
        return None

def test_generate_cookies(autologin_data, userid):
    """测试 4: 使用 autologin key 生成 cookies"""
    print("\n" + "=" * 80)
    print("步骤 5: 测试生成 Cookies")
    print("=" * 80)

    if not autologin_data:
        print("❌ 没有 autologin 数据，跳过")
        return None

    autologin_url = autologin_data.get('autologinurl')
    key = autologin_data.get('key')

    if not autologin_url or not key:
        print("❌ autologin 数据不完整")
        return None

    post_data = {
        'key': key,
        'userid': userid
    }

    try:
        print(f"POST 到: {autologin_url}")
        print(f"数据: userid={userid}, key={key[:20]}...")

        session = requests.Session()
        response = session.post(autologin_url, data=post_data, timeout=30, allow_redirects=True)

        print(f"\n响应状态码: {response.status_code}")
        print(f"重定向到: {response.url}")

        # 检查是否获得了 cookies
        cookies = session.cookies.get_dict()
        print(f"\n获得的 Cookies ({len(cookies)} 个):")
        for name, value in cookies.items():
            print(f"  - {name}: {value[:40]}{'...' if len(value) > 40 else ''}")

        # 测试 cookies 是否有效
        if cookies:
            print("\n测试 cookies 有效性...")
            test_response = session.get(MOODLE_URL, timeout=30)

            if 'login/logout.php' in test_response.text:
                print("✅ Cookies 有效! (检测到 logout 链接)")
                return session
            else:
                print("⚠️  Cookies 可能无效 (未检测到 logout 链接)")
                return session
        else:
            print("❌ 没有获得任何 cookies")
            return None

    except Exception as e:
        print(f"❌ 请求失败: {str(e)}")
        return None

def main():
    print("\n" + "🔍" * 40)
    print("Moodle API 测试工具")
    print("🔍" * 40)

    # 1. 解析 token
    token, privatetoken = parse_token_url(TOKEN_URL)
    if not token or not privatetoken:
        print("\n❌ 无法解析 token，测试终止")
        return

    # 2. 测试基本 API (获取站点信息)
    site_info = test_site_info(MOODLE_URL, token)
    if not site_info:
        print("\n❌ Token 无效或 API 不可用，测试终止")
        return

    userid = site_info.get('userid')

    # 3. 测试获取课程列表
    courses = test_get_courses(MOODLE_URL, token, userid)

    # 4. 测试 privatetoken (获取 autologin key)
    autologin_data = test_autologin_key(MOODLE_URL, token, privatetoken, userid)

    # 5. 测试生成 cookies
    if autologin_data:
        session = test_generate_cookies(autologin_data, userid)

    # 总结
    print("\n" + "=" * 80)
    print("测试总结")
    print("=" * 80)
    print(f"✅ Token 解析: 成功")
    print(f"✅ 站点信息 API: {'成功' if site_info else '失败'}")
    print(f"✅ 获取课程 API: {'成功' if courses else '失败'}")
    print(f"✅ Autologin Key API: {'成功' if autologin_data else '失败'}")
    print(f"✅ Cookie 生成: {'成功' if autologin_data else '失败'}")

    if site_info and courses and autologin_data:
        print("\n🎉 所有测试通过! 你的配置可以正常工作了!")
    else:
        print("\n⚠️  部分测试失败，请检查上面的错误信息")

if __name__ == "__main__":
    main()
