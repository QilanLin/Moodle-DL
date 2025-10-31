# Moodle-DL 认证完整指南

## 认证方式概览

moodle-dl 使用两种认证机制：
1. **Token**（私有令牌）- 用于 Moodle API 访问
2. **Cookies**（浏览器 Cookies）- 用于网页内容和文件下载

---

## Token vs Cookies

### Token（私有令牌）

**用途**：
- 访问 Moodle Web Service API
- 获取课程列表、作业、论坛等结构化数据
- 生成 MoodleSession cookie（通过 `tool_mobile_get_autologin_key` API）

**获取方式**：
```bash
# 普通 Moodle 登录
moodle-dl --new-token

# SSO 登录（如 Microsoft 账户）
moodle-dl --new-token --sso
```

**有效期**：长期有效（除非手动撤销）

**过期处理**：
- 如果 Token 过期，运行 `moodle-dl --new-token` 重新获取
- moodle-dl 会检测无效的 Token 并提示你更新

**存储位置**：
- macOS/Linux: `~/.moodle-dl/config.json`
- Windows: `C:\Users\你的用户名\.moodle-dl\config.json`

---

### Cookies（浏览器 Cookies）

**用途**：
- 访问需要浏览器认证的内容
- 下载嵌入在描述中的文件链接
- 访问 Moodle 插件内容（如 kalvidres 视频）
- 下载需要 Microsoft SSO 认证的资源

**获取方式**：
```bash
# 初始化设置时选择导出 cookies
moodle-dl --init --sso

# 或单独运行导出脚本
python3 export_browser_cookies.py
```

**包含的 Cookies**：

| Cookie 名称 | 来源 | 有效期 | 用途 |
|------------|------|--------|------|
| **MoodleSession** | Token 自动生成 | ~2 周 | Moodle 页面访问 |
| **MOODLEID1_** | 浏览器 | 会话 | Moodle 用户标识 |
| **buid** | 浏览器（必需） | ~29 天 | Microsoft SSO 用户标识 |
| **fpc** | 浏览器（必需） | ~29 天 | Microsoft First Party Cookie |

**关键差异**：
- `MoodleSession` 可以用 Token 自动生成（通过 autologin API）
- `buid` 和 `fpc` **无法自动生成**，必须从浏览器导出

**存储位置**：
- macOS/Linux: `~/.moodle-dl/Cookies.txt`
- Windows: `C:\Users\你的用户名\.moodle-dl\Cookies.txt`

---

## 认证流程详解

### 场景 1：普通 Moodle 登录（无 SSO）

```
1. 用户运行 moodle-dl --init
   ↓
2. 系统引导获取 Token（访问 Moodle 生成 Token）
   ↓
3. 用户选择是否下载需要 cookie 的文件
   - 如果选择 No → 只使用 Token（只能下载 API 可访问的文件）
   - 如果选择 Yes → 从浏览器导出 Cookies
   ↓
4. moodle-dl 使用 Token 访问 API，使用 Cookies 下载文件
   ↓
5. MoodleSession 过期后，使用 Token 自动重新生成
```

**需要 Cookies 的场景**：
- 下载描述中嵌入的外部链接
- 访问某些 Moodle 插件内容
- 下载不在 API 中暴露的文件

---

### 场景 2：SSO 登录（如 Microsoft 账户）

```
1. 用户运行 moodle-dl --init --sso
   ↓
2. 系统引导获取 Token（通过 Microsoft SSO 登录）
   ↓
3. 用户必须从浏览器导出 Cookies
   → 需要 buid 和 fpc（Microsoft SSO cookies）
   ↓
4. moodle-dl 使用 Token 访问 API
   使用 Cookies（包含 buid/fpc）下载需要 SSO 认证的文件
   ↓
5. MoodleSession 自动刷新
   buid/fpc 过期后需要重新从浏览器导出
```

**为什么 SSO 必须导出浏览器 Cookies**：
- Microsoft SSO 的 `buid` 和 `fpc` cookies 无法通过 API 生成
- 这些 cookies 是 Microsoft 认证流程的一部分
- 只能通过浏览器完整登录流程获得

---

## 无 Cookies 的行为

### 如果不导出 Cookies 会怎样？

#### ✅ 仍然可以工作的功能

1. **API 可访问的内容**：
   - 课程列表
   - 作业、测验、论坛等模块
   - 直接上传到 Moodle 的文件
   - 通过 Moodle API 暴露的所有资源

2. **基本下载功能**：
   - 课程文件（如 PDF, PPT, 视频等）
   - 作业附件
   - 资源模块的文件

**示例**：
```bash
# 不使用 cookies
moodle-dl --init
# 选择 "No" 当询问是否下载需要 cookie 的文件

# 结果：
✅ 可以下载课程文件、作业等
✅ 可以获取课程结构
```

---

#### ❌ 无法工作的功能

1. **描述中的嵌入链接**：
   - 课程描述、作业描述中的 `<a href="...">` 链接
   - 这些链接通常需要 cookie 认证

2. **特定 Moodle 插件**：
   - **kalvidres**（KCL Moodle 使用的视频插件）
   - 某些第三方插件内容

3. **外部嵌入内容**：
   - 嵌入的 Google Drive 文件
   - 嵌入的 OneDrive 文件（需要 Microsoft SSO cookies）

4. **SSO 保护的资源**：
   - 任何需要 Microsoft SSO 认证的文件/页面

**示例**：
```bash
# 如果某个课程的作业描述包含：
<a href="https://keats.kcl.ac.uk/mod/resource/view.php?id=12345">下载注解文档</a>

# 无 cookies：
❌ 下载失败 (401 Unauthorized)
提示：此链接需要 cookie 认证

# 有 cookies：
✅ 成功下载
```

---

## Cookie 自动刷新机制

### MoodleSession 的自动刷新

moodle-dl 实现了 MoodleSession cookie 的自动刷新：

```python
def check_and_fetch_cookies(privatetoken: str, userid: str) -> bool:
    # 检查 cookies 是否存在且有效
    if os.path.exists(self.cookies_path):
        if self.test_cookies():
            logging.debug('Cookies are still valid')
            return True

        logging.info('Moodle cookie has expired, an attempt is made to generate a new cookie.')

    # 使用 Token 调用 autologin API
    autologin_key = self.fetch_autologin_key(privatetoken)

    # 生成新的 MoodleSession
    post_data = {'key': autologin_key.get('key', ''), 'userid': userid}
    url = autologin_key.get('autologinurl', '')
    self.client.post_URL(url, post_data, self.cookies_path)

    return self.test_cookies()
```

**特点**：
- ✅ 完全自动，无需用户干预
- ✅ 只要 Token 有效，MoodleSession 可以无限刷新
- ✅ 刷新后的 MoodleSession 有效期约 2 周

**日志示例**：
```
INFO: Downloading autologin key
INFO: Downloading cookies
DEBUG: Cookies are still valid
```

---

### buid/fpc 的手动更新

Microsoft SSO cookies（`buid` 和 `fpc`）**无法自动刷新**：

**原因**：
- 这些 cookies 由 Microsoft 认证服务器生成
- 无法通过 Moodle API 获取
- 必须通过完整的浏览器 SSO 登录流程

**有效期**：约 29 天

**过期后的症状**：
```
ERROR: 下载失败 (401 Unauthorized)
ERROR: 可能原因：Cookies 已过期
```

**解决方案**：
```bash
# 重新从浏览器导出 cookies
moodle-dl --init --sso
# 或
python3 export_browser_cookies.py
```

---

## 日志级别

### 日志级别说明

moodle-dl 支持两种日志级别：

| 级别 | 启用方式 | 输出内容 |
|------|---------|---------|
| **INFO** | 默认 | 基本操作信息 |
| **DEBUG** | `--verbose` | 详细调试信息 |

### INFO 级别（默认）

**显示内容**：
- 课程名称
- 文件名称
- 下载进度
- 错误信息

**示例**：
```
INFO: 正在下载课程：Compilers and Formal Languages
INFO: 下载文件：Lecture_1_Introduction.pdf
INFO: ✅ 下载完成
```

**特点**：
- ✅ 显示文件名
- ❌ 不显示 URL
- ❌ 不显示 API 请求详情

---

### DEBUG 级别（--verbose）

**启用方式**：
```bash
moodle-dl --verbose
```

**额外显示内容**：
- 完整的 URL
- API 请求和响应
- Cookie 使用详情
- 文件路径
- 认证流程细节

**示例**：
```
DEBUG: API Request: https://keats.kcl.ac.uk/webservice/rest/server.php
DEBUG: Parameters: wsfunction=core_course_get_contents, courseid=12345
DEBUG: 使用 cookies: MoodleSession=abc..., buid=xyz...
DEBUG: 下载 URL: https://keats.kcl.ac.uk/mod/resource/view.php?id=67890
DEBUG: 保存到: /path/to/file.pdf
INFO: ✅ 下载完成
```

**特点**：
- ✅ 显示所有技术细节
- ✅ 有助于调试问题
- ⚠️ 输出量大

---

### 日志输出

#### 1. 控制台输出

**总是启用**：日志会输出到终端

**级别控制**：
```bash
# INFO 级别（默认）
moodle-dl

# DEBUG 级别
moodle-dl --verbose
```

---

#### 2. 文件日志

**启用方式**：
```bash
# 初始化时选择 "Enable logging"
moodle-dl --init

# 或在配置中启用
# config.json: "log_to_file": true
```

**日志文件位置**：
- 工作目录: `MoodleDL.log`
- 示例: `/Users/username/MoodleDL.log`

**级别控制**：
```bash
# 文件日志级别与控制台相同

# INFO 级别
moodle-dl --log-to-file

# DEBUG 级别
moodle-dl --log-to-file --verbose
```

**特点**：
- ✅ 文件日志和控制台日志使用相同的级别
- ✅ 如果启用 `--verbose`，文件也会记录 DEBUG 信息
- ✅ 日志文件会追加（不会覆盖）

---

## 认证问题排查

### 问题 1：Token 无效

**症状**：
```
ERROR: Invalid token
ERROR: 请运行 moodle-dl --new-token 重新获取
```

**原因**：
- Token 已过期
- Token 被手动撤销
- Moodle 服务器设置变更

**解决**：
```bash
moodle-dl --new-token --sso
```

---

### 问题 2：Cookies 无效

**症状**：
```
ERROR: 下载失败 (401 Unauthorized)
WARNING: Cookies 可能已过期
```

**原因**：
- MoodleSession 过期（自动刷新失败）
- buid/fpc 过期（SSO cookies）
- Cookies.txt 文件损坏

**解决**：
```bash
# 1. 检查 Token 是否有效
moodle-dl --new-token --sso

# 2. 重新导出浏览器 cookies
python3 export_browser_cookies.py

# 3. 或重新初始化
moodle-dl --init --sso
```

---

### 问题 3：部分文件下载失败

**症状**：
```
✅ 下载成功：Lecture_1.pdf
❌ 下载失败：embedded_resource.pdf (401)
```

**原因**：
- 嵌入资源需要 cookies
- 但当前 cookies 已过期或缺失

**解决**：
```bash
# 确保已导出浏览器 cookies
python3 export_browser_cookies.py

# 确保包含 Microsoft SSO cookies (buid, fpc)
cat ~/.moodle-dl/Cookies.txt | grep -E "buid|fpc"
```

---

### 问题 4：SSO 登录失败

**症状**：
```
ERROR: SSO authentication failed
ERROR: 请在浏览器中登录并导出 cookies
```

**原因**：
- 浏览器中没有 Microsoft SSO cookies
- buid/fpc cookies 已过期

**解决**：
```bash
# 1. 在浏览器中登录 Moodle
# 访问: https://keats.kcl.ac.uk
# 通过 Microsoft 账户登录

# 2. 确保勾选 "保持登录"

# 3. 导出 cookies
python3 export_browser_cookies.py

# 4. 验证 cookies 包含 buid 和 fpc
cat ~/.moodle-dl/Cookies.txt | grep microsoftonline.com
```

---

## 最佳实践

### ✅ 推荐做法

1. **首次设置**：
   ```bash
   moodle-dl --init --sso
   # 选择 "Yes" 导出 cookies
   # 选择你的主要浏览器
   ```

2. **定期维护**：
   - Token：很少需要更新（除非过期）
   - MoodleSession：自动刷新，无需关心
   - buid/fpc：每月检查一次，过期后重新导出

3. **调试问题时**：
   ```bash
   moodle-dl --verbose --log-to-file
   # 查看详细日志
   cat MoodleDL.log
   ```

4. **多设备使用**：
   - 每个设备单独获取 Token 和 Cookies
   - 不要复制配置文件（可能失效）

---

### ❌ 避免的做法

1. ❌ 分享 Token 或 Cookies（包含敏感信息）
2. ❌ 手动编辑 `config.json` 或 `Cookies.txt`
3. ❌ 在公共代码仓库中提交这些文件
4. ❌ 使用过期的 cookies（会导致下载失败）

---

## 安全建议

### 敏感文件保护

**需要保护的文件**：
1. `~/.moodle-dl/config.json` (包含 Token)
2. `~/.moodle-dl/Cookies.txt` (包含会话 cookies)
3. `~/.moodle-dl/Cookies.txt.backup` (备份文件)

**保护措施**：
```bash
# 1. 设置文件权限（仅自己可读）
chmod 600 ~/.moodle-dl/config.json
chmod 600 ~/.moodle-dl/Cookies.txt

# 2. 添加到 .gitignore
echo "config.json" >> .gitignore
echo "Cookies.txt" >> .gitignore
echo "*.backup" >> .gitignore

# 3. 不要上传到公共位置
# ❌ 不要提交到 GitHub
# ❌ 不要上传到 Google Drive/OneDrive
# ❌ 不要通过邮件发送
```

---

### Token 撤销

**如果 Token 泄露**：

1. 登录 Moodle
2. 访问：用户设置 → 安全密钥
3. 撤销对应的 Token
4. 运行 `moodle-dl --new-token --sso` 生成新 Token

---

## 总结

### 认证方式选择

| 需求 | 推荐方式 | 命令 |
|------|---------|------|
| 只下载 API 可访问的内容 | Token 即可 | `moodle-dl --init` |
| 下载所有内容（包括嵌入链接） | Token + Cookies | `moodle-dl --init --sso` |
| SSO 登录（Microsoft 账户） | Token + Cookies（必需） | `moodle-dl --init --sso` |

---

### 关键要点

1. ✅ Token 用于 API，Cookies 用于网页/文件下载
2. ✅ MoodleSession 可以自动刷新，buid/fpc 需要手动更新
3. ✅ SSO 登录必须导出浏览器 cookies
4. ✅ 使用 `--verbose` 查看详细日志辅助调试
5. ✅ 保护好 Token 和 Cookies 文件（包含敏感信息）

**完整的文档请参考**：
- [浏览器 Cookie 导出指南](BROWSER_COOKIE_EXPORT.md)
- [Moodle API 限制](MOODLE_API_LIMITATIONS.md)
