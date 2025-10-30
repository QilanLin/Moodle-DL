#!/bin/bash
# 检查 Moodle cookies 状态

COOKIES_FILE="/Users/linqilan/CodingProjects/Cookies.txt"

echo "=================================================="
echo "Moodle Cookies 状态检查"
echo "=================================================="
echo ""

# 检查文件是否存在
if [ ! -f "$COOKIES_FILE" ]; then
    echo "❌ Cookies 文件不存在: $COOKIES_FILE"
    echo ""
    echo "运行以下命令导出 cookies:"
    echo "  cd /Users/linqilan/CodingProjects/Moodle-DL"
    echo "  python3 export_browser_cookies.py"
    exit 1
fi

# 显示文件信息
echo "📁 Cookies 文件信息:"
echo "   路径: $COOKIES_FILE"
echo "   大小: $(ls -lh "$COOKIES_FILE" | awk '{print $5}')"
echo "   修改时间: $(stat -f "%Sm" -t "%Y-%m-%d %H:%M:%S" "$COOKIES_FILE")"
echo ""

# 分析 cookies 内容
echo "🍪 Cookies 内容:"
python3 << 'EOF'
import time
from datetime import datetime

cookies_file = "/Users/linqilan/CodingProjects/Cookies.txt"

cookies_count = 0
moodle_cookies = []
microsoft_cookies = []
expired_count = 0

with open(cookies_file, 'r') as f:
    for line in f:
        if line.startswith('#') or not line.strip():
            continue
        parts = line.strip().split('\t')
        if len(parts) >= 7:
            cookies_count += 1
            domain, flag, path, secure, expires, name, value = parts[:7]

            # 检查过期
            if expires != '0':
                try:
                    exp_timestamp = int(expires)
                    if exp_timestamp < time.time():
                        expired_count += 1
                        continue
                except:
                    pass

            # 分类
            if 'keats.kcl.ac.uk' in domain:
                moodle_cookies.append((name, expires))
            elif 'microsoftonline.com' in domain:
                microsoft_cookies.append((name, expires))

print(f"   总数: {cookies_count} 个")
print(f"   Moodle cookies: {len(moodle_cookies)} 个")
print(f"   Microsoft SSO: {len(microsoft_cookies)} 个")
if expired_count > 0:
    print(f"   ⚠️  已过期: {expired_count} 个")

print()
print("🔑 关键 Cookies:")

# 检查关键 cookies
key_cookies = {
    'MoodleSession': False,
    'ApplicationGatewayAffinity': False,
    'buid': False,
    'fpc': False
}

now = datetime.now()

with open(cookies_file, 'r') as f:
    for line in f:
        if line.startswith('#') or not line.strip():
            continue
        parts = line.strip().split('\t')
        if len(parts) >= 7:
            name, expires = parts[5], parts[4]
            if name in key_cookies:
                key_cookies[name] = True
                if expires == '0':
                    print(f"   ✓ {name:30} Session cookie")
                else:
                    try:
                        exp_timestamp = int(expires)
                        exp_date = datetime.fromtimestamp(exp_timestamp)
                        days_left = (exp_date - now).days
                        if days_left > 7:
                            print(f"   ✓ {name:30} {days_left} 天后过期")
                        elif days_left > 0:
                            print(f"   ⚠️  {name:30} {days_left} 天后过期 (即将过期)")
                        else:
                            print(f"   ❌ {name:30} 已过期")
                    except:
                        print(f"   ? {name:30} 无法解析过期时间")

# 检查缺失的关键 cookies
missing = [k for k, v in key_cookies.items() if not v]
if missing:
    print()
    print(f"   ⚠️  缺失关键 cookies: {', '.join(missing)}")
EOF

echo ""

# 测试 cookies 有效性
echo "🧪 测试 Cookies 有效性:"
if curl -s -b "$COOKIES_FILE" "https://keats.kcl.ac.uk/" --max-time 10 | grep -q "login/logout.php"; then
    echo "   ✅ Cookies 有效 - 已成功认证"
    echo ""
    echo "   你可以运行:"
    echo "     ./run_moodle_dl.sh"
    echo "   或"
    echo "     moodle-dl --path /Users/linqilan/CodingProjects"
else
    echo "   ❌ Cookies 无效或已过期"
    echo ""
    echo "   解决方案:"
    echo "   1. 确保在浏览器中已登录 keats.kcl.ac.uk"
    echo "   2. 运行: python3 export_browser_cookies.py"
    echo "   3. 或使用浏览器扩展手动导出 cookies"
fi

echo ""
echo "=================================================="
