# Moodle Cookies 生命周期和管理指南

## TL;DR (快速总结)

**Cookie 寿命：3-4 周（非常稳定）** ✅

- 🔋 Microsoft SSO cookies: **29+ 天**
- 🔋 MoodleSession: **1-2 周**（有活动时自动续期）
- 📅 推荐更新频率：**每月一次**
- 🚀 一次设置，长期使用！

---

## Cookie 寿命详细分析

### 你的 Cookies 当前状态

运行检查脚本可以查看：
```bash
cd /Users/linqilan/CodingProjects/Moodle-DL
./check_cookies.sh
```

### Cookie 类型和寿命

#### 1. Microsoft SSO Cookies (长期有效)

```
buid (Microsoft ID)   → 29 天后过期
fpc (First Party Cookie) → 29 天后过期
```

**特点：**
- ✅ **非常稳定**，有明确的 29 天过期时间
- ✅ 处理 Microsoft OAuth 认证
- ✅ 除非显式登出，否则一直有效
- ✅ 定期自动续期（每次登录时）

#### 2. Moodle Session Cookies (中期有效)

```
MoodleSession         → Session cookie (1-2周实际寿命)
ApplicationGatewayAffinity → Session cookie
```

**特点：**
- ⚡ 标记为 "session cookie"（expires=0）
- ⚡ 实际上 Moodle 会保持会话 1-2 周
- ⚡ 有活动时自动续期
- ⚡ 浏览器关闭后通常仍然有效（现代浏览器行为）

---

## Cookie 何时会失效？

### ❌ 会导致 Cookies 失效的操作

| 操作 | 影响 | 恢复时间 |
|------|------|---------|
| 🚪 **显式登出** | 立即失效 | 重新登录并导出 |
| 🕐 **2周无活动** | MoodleSession 过期 | 重新导出 |
| 🗑️ **清除浏览器 cookies** | 立即失效 | 重新登录并导出 |
| 🔑 **修改密码** | 所有会话失效 | 重新登录并导出 |
| ⏰ **29天后** | Microsoft cookies 过期 | 重新导出 |

### ✅ 不会导致 Cookies 失效的操作

| 操作 | 说明 |
|------|------|
| 🔄 **关闭浏览器** | Session cookies 通常保留 |
| ♻️ **重启电脑** | Cookies 保存在磁盘 |
| 📅 **每周使用 Moodle** | 自动续期会话 |
| 🤖 **运行 moodle-dl** | 不影响浏览器 cookies |
| 🌐 **访问其他网站** | 各网站 cookies 独立 |

---

## 实际使用场景和寿命

### 场景 1：活跃用户（推荐） ⭐

**使用模式：**
- 每周至少访问一次 keats.kcl.ac.uk（浏览器）
- 定期运行 moodle-dl

**Cookie 寿命：6-8 周**

原因：
- Moodle 检测到活动，自动续期会话
- Microsoft SSO cookies 每次访问时刷新
- 最佳体验，无需频繁重新导出

### 场景 2：偶尔使用

**使用模式：**
- 不经常访问 Moodle 网页
- 只用 moodle-dl 下载

**Cookie 寿命：1-2 周**

原因：
- MoodleSession 会因无活动而过期
- Microsoft cookies 仍有效，但 Moodle 会话失效

**建议：**每 1-2 周重新导出一次

### 场景 3：完全离线使用

**使用模式：**
- 从不访问浏览器版 Moodle
- 仅依赖导出的 cookies

**Cookie 寿命：1-2 周**

**建议：**
- 每月初导出一次 cookies
- 或者在 moodle-dl 失败时重新导出

---

## 自动化解决方案

### 方案 1：智能运行脚本（推荐）

```bash
# 使用智能脚本，自动检测和更新 cookies
./run_moodle_dl.sh
```

**功能：**
1. ✅ 自动检测 cookies 是否有效
2. ✅ 失效时自动从浏览器重新导出
3. ✅ 验证新 cookies 有效性
4. ✅ 自动运行 moodle-dl

### 方案 2：定期检查 (Cron Job)

```bash
# 编辑 crontab
crontab -e

# 添加每周日晚上 10 点运行
0 22 * * 0 cd /Users/linqilan/CodingProjects/Moodle-DL && ./run_moodle_dl.sh >> /tmp/moodle-dl.log 2>&1
```

### 方案 3：手动检查

```bash
# 运行前检查 cookies 状态
cd /Users/linqilan/CodingProjects/Moodle-DL
./check_cookies.sh

# 如果有效，直接运行
moodle-dl --path /Users/linqilan/CodingProjects

# 如果无效，重新导出
python3 export_browser_cookies.py
```

---

## Cookie 寿命最大化技巧

### 技巧 1：保持浏览器会话活跃 ⭐⭐⭐

```
每周访问一次 keats.kcl.ac.uk
→ 自动续期 MoodleSession
→ Cookies 可用 6-8 周
```

### 技巧 2：不要登出

```
访问后直接关闭标签页（不点登出）
→ Session cookies 保留
→ 下次自动恢复会话
```

### 技巧 3：使用智能脚本

```bash
# 总是用智能脚本运行
./run_moodle_dl.sh

# 脚本会自动处理 cookie 失效
```

### 技巧 4：备份有效的 Cookies

```bash
# 导出成功后备份
cp Cookies.txt Cookies.txt.backup.$(date +%Y%m%d)

# 失效时可以尝试恢复最近的备份
```

---

## 故障排查

### 问题 1：Cookies 刚导出就失效

**可能原因：**
- 浏览器中实际未登录（显示登录界面）
- 使用了匿名/隐私浏览模式
- 浏览器扩展导出了错误的 cookies

**解决方案：**
```bash
# 1. 在浏览器中重新登录
# 2. 确认可以访问课程页面
# 3. 重新导出 cookies
python3 export_browser_cookies.py

# 4. 立即验证
./check_cookies.sh
```

### 问题 2：部分视频能下载，部分失败

**可能原因：**
- Cookies 部分过期（混合状态）

**解决方案：**
```bash
# 完全重新导出
rm Cookies.txt
python3 export_browser_cookies.py
```

### 问题 3：一周前还能用，现在不行了

**正常现象：**
- MoodleSession 过期了

**解决方案：**
```bash
# 重新导出即可（30秒完成）
python3 export_browser_cookies.py
```

---

## 推荐工作流程

### 初始设置（一次性）

```bash
cd /Users/linqilan/CodingProjects/Moodle-DL

# 1. 在浏览器中登录 keats.kcl.ac.uk
# 2. 导出 cookies
python3 export_browser_cookies.py

# 3. 验证
./check_cookies.sh
```

### 日常使用

```bash
# 每周运行（推荐周末）
./run_moodle_dl.sh
```

**就这么简单！** 智能脚本会处理一切。

### 月度维护

```bash
# 每月初重新导出 cookies（可选，建议操作）
python3 export_browser_cookies.py
```

---

## 总结

### 核心要点

1. **Cookies 很稳定**：通常 3-4 周有效 ✅
2. **不需要频繁更新**：每月一次足够 ✅
3. **使用智能脚本**：自动处理失效 ✅
4. **保持浏览器会话**：最大化寿命 ✅

### 更新频率建议

| 使用频率 | Cookie 更新频率 | 说明 |
|---------|----------------|------|
| 🔥 **每日** | 1个月一次 | Cookies 会自动续期 |
| 📅 **每周** | 2-3周一次 | 推荐，平衡便利性 |
| 📆 **偶尔** | 每次使用前 | 运行 `./check_cookies.sh` 检查 |

### 快速命令参考

```bash
# 检查 cookies 状态
./check_cookies.sh

# 智能运行（自动处理一切）
./run_moodle_dl.sh

# 手动更新 cookies
python3 export_browser_cookies.py

# 标准运行
moodle-dl --path /Users/linqilan/CodingProjects
```

---

**结论：Cookie 寿命长达 3-4 周，非常稳定！一次设置，长期使用。** 🎉
