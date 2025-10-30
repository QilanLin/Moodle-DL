# 使用浏览器 Cookies 下载 Kalvidres 视频

## 问题说明

Kalvidres (Kaltura) 插件**不被 Moodle Mobile API 支持**，因此：
- ❌ moodle-dl 的 autologin cookies 无法访问 kalvidres 页面
- ❌ 即使官方 Moodle mobile app 也显示"unsupported plugin"
- ✅ **只能通过浏览器访问**，需要使用浏览器的 cookies

## 解决方案：导出浏览器 Cookies

### 方法 1：使用浏览器扩展（推荐）

#### Chrome / Edge

1. **安装扩展**：
   - 访问 Chrome Web Store
   - 搜索并安装 "Get cookies.txt LOCALLY"
   - 或者：https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc

2. **导出 Cookies**：
   ```
   1. 登录 https://keats.kcl.ac.uk
   2. 确认可以正常访问 kalvidres 视频页面
   3. 点击浏览器工具栏的扩展图标
   4. 点击 "Export" 或 "Get cookies.txt"
   5. 将导出的内容保存为 Cookies.txt
   ```

3. **替换 moodle-dl 的 Cookies 文件**：
   ```bash
   # 备份现有的 autologin cookies
   mv /Users/linqilan/CodingProjects/Cookies.txt \
      /Users/linqilan/CodingProjects/Cookies.txt.autologin.bak

   # 复制浏览器导出的 cookies
   cp ~/Downloads/cookies.txt /Users/linqilan/CodingProjects/Cookies.txt
   ```

#### Firefox

1. **安装扩展**：
   - 访问 Firefox Add-ons
   - 搜索并安装 "cookies.txt"
   - 或者：https://addons.mozilla.org/firefox/addon/cookies-txt/

2. **导出步骤**同上

### 方法 2：使用命令行工具

```bash
# 安装 browser-cookie3 (Python)
pip install browser-cookie3

# 创建导出脚本
cat > export_cookies.py << 'EOF'
#!/usr/bin/env python3
import browser_cookie3

# 从 Chrome 导出
cj = browser_cookie3.chrome(domain_name='keats.kcl.ac.uk')

# 或从 Firefox 导出
# cj = browser_cookie3.firefox(domain_name='keats.kcl.ac.uk')

# 保存为 Netscape cookie 格式
with open('Cookies.txt', 'w') as f:
    f.write('# Netscape HTTP Cookie File\n')
    f.write('# This file is exported from browser.\n\n')
    for cookie in cj:
        if 'keats.kcl.ac.uk' in cookie.domain or 'microsoftonline.com' in cookie.domain:
            f.write(f'{cookie.domain}\t')
            f.write(f'{"TRUE" if cookie.domain.startswith(".") else "FALSE"}\t')
            f.write(f'{cookie.path}\t')
            f.write(f'{"TRUE" if cookie.secure else "FALSE"}\t')
            f.write(f'{cookie.expires if cookie.expires else 0}\t')
            f.write(f'{cookie.name}\t')
            f.write(f'{cookie.value}\n')
    print(f'✅ Exported {len(list(cj))} cookies')
EOF

python3 export_cookies.py
```

## 使用浏览器 Cookies 运行 moodle-dl

### 已完成的代码修改

moodle-dl 已被修改为**允许使用浏览器 cookies**：
- ✅ 即使 autologin cookies 验证失败，仍会尝试下载 cookie_mod 文件
- ✅ 使用 Cookies.txt 中的 cookies（浏览器导出的）
- ✅ 为 kalvidres 重新启用了 yt-dlp 支持

### 运行步骤

```bash
# 1. 确认 config.json 中启用了 cookie 下载
cat /Users/linqilan/CodingProjects/config.json | grep download_also_with_cookie
# 应该显示：  "download_also_with_cookie": true

# 2. 导出并替换 Cookies.txt（按照上面的方法）

# 3. 验证 cookies 有效性
curl -s -b /Users/linqilan/CodingProjects/Cookies.txt \
  "https://keats.kcl.ac.uk/" | grep -i logout
# 应该能找到 "logout" 链接

# 4. 运行 moodle-dl
moodle-dl --path /Users/linqilan/CodingProjects

# 你会看到警告信息：
# WARNING: Autologin cookies failed validation, but download_also_with_cookie is enabled.
#          Will attempt to use cookies from Cookies.txt file (e.g., browser-exported cookies).

# 5. moodle-dl 会开始下载 kalvidres 视频！
```

## 手动下载单个视频（测试）

如果只想测试下载一个视频：

```bash
# 使用浏览器 cookies 和 yt-dlp
yt-dlp --cookies /Users/linqilan/CodingProjects/Cookies.txt \
  "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619" \
  -o "test-video.%(ext)s" \
  --verbose
```

## Cookie 有效期

浏览器 cookies 通常有效期为：
- Session cookies：浏览器关闭后失效
- Persistent cookies：几小时到几周

**建议**：
1. 保持浏览器登录状态
2. 定期（每周）重新导出 cookies
3. 如果 moodle-dl 下载失败，首先重新导出 cookies

## 故障排查

### yt-dlp 仍然失败

```bash
# 检查 cookies 是否有效
curl -b Cookies.txt "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619"

# 应该返回包含 kaltura-player-iframe 的 HTML
# 如果返回登录页面，说明 cookies 已过期，需要重新导出
```

### moodle-dl 没有尝试下载 kalvidres

```bash
# 检查数据库，看 kalvidres 文件是否被添加
sqlite3 /Users/linqilan/CodingProjects/moodle_state.db \
  "SELECT module_id, module_name FROM files WHERE module_modname LIKE '%cookie%' LIMIT 5;"

# 如果为空，删除数据库重新运行
rm /Users/linqilan/CodingProjects/moodle_state.db
moodle-dl --path /Users/linqilan/CodingProjects
```

## 为什么需要这样做？

### 技术原因

1. **Kalvidres 不支持 Mobile API**：
   - moodle-dl 使用 Moodle Mobile Web Services API
   - Kalvidres 插件只在网页版 Moodle 中工作
   - 官方 Moodle mobile app 也无法访问 kalvidres

2. **Autologin Cookies 的限制**：
   - `tool_mobile_get_autologin_key` API 生成的 cookies 仅用于 mobile app
   - 这些 cookies 无法用于访问网页版 Moodle
   - KCL 使用 Microsoft SSO，需要完整的 OAuth 流程

3. **浏览器 Cookies 可以工作**：
   - 浏览器 cookies 包含完整的 SSO 认证信息
   - 可以访问所有网页版功能，包括 kalvidres
   - yt-dlp 可以使用这些 cookies 提取视频

## 未来改进

可能的改进方向：
1. 自动从浏览器读取 cookies（使用 browser-cookie3）
2. 实现网页版认证流程（复杂，需要处理 OAuth）
3. 提供 GUI 工具导出/导入 cookies

---

**总结**：由于 kalvidres 不支持 mobile API，必须使用浏览器 cookies。按照本指南导出并使用浏览器 cookies，即可下载所有 47 个 kalvidres 视频！
