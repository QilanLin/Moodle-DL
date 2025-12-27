# 刷新 Cookies 功能使用说明

## 功能说明

`--refresh-cookies` 命令允许你只刷新浏览器 cookies，而**不重置任何文件的下载状态**。

## 使用场景

当你遇到以下情况时，可以使用此功能：

1. **Cookies 已过期**：运行 `moodle-dl` 时提示 cookies 过期或需要重新登录
2. **下载失败需要重试**：有一些文件下载失败了，隔了几天后想重试，但 cookies 已失效
3. **只想更新认证信息**：不想重新下载所有文件，只想更新 cookies

## 使用方法

### 基本用法

```bash
moodle-dl --refresh-cookies
```

### 完整流程

1. **刷新 cookies**：
   ```bash
   moodle-dl --refresh-cookies
   ```
   
   程序会：
   - 显示你的 Moodle 域名和 cookies 保存路径
   - 让你选择浏览器（Chrome、Firefox、Edge、Safari 等）
   - 自动从浏览器导出最新的 cookies
   - 更新数据库中的 cookies（不影响文件下载状态）

2. **重试失败的下载**（可选）：
   ```bash
   moodle-dl --retry-failed
   ```
   
   使用刷新后的 cookies 重新下载之前失败的文件

## 示例输出

```
================================================================================
🔄 刷新浏览器 Cookies（不影响文件下载状态）
================================================================================

📍 Moodle 域名: keats.kcl.ac.uk
📁 Cookies 保存路径: /path/to/Cookies.txt

请选择要导出 cookies 的浏览器：
💡 上次使用的浏览器: Firefox
  Chrome
→ Firefox
  Edge
  Safari
  自动检测

正在从浏览器导出 cookies...

✅ Cookies 刷新成功！

💡 下一步：
   如果有下载失败的文件，可以运行：
   moodle-dl --retry-failed
```

## 与其他命令的区别

| 命令 | 功能 | 是否重置下载状态 |
|------|------|-----------------|
| `--refresh-cookies` | 只刷新 cookies | ❌ 否 |
| `--retry-failed` | 重试失败的下载 | ⚠️ 只重置失败文件的状态 |
| `--reset-downloaded-files` | 重置所有文件状态 | ✅ 是（重置全部） |
| `--init` | 重新初始化配置 | ✅ 是（全新开始） |

## 技术细节

- **数据库不受影响**：只更新 cookies，不修改文件下载记录
- **浏览器选择会被记住**：下次自动刷新时会使用相同的浏览器
- **支持自动检测**：如果不确定用哪个浏览器，选择"自动检测"
- **无需手动操作**：不需要手动打开开发者工具或复制 cookies

## 故障排查

### 问题：提示找不到 export_browser_cookies.py

**解决方案**：确保 `export_browser_cookies.py` 文件在项目根目录

### 问题：无法导出 cookies

**可能原因**：
1. 浏览器中未登录 Moodle
2. 选择的浏览器不正确
3. 缺少 `browser-cookie3` 库

**解决方案**：
```bash
# 1. 确保在浏览器中已登录 Moodle
# 2. 尝试选择其他浏览器
# 3. 安装依赖
pip install browser-cookie3
```

### 问题：导出成功但仍然提示过期

**解决方案**：
1. 在浏览器中重新登录 Moodle
2. 再次运行 `moodle-dl --refresh-cookies`
3. 如果问题持续，尝试清除浏览器缓存后重新登录

## 最佳实践

1. **定期刷新**：如果长时间没有运行 moodle-dl，先刷新 cookies
2. **失败后立即刷新**：如果看到很多下载失败，先刷新 cookies 再重试
3. **SSO 用户**：如果使用 SSO 登录，确保浏览器中的 SSO 会话仍然有效

## 相关命令

- `moodle-dl --help`：查看所有可用命令
- `moodle-dl --retry-failed`：重试失败的下载
- `moodle-dl --manage-database`：管理数据库
- `python3 export_browser_cookies.py`：手动导出 cookies

