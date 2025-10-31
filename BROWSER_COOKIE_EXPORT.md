# 浏览器 Cookie 导出完整指南

## 快速开始

在使用 `moodle-dl --init --sso` 设置时，如果选择下载需要 cookie 的文件，系统会自动引导你导出浏览器 cookies。

### 基本流程

```bash
moodle-dl --init --sso

# 设置过程中会询问：
你想要下载需要 cookie 的文件吗？ (y/N) y

# 选择你的浏览器：
请选择你使用的浏览器或内核：
1. Chrome
2. Edge
3. Firefox
4. Safari
5. Chromium 内核浏览器（Brave, Vivaldi, Arc, Opera 等）
6. Firefox 内核浏览器（Zen, Waterfox, LibreWolf 等）
7. 自动检测所有浏览器
```

---

## 支持的浏览器

### ✅ 直接支持（无需额外配置）

| 浏览器 | 选择方式 | 平台 |
|--------|---------|------|
| Chrome | 选择 "1. Chrome" | 全平台 |
| Edge | 选择 "2. Edge" | 全平台 |
| Firefox | 选择 "3. Firefox" | 全平台 |
| Safari | 选择 "4. Safari" | macOS |
| Brave | 选择 "5. Chromium 内核" → "Brave" | 全平台 |
| Vivaldi | 选择 "5. Chromium 内核" → "Vivaldi" | 全平台 |
| Opera | 选择 "5. Chromium 内核" → "Opera" | 全平台 |
| Chromium | 选择 "5. Chromium 内核" → "Chromium" | 全平台 |
| LibreWolf | 选择 "6. Firefox 内核" → "LibreWolf" | 全平台 |

### ✅ 通过自动路径检测支持

| 浏览器 | 选择方式 | 平台 | 说明 |
|--------|---------|------|------|
| **Zen Browser** | 选择 "6. Firefox 内核" → "Zen Browser" | 全平台 | 自动检测 cookie 文件路径 |
| **Waterfox** | 选择 "6. Firefox 内核" → "Waterfox" | 全平台 | 自动检测 cookie 文件路径 |
| **Arc** | 选择 "5. Chromium 内核" → "Arc" | macOS/Windows | 自动检测 cookie 文件路径 |

---

## Cookie 文件位置

### Chromium 系列浏览器

**macOS:**
- Chrome: `~/Library/Application Support/Google/Chrome/Default/Cookies`
- Brave: `~/Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies`
- Arc: `~/Library/Application Support/Arc/User Data/Default/Cookies` (自动检测)

**Linux:**
- Chrome: `~/.config/google-chrome/Default/Cookies`
- Brave: `~/.config/BraveSoftware/Brave-Browser/Default/Cookies`

**Windows:**
- Chrome: `%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cookies`

### Firefox 系列浏览器

**macOS:**
- Firefox: `~/Library/Application Support/Firefox/Profiles/*.default*/cookies.sqlite`
- Zen: `~/Library/Application Support/zen/Profiles/*.default*/cookies.sqlite` (自动检测)

**Linux:**
- Firefox: `~/.mozilla/firefox/*.default*/cookies.sqlite`
- Zen: `~/.zen/Profiles/*.default*/cookies.sqlite` (自动检测)
- LibreWolf: `~/.librewolf/*.default*/cookies.sqlite`

**Windows:**
- Firefox: `%APPDATA%\Mozilla\Firefox\Profiles\*.default*\cookies.sqlite`

---

## 导出的 Cookie 内容

### Moodle 相关 Cookies

| Cookie 名称 | 用途 | 有效期 |
|------------|------|--------|
| **MoodleSession** | Moodle 会话认证 | ~2 周 |
| **MOODLEID1_** | Moodle 用户标识 | 会话 |

### Microsoft SSO Cookies（仅 SSO 登录需要）

| Cookie 名称 | 用途 | 有效期 |
|------------|------|--------|
| **buid** | Microsoft 浏览器用户标识 | ~29 天 |
| **fpc** | First Party Cookie | ~29 天 |
| **ApplicationGatewayAffinity** | Azure 网关亲和性 | 会话 |

**重要说明**：
- `MoodleSession` 会由 moodle-dl 使用 Token 自动生成
- `buid` 和 `fpc` **必须从浏览器导出**（无法自动生成）
- 只导出 `keats.kcl.ac.uk` 和 `microsoftonline.com` 域名的 cookies

---

## 使用示例

### 示例 1：使用 Zen Browser

```
请选择你使用的浏览器或内核：
> 6. Firefox 内核浏览器（Zen, Waterfox, LibreWolf 等）

请选择具体的 Firefox 内核浏览器：
> 3. Zen Browser（通过自定义路径支持）

正在从 zen 导出 cookies...
  正在检测 zen 的 cookie 文件路径...
  ✓ 找到 cookie 文件: ~/.zen/Profiles/abc123.default/cookies.sqlite
✅ 成功导出 18 个 cookies 到: Cookies.txt

关键 Cookies:
  ✓ MoodleSession: abc123...
  ✓ buid: xyz789...
  ✓ fpc: def456...

正在测试 cookies 有效性...
✅ Cookies 有效！已成功认证
```

### 示例 2：使用 Chrome

```
请选择你使用的浏览器或内核：
> 1. Chrome

正在从 chrome 导出 cookies...
✅ 成功导出 18 个 cookies 到: Cookies.txt
✅ Cookies 有效！已成功认证
```

### 示例 3：自动检测所有浏览器

```
请选择你使用的浏览器或内核：
> 7. 自动检测所有浏览器

将依次尝试：Chrome, Brave, Vivaldi, Opera, Chromium, Edge, Firefox, LibreWolf, Safari

尝试从 chrome 导出...
❌ 未找到 keats.kcl.ac.uk 的 cookies

尝试从 firefox 导出...
✅ 成功导出 18 个 cookies
```

---

## 常见问题

### Q1: 导出失败，提示"未找到 cookies"

**可能原因**：
1. ❌ 没有在浏览器中登录 Moodle
2. ❌ 浏览器已关闭且 cookies 未持久化
3. ❌ 选错了浏览器

**解决方案**：
1. 在浏览器中访问 `https://keats.kcl.ac.uk` 并登录
2. 确保勾选了"保持登录"
3. 重新运行导出，确保选择正确的浏览器
4. 如果仍然失败，尝试"自动检测所有浏览器"

### Q2: Cookies 多久会过期？

| Cookie | 有效期 | 过期后操作 |
|--------|--------|-----------|
| MoodleSession | 1-2 周 | 自动使用 Token 重新生成 |
| buid / fpc | ~29 天 | 需要重新从浏览器导出 |

**最佳实践**：
- 如果下载失败并提示认证错误，重新导出浏览器 cookies
- MoodleSession 会自动刷新，无需手动操作

### Q3: 使用 Zen/Waterfox/Arc 浏览器怎么办？

**直接选择对应的浏览器即可**！

系统会自动检测这些浏览器的 cookie 文件位置，无需任何额外配置。

示例：
- Zen Browser → 选择 "6. Firefox 内核" → "Zen Browser"
- Arc → 选择 "5. Chromium 内核" → "Arc"

### Q4: 找不到 cookie 文件怎么办？

**错误信息**：
```
❌ 未找到 zen 的 cookie 文件
   可能的原因：
   1. zen 未安装
   2. zen 的 profile 路径不标准
```

**解决方案**：
1. 确保浏览器已安装并至少运行过一次
2. 检查浏览器是否已登录 Moodle
3. **备选方案**：使用浏览器扩展手动导出

### Q5: 如何手动导出 Cookies？

如果自动导出失败，可以使用浏览器扩展：

**步骤**：
1. 安装扩展 **"Get cookies.txt LOCALLY"**
   - Chrome/Edge/Brave/Arc: [Chrome Web Store](https://chrome.google.com/webstore)
   - Firefox/Zen/Waterfox: [Firefox Add-ons](https://addons.mozilla.org/firefox/)

2. 在浏览器中访问 `https://keats.kcl.ac.uk`（确保已登录）

3. 点击扩展图标 → 选择 "Export" → "Netscape format"

4. 保存文件为 `Cookies.txt`

5. 移动文件到 moodle-dl 工作目录
   - macOS/Linux: `~/.moodle-dl/Cookies.txt`
   - Windows: `C:\Users\你的用户名\.moodle-dl\Cookies.txt`

**注意**：手动导出的 Cookies 过期后需要重新导出。

---

## 技术实现细节

### 自动路径检测原理

对于 Zen/Waterfox/Arc 等浏览器，系统使用以下方法自动检测 cookie 文件：

1. **识别操作系统**：macOS, Linux, Windows
2. **使用 glob 模式匹配**：
   - Firefox 系列: `*/Profiles/*.default*/cookies.sqlite`
   - Chrome 系列: `*/User Data/*/Cookies`
3. **选择最新的 profile**：按文件修改时间排序
4. **传递自定义路径给 browser-cookie3**：
   ```python
   browser_cookie3.firefox(cookie_file='/custom/path/cookies.sqlite')
   browser_cookie3.chrome(cookie_file='/custom/path/Cookies')
   ```

### 支持的特殊情况

- ✅ Linux Flatpak 版 Zen Browser
- ✅ Firefox 的多个 profile
- ✅ 非标准安装路径（通过 glob 模式）
- ✅ Arc 的多个 Space（选择最新的）

---

## Cookie 导出与认证流程

### 完整认证流程

```
1. 用户在浏览器登录 Moodle
   ↓
2. moodle-dl 从浏览器导出 cookies
   → MoodleSession (会话)
   → buid, fpc (Microsoft SSO)
   ↓
3. 使用 Token 访问 Moodle API
   ↓
4. 使用 Cookies 下载需要认证的文件
   (如 kalvidres 视频、嵌入链接等)
   ↓
5. MoodleSession 过期后，使用 Token 自动重新生成
```

### Token 与 Cookies 的区别

| 项目 | Token | Cookies |
|------|-------|---------|
| **用途** | Moodle API 访问 | 网页/文件下载 |
| **获取方式** | `moodle-dl --new-token` | 从浏览器导出 |
| **有效期** | 长期（除非手动撤销） | MoodleSession: 1-2周<br>buid/fpc: ~29天 |
| **自动刷新** | 否（手动获取新 Token） | 是（MoodleSession 使用 Token 自动生成） |
| **SSO 支持** | 需要加 `--sso` | 需要导出 buid/fpc |

**关键要点**：
- Token 用于 API，Cookies 用于网页
- MoodleSession 可以用 Token 自动生成
- buid/fpc **无法自动生成**，必须从浏览器导出

---

## 故障排除

### 问题 1：Cookies 导出成功但下载失败

**症状**：
```
✅ 成功导出 18 个 cookies
✅ Cookies 有效！已成功认证

# 但下载时：
❌ 下载失败: 401 Unauthorized
```

**原因**：Cookies 已过期（特别是 buid/fpc）

**解决**：
```bash
# 重新从浏览器导出 cookies
moodle-dl --init --sso
# 选择 "是" 导出 cookies
```

### 问题 2：使用多个浏览器

**场景**：你在 Chrome 和 Zen 中都登录了 Moodle

**建议**：
- 选择你**最常用**的浏览器
- 或使用"自动检测所有浏览器"（会选择第一个找到 cookies 的）

### 问题 3：Linux 上使用 Flatpak 版 Zen

**Flatpak 路径**：
```
~/.var/app/app.zen_browser.zen/zen/Profiles/*/cookies.sqlite
```

**无需额外配置**：系统会自动检测 Flatpak 路径

---

## 最佳实践

### ✅ 推荐做法

1. **首次设置**：
   - 使用 `moodle-dl --init --sso`
   - 选择你的主要浏览器
   - 确保在浏览器中已登录 Moodle

2. **定期维护**：
   - 每月检查一次 cookies 是否过期
   - 如果下载失败，首先尝试重新导出 cookies

3. **多设备使用**：
   - 每个设备单独导出 cookies
   - 不要复制 Cookies.txt 文件到其他设备（可能失效）

### ❌ 避免的做法

1. ❌ 在没有登录的浏览器中导出 cookies
2. ❌ 混用不同浏览器的 cookies
3. ❌ 手动编辑 Cookies.txt 文件
4. ❌ 分享 Cookies.txt 文件（包含敏感信息）

---

## 安全注意事项

### Cookie 文件的安全性

**Cookies.txt 包含的敏感信息**：
- ✅ 你的 Moodle 会话令牌
- ✅ Microsoft SSO 认证信息
- ✅ 可以用于访问你的 Moodle 账户

**安全建议**：
1. ⚠️ **不要分享** Cookies.txt 文件
2. ⚠️ **不要上传** Cookies.txt 到公共代码仓库
3. ⚠️ 定期删除过期的 cookies 文件
4. ✅ 使用 `.gitignore` 排除 Cookies.txt

**moodle-dl 自动备份**：
- 导出新 cookies 时，旧文件会自动备份为 `Cookies.txt.backup`
- 备份文件也包含敏感信息，注意保护

---

## 总结

### 快速参考

| 浏览器 | 如何选择 |
|--------|---------|
| Chrome, Edge, Firefox, Safari | 直接选择 (1-4) |
| Brave, Vivaldi, Opera, Arc | 选择 "Chromium 内核" (5) |
| Zen, Waterfox, LibreWolf | 选择 "Firefox 内核" (6) |
| 不确定/导出失败 | 选择 "自动检测" (7) |

### 关键要点

1. ✅ 所有主流浏览器都支持自动导出
2. ✅ Zen/Waterfox/Arc 通过自动路径检测支持
3. ✅ MoodleSession 会自动刷新，buid/fpc 需要手动重新导出
4. ✅ 如果自动导出失败，可以使用浏览器扩展手动导出
5. ✅ Cookies.txt 包含敏感信息，注意保护

**完全不用担心浏览器兼容性问题！**
