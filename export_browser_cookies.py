#!/usr/bin/env python3
"""
从浏览器自动导出 Moodle cookies

要求：pip install browser-cookie3
"""

import os
import sys
import glob
import platform

try:
    import browser_cookie3
except ImportError:
    print("错误：需要安装 browser-cookie3")
    print("运行：pip install browser-cookie3")
    sys.exit(1)


def find_browser_cookie_path(browser_name: str) -> str:
    """
    自动检测指定浏览器的 cookie 文件路径

    Args:
        browser_name: 浏览器名称 (zen, waterfox, arc 等)

    Returns:
        cookie 文件的绝对路径，如果找不到则返回 None
    """
    system = platform.system()

    # 定义不同浏览器在不同系统上的路径模式
    browser_paths = {
        'zen': {
            'Darwin': '~/Library/Application Support/zen/Profiles/*.default*/cookies.sqlite',
            'Linux': ['~/.zen/Profiles/*.default*/cookies.sqlite',
                     '~/.var/app/app.zen_browser.zen/zen/Profiles/*.default*/cookies.sqlite'],  # Flatpak
            'Windows': os.path.join(os.getenv('APPDATA', ''), 'zen', 'Profiles', '*.default*', 'cookies.sqlite')
        },
        'waterfox': {
            'Darwin': '~/Library/Application Support/Waterfox/Profiles/*.default*/cookies.sqlite',
            'Linux': '~/.waterfox/Profiles/*.default*/cookies.sqlite',
            'Windows': os.path.join(os.getenv('APPDATA', ''), 'Waterfox', 'Profiles', '*.default*', 'cookies.sqlite')
        },
        'arc': {
            'Darwin': '~/Library/Application Support/Arc/User Data/*/Cookies',
            'Linux': None,  # Arc 不支持 Linux
            'Windows': os.path.join(os.getenv('LOCALAPPDATA', ''), 'Arc', 'User Data', '*', 'Cookies')
        },
        'firefox': {
            'Darwin': '~/Library/Application Support/Firefox/Profiles/*.default*/cookies.sqlite',
            'Linux': '~/.mozilla/firefox/*.default*/cookies.sqlite',
            'Windows': os.path.join(os.getenv('APPDATA', ''), 'Mozilla', 'Firefox', 'Profiles', '*.default*', 'cookies.sqlite')
        },
        'librewolf': {
            'Darwin': '~/Library/Application Support/LibreWolf/Profiles/*.default*/cookies.sqlite',
            'Linux': '~/.librewolf/*.default*/cookies.sqlite',
            'Windows': os.path.join(os.getenv('APPDATA', ''), 'LibreWolf', 'Profiles', '*.default*', 'cookies.sqlite')
        }
    }

    if browser_name.lower() not in browser_paths:
        return None

    paths = browser_paths[browser_name.lower()].get(system)

    if not paths:
        return None

    # 处理多个可能的路径（如 Flatpak）
    if isinstance(paths, list):
        for path_pattern in paths:
            expanded = os.path.expanduser(path_pattern)
            matches = glob.glob(expanded)
            if matches:
                # 返回最近修改的文件
                return max(matches, key=os.path.getmtime)
    else:
        # 单个路径模式
        expanded = os.path.expanduser(paths)
        matches = glob.glob(expanded)
        if matches:
            # 返回最近修改的文件（通常是当前使用的 profile）
            return max(matches, key=os.path.getmtime)

    return None


def export_cookies_from_browser(domain: str, output_file: str, browser_name='chrome'):
    """从指定浏览器导出 cookies"""

    print(f"正在从 {browser_name} 导出 cookies...")

    try:
        # 定义浏览器到 browser_cookie3 方法的映射
        # 使用元组: (方法, 是否需要自定义路径)
        browser_methods = {
            'chrome': (browser_cookie3.chrome, False),
            'firefox': (browser_cookie3.firefox, False),
            'edge': (browser_cookie3.edge, False),
            'safari': (browser_cookie3.safari, False),
            'brave': (browser_cookie3.brave, False),
            'vivaldi': (browser_cookie3.vivaldi, False),
            'opera': (browser_cookie3.opera, False),
            'chromium': (browser_cookie3.chromium, False),
            'librewolf': (browser_cookie3.librewolf, False),
            # 需要自定义路径的浏览器
            'zen': (browser_cookie3.firefox, True),  # Zen 使用 Firefox 格式
            'waterfox': (browser_cookie3.firefox, True),  # Waterfox 使用 Firefox 格式
            'arc': (browser_cookie3.chrome, True),  # Arc 使用 Chrome 格式
        }

        # 动态添加其他可能支持的方法
        for method_name in ['opera_gx']:
            if hasattr(browser_cookie3, method_name):
                browser_methods[method_name] = (getattr(browser_cookie3, method_name), False)

        # 获取对应的方法
        if browser_name in browser_methods:
            method, needs_custom_path = browser_methods[browser_name]

            if needs_custom_path:
                # 自动检测 cookie 文件路径
                print(f"  正在检测 {browser_name} 的 cookie 文件路径...")
                cookie_path = find_browser_cookie_path(browser_name)

                if not cookie_path:
                    print(f"❌ 未找到 {browser_name} 的 cookie 文件")
                    print(f"   可能的原因：")
                    print(f"   1. {browser_name} 未安装")
                    print(f"   2. {browser_name} 的 profile 路径不标准")
                    print(f"\n   💡 解决方案：")
                    print(f"   • 确保已安装 {browser_name} 并至少运行过一次")
                    print(f"   • 或使用浏览器扩展 'Get cookies.txt LOCALLY' 手动导出")
                    return False

                print(f"  ✓ 找到 cookie 文件: {cookie_path}")
                cj = method(cookie_file=cookie_path)
            else:
                # 使用默认路径
                cj = method()
        else:
            print(f"❌ 不支持的浏览器：{browser_name}")
            print("💡 支持的浏览器：chrome, firefox, brave, vivaldi, opera, edge, chromium, librewolf, safari, zen, waterfox, arc")
            print("   建议：选择'自动检测所有浏览器'（选项 7）")
            return False

        # 转换为列表以便计数和过滤
        cookies_list = []
        # 提取 Moodle 的主域名（去掉子域名）
        moodle_main_domain = domain.split('.')[-2] + '.' + domain.split('.')[-1] if '.' in domain else domain

        for cookie in cj:
            # 保存 Moodle 相关的 cookies 和可能的 SSO cookies
            # 策略：保存包含 Moodle 域名的 cookies，以及其他常见认证域名的 cookies
            cookie_domain = cookie.domain.lstrip('.')

            # 保存 Moodle 域名的 cookies
            if moodle_main_domain in cookie_domain:
                cookies_list.append(cookie)
            # 保存可能的 SSO cookies（常见的认证提供商域名）
            elif any(sso in cookie_domain.lower() for sso in [
                'microsoft', 'google', 'okta', 'shibboleth',
                'saml', 'oauth', 'login', 'auth', 'sso'
            ]):
                cookies_list.append(cookie)

        if not cookies_list:
            print(f"❌ 未找到 {domain} 的 cookies")
            print(f"   请确保：")
            print(f"   1. 已在浏览器中登录 {domain}")
            print(f"   2. 浏览器正在运行或最近使用过")
            print(f"\n   💡 提示：")
            if browser_name == 'firefox':
                print(f"   • 如果你使用 Zen/Waterfox/LibreWolf 等替代浏览器，")
                print(f"     请选择'自动检测所有浏览器'或在 Firefox 中登录")
            elif browser_name == 'chrome':
                print(f"   • 如果你使用 Brave/Arc/Vivaldi 等 Chromium 浏览器，")
                print(f"     可能需要选择对应的具体浏览器")
            print(f"   • 或选择'自动检测所有浏览器'（选项 7）")
            return False

        # 备份现有文件
        if os.path.exists(output_file):
            backup_file = output_file + '.backup'
            os.rename(output_file, backup_file)
            print(f"✅ 已备份现有文件到: {backup_file}")

        # 写入 Netscape cookie 格式
        with open(output_file, 'w') as f:
            f.write('# Netscape HTTP Cookie File\n')
            f.write(f'# Exported from {browser_name} browser\n')
            f.write(f'# Domain: {domain}\n')
            f.write('# This file is generated by export_browser_cookies.py\n\n')

            for cookie in cookies_list:
                # Netscape cookie format:
                # domain, flag, path, secure, expiration, name, value
                cookie_domain = cookie.domain
                flag = 'TRUE' if cookie_domain.startswith('.') else 'FALSE'
                path = cookie.path
                secure = 'TRUE' if cookie.secure else 'FALSE'
                expires = cookie.expires if cookie.expires else 0
                name = cookie.name
                value = cookie.value

                f.write(f'{cookie_domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n')

        print(f"✅ 成功导出 {len(cookies_list)} 个 cookies 到: {output_file}")

        # 显示关键 cookies（Moodle session 和 SSO cookies）
        print("\n关键 Cookies:")
        shown_cookies = set()
        for cookie in cookies_list:
            # 显示 MoodleSession
            if 'moodle' in cookie.name.lower() and 'session' in cookie.name.lower():
                if cookie.name not in shown_cookies:
                    print(f"  ✓ {cookie.name}: {cookie.value[:30]}...")
                    shown_cookies.add(cookie.name)
            # 显示非 Moodle 域名的 cookies（可能是 SSO）
            elif moodle_main_domain not in cookie.domain.lstrip('.'):
                if cookie.name not in shown_cookies:
                    print(f"  ✓ {cookie.name} ({cookie.domain}): {cookie.value[:20]}...")
                    shown_cookies.add(cookie.name)

        return True

    except Exception as e:
        print(f"❌ 导出失败: {e}")
        return False

def test_cookies(domain: str, cookies_file: str):
    """测试导出的 cookies 是否有效"""
    print("\n正在测试 cookies 有效性...")

    try:
        import requests

        session = requests.Session()

        # 加载 cookies
        from http.cookiejar import MozillaCookieJar
        cookie_jar = MozillaCookieJar(cookies_file)
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
        session.cookies = cookie_jar

        # 检查是否包含关键 cookies（方案C - Cookie域名检测）
        has_moodle_session = any('moodle' in cookie.name.lower() and 'session' in cookie.name.lower() for cookie in cookie_jar)

        # 提取 Moodle 主域名
        moodle_main_domain = domain.split('.')[-2] + '.' + domain.split('.')[-1] if '.' in domain else domain

        # 检测是否有 SSO cookies（任何非 Moodle 域名的 cookies）
        has_sso_cookies = any(
            moodle_main_domain not in cookie.domain.lstrip('.') and
            cookie.domain not in ['localhost', '127.0.0.1']
            for cookie in cookie_jar
        )

        # 测试访问
        moodle_url = f'https://{domain}/' if not domain.startswith('http') else domain
        response = session.get(moodle_url, timeout=10)

        # 方案B - 域名比较检测 SSO 重定向
        from urllib.parse import urlparse
        original_domain = urlparse(moodle_url).netloc
        final_domain = urlparse(response.url).netloc

        if 'login/logout.php' in response.text:
            print("✅ Cookies 有效！已成功认证")
            return True
        elif 'login/index.php' in response.url and original_domain == final_domain:
            # 重定向到同域名的登录页 = cookies 无效
            print("❌ Cookies 无效，被重定向到登录页")
            print(f"   请确保在浏览器中已登录 {domain}")
            return False
        elif original_domain != final_domain:
            # 重定向到不同域名 = SSO 认证
            if has_moodle_session and has_sso_cookies:
                print("✅ Cookies 导出成功（包含 SSO 认证 cookies）")
                print(f"   注意：访问时会重定向到 SSO 提供商 ({final_domain})")
                print("   这是正常的 SSO 登录流程")
                return True
            else:
                print(f"⚠️  被重定向到 SSO 提供商 ({final_domain})，但缺少关键 cookies")
                print(f"   MoodleSession: {'✓' if has_moodle_session else '✗'}")
                print(f"   SSO cookies: {'✓' if has_sso_cookies else '✗'}")
                return False
        else:
            print("⚠️  无法确定 cookies 状态")
            print(f"   响应 URL: {response.url}")
            # 如果包含关键 cookies，仍然认为成功
            if has_moodle_session:
                print("   但 cookies 文件包含 MoodleSession，应该可以使用")
                return True
            return False

    except Exception as e:
        print(f"⚠️  测试失败: {e}")
        return False

def export_cookies_interactive(domain: str = None, output_file: str = None, ask_browser: bool = True):
    """
    交互式导出 cookies（可被其他模块调用）

    Args:
        domain: Moodle 域名（例如 moodle.example.com，如果为 None 则会提示用户输入）
        output_file: 输出文件路径（默认为当前目录的 Cookies.txt）
        ask_browser: 是否询问用户选择浏览器（默认 True）

    Returns:
        bool: 是否成功导出并验证 cookies
    """
    # 如果没有提供域名，询问用户
    if domain is None:
        print("请输入你的 Moodle 网站域名")
        print("示例: moodle.university.edu 或 elearning.school.com")
        domain = input("Moodle 域名: ").strip()
        if not domain:
            print("❌ 域名不能为空")
            return False
    if output_file is None:
        output_file = os.path.join(os.getcwd(), 'Cookies.txt')

    print("=" * 80)
    print("Moodle Browser Cookies 导出工具")
    print("=" * 80)
    print(f"域名: {domain}")
    print(f"输出文件: {output_file}")
    print("=" * 80)

    if ask_browser:
        # 询问用户选择浏览器
        print("\n请选择你使用的浏览器或内核：")
        print("1. Chrome")
        print("2. Edge")
        print("3. Firefox")
        print("4. Safari")
        print("5. Chromium 内核浏览器（Brave, Vivaldi, Arc, Opera 等）")
        print("6. Firefox 内核浏览器（Zen, Waterfox, LibreWolf 等）")
        print("7. 自动检测所有浏览器")
        print("8. 手动指定浏览器名称")

        while True:
            try:
                choice = input("\n请输入选项 (1-8): ").strip()
                if choice == '1':
                    browsers_to_try = ['chrome']
                    break
                elif choice == '2':
                    browsers_to_try = ['edge']
                    break
                elif choice == '3':
                    browsers_to_try = ['firefox']
                    break
                elif choice == '4':
                    browsers_to_try = ['safari']
                    break
                elif choice == '5':
                    # Chromium 内核浏览器 - 二级选择
                    print("\n请选择具体的 Chromium 内核浏览器：")
                    print("1. Chrome")
                    print("2. Brave")
                    print("3. Vivaldi")
                    print("4. Opera")
                    print("5. Chromium")
                    print("6. Arc（通过自定义路径支持）")

                    chromium_choice = input("\n请输入选项 (1-6): ").strip()
                    chromium_map = {
                        '1': 'chrome',
                        '2': 'brave',      # ✅ 有专门的 brave() 方法
                        '3': 'vivaldi',    # ✅ 有专门的 vivaldi() 方法
                        '4': 'opera',      # ✅ 有专门的 opera() 方法
                        '5': 'chromium',   # ✅ 有专门的 chromium() 方法
                        '6': 'arc',        # ✅ 通过自定义路径支持
                    }
                    selected = chromium_map.get(chromium_choice)

                    if selected:
                        browsers_to_try = [selected]
                        break
                    else:
                        print("❌ 无效选项，请输入 1-6")
                        continue

                elif choice == '6':
                    # Firefox 内核浏览器 - 二级选择
                    print("\n请选择具体的 Firefox 内核浏览器：")
                    print("1. Firefox")
                    print("2. LibreWolf")
                    print("3. Zen Browser（通过自定义路径支持）")
                    print("4. Waterfox（通过自定义路径支持）")

                    firefox_choice = input("\n请输入选项 (1-4): ").strip()
                    firefox_map = {
                        '1': 'firefox',
                        '2': 'librewolf',  # ✅ 有专门的 librewolf() 方法
                        '3': 'zen',        # ✅ 通过自定义路径支持
                        '4': 'waterfox',   # ✅ 通过自定义路径支持
                    }
                    selected = firefox_map.get(firefox_choice)

                    if selected:
                        browsers_to_try = [selected]
                        break
                    else:
                        print("❌ 无效选项，请输入 1-4")
                        continue
                elif choice == '7':
                    # 自动检测所有浏览器 - 包含所有支持的浏览器
                    browsers_to_try = [
                        'chrome', 'brave', 'vivaldi', 'opera', 'chromium', 'edge',
                        'firefox', 'librewolf',
                        'safari'
                    ]
                    print("\n将依次尝试：Chrome, Brave, Vivaldi, Opera, Chromium, Edge, Firefox, LibreWolf, Safari")
                    break
                elif choice == '8':
                    # 手动指定浏览器
                    print("\n✅ 支持的浏览器：")
                    print("   • 直接支持：chrome, firefox, brave, vivaldi, opera, edge, chromium, librewolf, safari")
                    print("   • 通过自定义路径支持：zen, waterfox, arc")
                    custom_browser = input("\n请输入浏览器名称: ").strip().lower()
                    if custom_browser:
                        browsers_to_try = [custom_browser]
                        break
                    else:
                        print("❌ 浏览器名称不能为空")
                else:
                    print("❌ 无效选项，请输入 1-8")
            except (KeyboardInterrupt, EOFError):
                print("\n\n取消导出")
                return False
    else:
        # 自动检测所有浏览器（向后兼容）
        browsers_to_try = ['chrome', 'edge', 'firefox', 'safari']

    # 尝试从选定的浏览器导出
    success = False
    for browser in browsers_to_try:
        print(f"\n尝试从 {browser} 导出...")
        if export_cookies_from_browser(domain, output_file, browser):
            success = True
            break

    if not success:
        print("\n" + "=" * 80)
        print("❌ 导出失败")
        print("\n请手动导出 cookies：")
        print("1. 安装浏览器扩展 'Get cookies.txt LOCALLY'")
        print(f"2. 在浏览器中登录 https://{domain}")
        print("3. 点击扩展图标，导出 cookies")
        print(f"4. 保存到: {output_file}")
        print("=" * 80)
        return False

    # 测试 cookies
    cookies_valid = test_cookies(domain, output_file)

    if cookies_valid:
        print("\n" + "=" * 80)
        print("✅ Cookies 导出成功并已验证！")
        print("=" * 80)

    return cookies_valid


def main():
    """命令行入口"""
    # 从命令行参数获取配置
    domain = None
    output_file = os.path.join(os.getcwd(), 'Cookies.txt')

    if len(sys.argv) > 1:
        domain = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]

    # 如果没有提供域名，export_cookies_interactive 会提示用户输入
    success = export_cookies_interactive(domain, output_file)

    if not success:
        sys.exit(1)

    print("\n下一步：运行 moodle-dl 下载内容")
    print(f"moodle-dl --path {os.path.dirname(output_file) or '.'}")

if __name__ == '__main__':
    main()
