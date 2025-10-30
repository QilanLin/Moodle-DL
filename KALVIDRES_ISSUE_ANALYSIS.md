# Kalvidres 下载问题分析报告

## 问题概述

Kalvidres (Kaltura) 视频文件无法通过 moodle-dl 下载。

## 根本原因

**Autologin API 生成的 cookies 无法用于网页访问认证。**

## 详细分析

### 1. 代码流程

1. moodle-dl 从 Moodle API 获取课程内容（包括 47 个 kalvidres 视频）
2. kalvidres 模块被正确识别为 `cookie_mod` 类型
3. Cookie Handler 使用 `tool_mobile_get_autologin_key` API 和 privatetoken 生成 cookies
4. Cookies 被保存到 `/Users/linqilan/CodingProjects/Cookies.txt`
5. Cookie Handler 测试 cookies 有效性（访问 Moodle 首页）
6. **测试失败**：页面被重定向到 `/login/index.php`，未找到 `login/logout.php` 链接
7. `download_also_with_cookie` 被设置为 `False`
8. 所有 `cookie_mod` 文件（包括 kalvidres）被过滤掉，不进入下载队列

### 2. Cookies 生成与测试

#### 生成的 Cookies
```
keats.kcl.ac.uk  MoodleSession  v1gloqe3f6bgpi3lbpdfjfsmak
keats.kcl.ac.uk  ApplicationGatewayAffinity  65169f0ec4c2dce4b38e1c293bdebbcc
login.microsoftonline.com  (多个 Microsoft SSO 相关 cookies)
```

#### 测试结果
```bash
$ curl -b Cookies.txt "https://keats.kcl.ac.uk/"
# 返回：重定向到 /login/index.php

$ curl -b Cookies.txt "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619"
# 返回：HTTP 303 重定向到 /enrol/index.php?id=134647
# 页面显示："You are currently using guest access"
```

### 3. yt-dlp 测试结果

```bash
$ yt-dlp --cookies Cookies.txt "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619"
# 错误：被重定向到 /enrol/index.php，yt-dlp 无法提取视频
```

### 4. 次要问题：yt-dlp 功能被禁用

在 commit `dcfa9c1` 中，yt-dlp 下载功能被禁用以避免下载"无用的 HTML 页面"：

```python
# moodle_dl/downloader/task.py:530-547
# 已禁用：优先尝试 yt-dlp 下载视频
# if infos.is_html and not self.is_blocked_for_yt_dlp(url_to_download):
#     yt_dlp_processed = await self.download_using_yt_dlp(...)

if infos.is_html:
    raise ValueError('HTML 页面不需要下载，只需创建快捷方式即可访问')
```

**已修复**：在 commit `c461bc7` 中，为 `cookie_mod` 文件重新启用了 yt-dlp。

## 为什么 Cookies 无效？

### 可能的原因

1. **SSO 限制**：KCL Moodle 使用 Microsoft OAuth SSO 认证
   - Autologin cookies 可能只对 Moodle mobile app 有效
   - 网页访问可能需要完整的 OAuth 流程

2. **会话管理**：
   - MoodleSession cookie (expiration=0) 是会话 cookie
   - 可能需要额外的 CSRF tokens 或会话状态

3. **Cookie 域限制**：
   - SSO 涉及多个域（keats.kcl.ac.uk, login.microsoftonline.com）
   - Cookie 可能无法正确处理跨域认证

## 测试数据

### 课程信息
- 课程 ID: 134647
- 课程名: "6CCS3CFL Compilers and Formal Languages(25~26 SEM1 000001)"
- Kalvidres 视频数量: 47 个
- 测试视频 ID: 9159619 (Week 1 Introduction video)

### API 验证
- ✅ token 有效
- ✅ privatetoken 有效
- ✅ core_course_get_contents 返回 kalvidres 模块
- ✅ tool_mobile_get_autologin_key 成功返回 autologin key
- ✅ autologin POST 请求成功，生成 cookies
- ❌ cookies 无法用于网页访问认证

### 数据库状态
```sql
SELECT DISTINCT module_modname FROM files;
-- 结果：calendar, label, quiz, resource, section_summary, url
-- 不包含 kalvidres 或 cookie_mod
```

**结论**：kalvidres 文件从未被添加到数据库，因为在文件过滤阶段就被排除了。

## 解决方案选项

### 选项 1：修复 Cookie 认证 (困难)
- 研究 KCL Moodle SSO 认证流程
- 可能需要模拟完整的浏览器 OAuth 流程
- 需要处理 CSRF tokens 和会话状态

### 选项 2：使用浏览器 Cookies (推荐)
1. 用浏览器登录 KCL Moodle
2. 导出浏览器 cookies (使用 Chrome/Firefox 扩展)
3. 替换 moodle-dl 的 Cookies.txt 文件
4. 运行 moodle-dl 下载

### 选项 3：手动下载视频
1. 用浏览器访问 kalvidres 页面
2. 找到页面中的 Kaltura iframe
3. 提取 Kaltura 视频 URL
4. 使用 yt-dlp 直接下载

### 选项 4：禁用 Cookie 验证 (不推荐)
- 修改 `moodle_service.py` 强制 `download_also_with_cookie=True`
- 风险：可能导致大量下载失败和错误日志

## 相关文件

- `/Users/linqilan/CodingProjects/config.json` - moodle-dl 配置
- `/Users/linqilan/CodingProjects/Cookies.txt` - 生成的 cookies
- `/Users/linqilan/CodingProjects/moodle_state.db` - moodle-dl 数据库
- `/Users/linqilan/CodingProjects/MoodleDL.log` - 完整日志

## 相关提交

- `dcfa9c1` - 禁用 yt-dlp（导致 kalvidres 无法下载）
- `c461bc7` - 为 cookie_mod 重新启用 yt-dlp
- `da799a1` - 移除 yt-dlp 依赖项（但 yt-dlp 仍然安装）

## 下一步行动

1. **立即可行**：使用浏览器导出有效的 cookies
2. **长期方案**：研究并实现 SSO-aware 的 cookie 生成机制

---

报告生成时间: 2025-10-30
moodle-dl 版本: 2.3.12 (自定义修改版)
