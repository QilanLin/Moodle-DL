# Cookies.txt 文本文件弃用说明

## 概述

从本版本开始，moodle-dl 不再自动生成 `Cookies.txt` 文本文件。所有 cookies 现在由新的 `AuthSessionManager` 统一管理，存储在 SQLite 数据库中。

## 背景

### 之前的设计
- 使用 Netscape 格式的 `Cookies.txt` 文本文件
- 每次发送请求时，自动保存 cookies 到文件（在 `request_helper.py` 中）
- 代码分散，多个模块需要手动处理 cookies 文件

### 新的设计
- 使用 SQLite 数据库存储 cookies（表：`auth_sessions`, `cookie_store`）
- 由单一的 `AuthSessionManager` 统一管理
- 自动版本控制和审计日志
- 更安全、更高效

## 变更详情

### CookieHandler 修改
文件：`moodle_dl/moodle/cookie_handler.py`

```python
# 之前
self.cookies_path = PT.get_cookies_path(config.get_misc_files_path())

# 现在
self.cookies_path = None  # 不再生成 Cookies.txt
```

**理由：** 
- `request_helper.py` 中的 `post_URL()` 和 `get_URL()` 方法会自动保存 cookies 到文件
- 由于 `self.cookies_path = None`，这些方法不会再执行保存操作
- Cookies 改由 `AuthSessionManager` 通过数据库管理

### 核心工作流

1. **Cookies 来源**
   - SSO 登录时：由 Playwright 获取
   - 存储位置：数据库 `cookie_store` 表
   - 由 `AuthSessionManager` 管理版本

2. **使用 Cookies**
   - 构建 API 请求时，从数据库读取
   - 转换为 Playwright 格式（如需）
   - 再转换为 Netscape 格式（如需，用于 yt-dlp）

3. **Cookies 刷新**
   - 自动：session 过期时
   - 手动：用户运行 `--refresh-cookies` 命令
   - 审计：所有操作记录在 `auth_audit_log` 表

## 向后兼容性

### 现有的 Cookies.txt 会怎样？

如果你之前已经有 `Cookies.txt` 文件，可以：

1. **自动迁移**（推荐）
   ```bash
   moodle-dl --migrate-cookies
   ```
   将现有 cookies 导入数据库

2. **手动迁移**
   - 删除 `Cookies.txt`
   - 运行 `moodle-dl --init --sso` 重新导出

3. **保留不动**
   - 如果不运行任何认证相关命令，`Cookies.txt` 会保留在文件系统中
   - 但程序不会再维护或更新它

### 如果我需要 Cookies.txt？

如果其他工具或脚本需要 `Cookies.txt` 文件，可以手动导出：

```python
from moodle_dl.auth_session_manager import AuthSessionManager

# 获取 cookies
auth_manager = AuthSessionManager(db_file)
cookies = auth_manager.get_session_cookies(session_id)

# 保存为 Netscape 格式
from moodle_dl.export_browser_cookies import save_playwright_cookies_to_netscape
save_playwright_cookies_to_netscape(cookies, 'Cookies.txt')
```

## 数据存储对比

### 文本文件方式（已弃用）
```
Cookies.txt
├─ 保存位置: ~/moodle-dl/misc/Cookies.txt
├─ 格式: Netscape
├─ 版本控制: 无
├─ 审计日志: 无
└─ 问题: 可能被意外删除或修改
```

### 数据库方式（现在）
```
SQLite Database (moodle_state.db)
├─ 表: auth_sessions
│  ├─ session_id
│  ├─ moodle_domain
│  ├─ user_id
│  ├─ created_at
│  ├─ expires_at
│  └─ is_active
├─ 表: cookie_store
│  ├─ cookie_id
│  ├─ session_id
│  ├─ name, value, domain, path
│  ├─ secure, httpOnly, sameSite
│  └─ expires
└─ 表: auth_audit_log
   ├─ 所有认证操作记录
   ├─ 时间戳
   └─ 操作详情
```

## 常见问题

### Q: 为什么不再生成 Cookies.txt？
A: 数据库提供了更好的：
- **安全性**：数据完整性检查，类型安全
- **可维护性**：版本控制，审计日志
- **性能**：避免频繁的磁盘 I/O

### Q: 如何导出 cookies？
A: 使用 `AuthSessionManager` 或 `CookieManager` 的 API：
```python
cookies = auth_manager.get_session_cookies(session_id)
```

### Q: 其他工具如何访问 cookies？
A: 
1. 直接查询 SQLite 数据库
2. 使用导出 API 生成 Netscape 格式文件
3. 使用 Playwright 格式 API

### Q: 升级后需要重新配置吗？
A: 不需要。只需运行一次迁移：
```bash
moodle-dl --migrate-cookies
```
或者删除旧配置重新初始化。

## 相关文件

- `AuthSessionManager`: `moodle_dl/auth_session_manager.py`
- `CookieManager`: `moodle_dl/cookie_manager.py`
- `CookieHandler`: `moodle_dl/moodle/cookie_handler.py`
- `RequestHelper`: `moodle_dl/moodle/request_helper.py`

## 迁移时间表

- **v2.3.x 及以后**: Cookies.txt 完全弃用
- **v2.4.0**: 默认不生成 Cookies.txt
- **v3.0.0**: 移除所有相关代码

---

**最后更新**: 2025-11-19

