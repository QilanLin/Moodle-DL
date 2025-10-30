#!/bin/bash
# 智能运行 moodle-dl - 自动检测并更新 cookies

set -e

COOKIES_FILE="/Users/linqilan/CodingProjects/Cookies.txt"
MOODLE_URL="https://keats.kcl.ac.uk/"

echo "=================================================="
echo "智能 Moodle-DL 运行脚本"
echo "=================================================="

# 检查 cookies 文件是否存在
if [ ! -f "$COOKIES_FILE" ]; then
    echo "❌ Cookies 文件不存在"
    echo "正在尝试从浏览器导出..."
    python3 export_browser_cookies.py
    if [ $? -ne 0 ]; then
        echo "❌ 自动导出失败，请手动导出 cookies"
        exit 1
    fi
fi

# 测试 cookies 是否有效
echo ""
echo "1️⃣ 检查 cookies 有效性..."
if curl -s -b "$COOKIES_FILE" "$MOODLE_URL" --max-time 10 | grep -q "login/logout.php"; then
    echo "   ✅ Cookies 有效"
else
    echo "   ⚠️  Cookies 可能已过期或无效"
    echo ""
    echo "2️⃣ 尝试从浏览器重新导出 cookies..."

    # 尝试自动重新导出
    if command -v python3 &> /dev/null; then
        python3 export_browser_cookies.py

        # 再次测试
        echo ""
        echo "3️⃣ 验证新导出的 cookies..."
        if curl -s -b "$COOKIES_FILE" "$MOODLE_URL" --max-time 10 | grep -q "login/logout.php"; then
            echo "   ✅ 新 cookies 有效！"
        else
            echo "   ❌ 新 cookies 仍然无效"
            echo ""
            echo "请手动操作："
            echo "1. 在浏览器中访问 $MOODLE_URL"
            echo "2. 确认已登录"
            echo "3. 使用浏览器扩展导出 cookies"
            echo "4. 保存到 $COOKIES_FILE"
            exit 1
        fi
    else
        echo "❌ 未找到 python3，无法自动导出"
        exit 1
    fi
fi

# 运行 moodle-dl
echo ""
echo "=================================================="
echo "🚀 开始运行 moodle-dl"
echo "=================================================="
moodle-dl --path /Users/linqilan/CodingProjects "$@"

echo ""
echo "=================================================="
echo "✅ 完成"
echo "=================================================="
