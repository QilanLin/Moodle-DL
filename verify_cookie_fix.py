#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单的 Cookie 加载验证脚本

直接测试 http.cookiejar.MozillaCookieJar 的功能，
不依赖完整的 moodle-dl 环境。
"""

import http.cookiejar
import tempfile
import os


def test_standard_library_cookie_loading():
    """测试标准库加载 Netscape Cookie"""
    
    print("=" * 60)
    print("测试: Python 标准库 Cookie 加载")
    print("=" * 60)
    
    # 测试 1: 标准格式（TRUE/FALSE）
    print("\n测试 1: 标准 Netscape 格式 (TRUE/FALSE)")
    cookie_content = """# Netscape HTTP Cookie File
keats.kcl.ac.uk	FALSE	/	TRUE	-1	MoodleSession	session_value_123
keats.kcl.ac.uk	FALSE	/	FALSE	1735689600	TestCookie	test_value
"""
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write(cookie_content)
        temp_file = f.name
    
    try:
        jar = http.cookiejar.MozillaCookieJar(temp_file)
        jar.load(ignore_discard=True, ignore_expires=True)
        
        print(f"  ✅ 成功加载 {len(jar)} 个 cookies")
        
        for cookie in jar:
            print(f"  📋 Cookie: {cookie.name}")
            print(f"     - Value: {cookie.value[:20]}...")
            print(f"     - Secure: {cookie.secure}")
            print(f"     - Expires: {cookie.expires}")
            print(f"     - Domain: {cookie.domain}")
        
        # 验证关键属性
        cookies_list = list(jar)
        assert len(cookies_list) == 2, f"应该有 2 个 cookies，实际: {len(cookies_list)}"
        
        cookie1 = cookies_list[0]
        assert cookie1.name == 'MoodleSession', f"第一个 cookie 名称应为 MoodleSession，实际: {cookie1.name}"
        assert cookie1.secure == True, f"第一个 cookie 应为 secure (TRUE)，实际: {cookie1.secure}"
        
        cookie2 = cookies_list[1]
        assert cookie2.name == 'TestCookie', f"第二个 cookie 名称应为 TestCookie，实际: {cookie2.name}"
        assert cookie2.secure == False, f"第二个 cookie 不应为 secure (FALSE)，实际: {cookie2.secure}"
        
        print("  ✅ 所有断言通过！")
        
    finally:
        os.unlink(temp_file)
    
    # 测试 2: 转换为字典格式
    print("\n测试 2: 转换为字典格式（用于 Playwright）")
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write(cookie_content)
        temp_file = f.name
    
    try:
        jar = http.cookiejar.MozillaCookieJar(temp_file)
        jar.load(ignore_discard=True, ignore_expires=True)
        
        # 转换为字典列表
        cookies_dict = []
        for cookie in jar:
            cookie_dict = {
                'domain': cookie.domain,
                'path': cookie.path,
                'secure': 1 if cookie.secure else 0,
                'expires': int(cookie.expires) if cookie.expires else None,
                'name': cookie.name,
                'value': cookie.value,
                'httponly': 1 if cookie.has_nonstandard_attr('HttpOnly') else 0,
                'samesite': 'Lax'
            }
            cookies_dict.append(cookie_dict)
        
        print(f"  ✅ 成功转换 {len(cookies_dict)} 个 cookies 为字典格式")
        
        for i, c in enumerate(cookies_dict, 1):
            print(f"  📋 Cookie {i}:")
            print(f"     - name: {c['name']}")
            print(f"     - secure: {c['secure']} (类型: {type(c['secure']).__name__})")
            print(f"     - expires: {c['expires']}")
        
        # 验证类型
        assert isinstance(cookies_dict[0]['secure'], int), "secure 应为整数类型"
        assert cookies_dict[0]['secure'] == 1, "TRUE 应转换为 1"
        assert cookies_dict[1]['secure'] == 0, "FALSE 应转换为 0"
        
        print("  ✅ 字典转换验证通过！")
        
    finally:
        os.unlink(temp_file)
    
    # 测试 3: 边界情况
    print("\n测试 3: 边界情况处理")
    
    edge_cases = [
        ("会话 cookie (expires=-1)", "example.com\tFALSE\t/\tTRUE\t-1\tSessionCookie\tvalue"),
        ("已过期 (expires=0)", "example.com\tFALSE\t/\tFALSE\t0\tExpiredCookie\tvalue"),
        ("长期有效", "example.com\tFALSE\t/\tTRUE\t2147483647\tLongCookie\tvalue"),
    ]
    
    for desc, cookie_line in edge_cases:
        cookie_content = f"# Netscape HTTP Cookie File\n{cookie_line}\n"
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(cookie_content)
            temp_file = f.name
        
        try:
            jar = http.cookiejar.MozillaCookieJar(temp_file)
            jar.load(ignore_discard=True, ignore_expires=True)
            
            cookie = list(jar)[0]
            print(f"  ✅ {desc}: expires={cookie.expires}")
            
        finally:
            os.unlink(temp_file)
    
    print("\n" + "=" * 60)
    print("✅ 所有测试通过！标准库方案可靠！")
    print("=" * 60)


if __name__ == '__main__':
    try:
        test_standard_library_cookie_loading()
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
