#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 Safari cookie 读取支持

检查 browser-cookie3 对 Safari 的实际支持情况
"""

import sys
import os

print("=" * 60)
print("Safari Cookie 读取支持测试")
print("=" * 60)

# 1. 检查 browser-cookie3 是否安装
print("\n1. 检查 browser-cookie3 库...")
try:
    import browser_cookie3
    print("   ✅ browser-cookie3 已安装")
    
    # 检查版本
    if hasattr(browser_cookie3, '__version__'):
        print(f"   版本: {browser_cookie3.__version__}")
    else:
        print("   版本: 未知")
    
    # 检查是否有 safari 方法
    if hasattr(browser_cookie3, 'safari'):
        print("   ✅ browser_cookie3.safari 方法存在")
    else:
        print("   ❌ browser_cookie3.safari 方法不存在")
        sys.exit(1)
        
except ImportError:
    print("   ❌ browser-cookie3 未安装")
    print("   安装命令: pip install browser-cookie3")
    sys.exit(1)

# 2. 检查 macOS
print("\n2. 检查操作系统...")
if sys.platform == 'darwin':
    print("   ✅ macOS 检测到")
else:
    print(f"   ⚠️  当前系统: {sys.platform}")
    print("   Safari cookie 读取仅在 macOS 上支持")
    sys.exit(1)

# 3. 检查 Safari cookie 文件是否存在
print("\n3. 检查 Safari cookie 文件...")
safari_cookie_path = os.path.expanduser('~/Library/Cookies/Cookies.binarycookies')
if os.path.exists(safari_cookie_path):
    print(f"   ✅ Safari cookie 文件存在: {safari_cookie_path}")
    file_size = os.path.getsize(safari_cookie_path)
    print(f"   文件大小: {file_size:,} 字节")
else:
    print(f"   ⚠️  Safari cookie 文件不存在: {safari_cookie_path}")
    print("   可能原因:")
    print("   - Safari 从未运行过")
    print("   - Safari 从未访问过任何网站")
    print("   - 文件路径不同（Safari 版本差异）")

# 4. 尝试读取 Safari cookies
print("\n4. 尝试读取 Safari cookies...")
try:
    print("   正在调用 browser_cookie3.safari()...")
    cj = browser_cookie3.safari()
    
    # 统计 cookies
    cookies_list = list(cj)
    cookie_count = len(cookies_list)
    
    print(f"   ✅ 成功读取 {cookie_count} 个 cookies")
    
    if cookie_count > 0:
        print("\n   示例 cookies (前5个):")
        for i, cookie in enumerate(cookies_list[:5], 1):
            print(f"   {i}. {cookie.name} ({cookie.domain})")
            print(f"      value: {cookie.value[:30]}...")
            print(f"      secure: {cookie.secure}, expires: {cookie.expires}")
    
    # 检查是否有 keats.kcl.ac.uk 的 cookies
    keats_cookies = [c for c in cookies_list if 'keats.kcl.ac.uk' in c.domain]
    if keats_cookies:
        print(f"\n   ✅ 找到 {len(keats_cookies)} 个 keats.kcl.ac.uk 的 cookies")
        for cookie in keats_cookies:
            print(f"      - {cookie.name}: {cookie.value[:30]}...")
    else:
        print("\n   ⚠️  未找到 keats.kcl.ac.uk 的 cookies")
        print("   可能原因:")
        print("   - Safari 中未登录 keats.kcl.ac.uk")
        print("   - Cookies 已过期")
        print("   - Safari 隐私设置阻止了 cookies")
    
except PermissionError as e:
    print(f"   ❌ 权限错误: {e}")
    print("\n   🔧 解决方案:")
    print("   1. 打开 '系统设置' → '隐私与安全性'")
    print("   2. 找到 '完全磁盘访问权限'")
    print("   3. 添加 Terminal 或 Python 应用")
    print("   4. 重新运行此脚本")
    
except Exception as e:
    print(f"   ❌ 读取失败: {type(e).__name__}: {e}")
    print("\n   可能原因:")
    print("   - Safari cookie 文件格式不兼容")
    print("   - browser-cookie3 版本过旧")
    print("   - macOS 版本不兼容")
    print("   - Safari 版本不兼容")

# 5. 总结
print("\n" + "=" * 60)
print("测试总结")
print("=" * 60)
print("""
结论：
- browser-cookie3 理论上支持 Safari
- 实际可用性取决于：
  1. macOS 权限设置
  2. Safari cookie 文件格式兼容性
  3. browser-cookie3 版本

建议：
- 如果测试失败，优先使用 Firefox 或 Chrome
- Safari 支持可能不稳定，建议作为备选方案
""")
