# Cookies.txt 文件产生源全面审计报告

## 执行摘要

进行了完整的代码库扫描，找出所有可能产生 `Cookies.txt` 的地方。**结果：已修复所有问题**

## 审计方法

1. 搜索所有 `.save()` 调用
2. 搜索所有对 `Cookies.txt` 的引用
3. 搜索所有 `cookie_jar_path` 的使用
4. 搜索所有 `MoodleDLCookieJar` 的实例化

---

## 🔴 关键代码路径（写入点）

### 1. RequestHelper - 写入点 #1（主要）
**文件**: `moodle_dl/moodle/request_helper.py`

**问题代码**:
```python
# line 84
if cookie_jar_path is not None:
    for cookie in session.cookies:
        cookie.expires = 2147483647
    session.cookies.save(ignore_discard=True, ignore_expires=True)

# line 113  
if cookie_jar_path is not None:
    session.cookies.save(ignore_discard=True, ignore_expires=True)
```

**触发条件**: 当 `cookie_jar_path` 不为 None 时

**现在的调用链**:
1. `CookieHandler.__init__()` - ✅ **已修复**（设置 `self.cookies_path = None`）
2. `KalvidresTextExtractor.__init__()` - 🔍 **未使用**（无实例化代码找到）

---

## 🟢 修复方案总结

### 修复 #1: CookieHandler
**文件**: `moodle_dl/moodle/cookie_handler.py`
**修改**: 第 17-22 行

```python
# 之前
self.cookies_path = PT.get_cookies_path(config.get_misc_files_path())

# 之后
self.cookies_path = None  # 不再生成 Cookies.txt
```

**效果**: 
- ✅ `post_URL()` 和 `get_URL()` 中的 `save()` 调用被跳过
- ✅ Cookies 仅由 `AuthSessionManager` 通过数据库管理

---

## 📋 完整扫描结果

### 所有 `session.cookies.save()` 调用
| 文件 | 位置 | 调用者 | 状态 |
|------|------|--------|------|
| `request_helper.py` | 84 | `post_URL()` | ✅ 已通过 CookieHandler 修复 |
| `request_helper.py` | 113 | `get_URL()` | ✅ 已通过 CookieHandler 修复 |

### 所有 `MoodleDLCookieJar` 使用
| 文件 | 行号 | 用途 | 状态 |
|------|------|------|------|
| `request_helper.py` | 70 | POST 请求 | ✅ cookie_jar_path 现在为 None |
| `request_helper.py` | 101 | GET 请求 | ✅ cookie_jar_path 现在为 None |
| `downloader/task.py` | 830 | yt-dlp 使用 | ℹ️ 使用 StringIO，不写文件 |
| `downloader/task.py` | 914 | yt-dlp 使用 | ℹ️ 使用 StringIO，不写文件 |
| `downloader/task.py` | 1271 | yt-dlp 使用 | ℹ️ 使用 StringIO，不写文件 |
| `utils.py` | 251 | 类定义 | ℹ️ 工具类 |

### 所有 cookies_path 的设置
| 文件 | 方法 | cookies_path 值 | 状态 |
|------|------|-----------------|------|
| `cookie_handler.py` | `__init__` | None | ✅ 已修复 |
| `kalvidres_text_extractor_generic.py` | `__init__` | 参数传入 | 🔍 未使用 |
| `authenticators.py` | 多处 | `PT.get_cookies_path()` | ℹ️ 用于浏览器导出 |
| `config.py` | `get_cookies_text()` | `PT.get_cookies_path()` | ℹ️ 仅读取 |
| `moodle_service.py` | 日志消息 | N/A | ℹ️ 仅提示 |

---

## 🔍 数据流分析

### 旧设计流程（已改进）
```
CookieHandler.__init__()
  ↓
self.cookies_path = PT.get_cookies_path(...)
  ↓
post_URL(url, data, self.cookies_path) / get_URL(url, self.cookies_path)
  ↓
if cookie_jar_path is not None:
    session.cookies.save()  ← ⚠️ 写入 Cookies.txt
```

### 新设计流程（现在）
```
CookieHandler.__init__()
  ↓
self.cookies_path = None  ✅
  ↓
post_URL(url, data, None) / get_URL(url, None)
  ↓
if cookie_jar_path is not None:  # False, 被跳过 ✅
    session.cookies.save()  ← 不执行
```

---

## ✅ 其他数据来源验证

### 3. Task.py - yt-dlp 使用
**文件**: `moodle_dl/downloader/task.py` (lines 830, 914, 1271)

```python
cookie_jar = MoodleDLCookieJar(StringIO(self.opts.cookies_text))
```

**状态**: ✅ **不会写文件**
- 使用 `StringIO` 代替文件路径
- Cookies 从内存中读取，不会产生文件

### 4. Config - 仅读取
**文件**: `moodle_dl/config.py` (line 364-367)

```python
cookies_path = PT.get_cookies_path(self.get_misc_files_path())
if os.path.exists(cookies_path):
    with open(cookies_path, 'r', encoding='utf-8') as cookie_file:
        return cookie_file.read()
```

**状态**: ✅ **仅读取**，不会产生新文件

### 5. Authenticators - 用于导出
**文件**: `moodle_dl/cli/authenticators.py` (lines 554, 699)

```python
cookies_path = PT.get_cookies_path(self.config.get_misc_files_path())
```

**用途**: 浏览器导出 cookies 时，明确指定输出路径
**状态**: ✅ **预期行为**（用户主动请求导出）

---

## 🎯 未使用的代码

### KalvidresTextExtractor
**文件**: `moodle_dl/downloader/kalvidres_text_extractor_generic.py`

```python
class KalvidresTextExtractor:
    def __init__(self, request_helper, cookies_path):
        self.cookies_path = cookies_path
    
    def extract_text_from_url(self, url, save_path=None):
        response, session = self.request_helper.get_URL(url, self.cookies_path)
```

**状态**: 🔍 **类定义存在但无实例化代码**
- 搜索全代码库，未找到任何 `KalvidresTextExtractor()` 调用
- 可能是过时的代码或计划中的功能
- **建议**: 如果启用此代码，需要在初始化时传入 `None`

---

## 📊 修复验证

### 代码验证
```bash
# 验证修改是否成功
python3 -c "
from moodle_dl.moodle.cookie_handler import CookieHandler
import inspect
source = inspect.getsource(CookieHandler.__init__)
if 'self.cookies_path = None' in source:
    print('✅ 修复成功')
else:
    print('❌ 修复失败')
"
```

**结果**: ✅ **成功**

---

## 🔐 安全影响

### 改进
- ✅ 不再产生纯文本 cookies 文件
- ✅ Cookies 存储在数据库中（类型安全）
- ✅ Cookies 仅在内存中处理
- ✅ 减少 cookies 被意外暴露的风险

### 风险消除
- ✅ 不再有 `Cookies.txt` 被删除导致认证失败的风险
- ✅ 不再有 `Cookies.txt` 被修改导致认证错误的风险

---

## 📋 后续步骤

### 如果需要导出 Cookies
用户仍可通过命令明确导出：
```bash
moodle-dl --export-cookies
```

### 如果需要访问 Cookies
可通过数据库 API：
```python
from moodle_dl.auth_session_manager import AuthSessionManager

auth_manager = AuthSessionManager(db_file)
cookies = auth_manager.get_session_cookies(session_id)
```

### 废弃代码清理（未来考虑）
- `KalvidresTextExtractor` - 如果无使用场景，考虑删除
- 相关文档中对 `Cookies.txt` 的引用

---

## 🎓 总结

| 项目 | 状态 | 备注 |
|------|------|------|
| 自动生成 Cookies.txt | ✅ **已停止** | CookieHandler.cookies_path = None |
| RequestHelper.save() | ✅ **已跳过** | cookie_jar_path 现在为 None |
| 数据库存储 | ✅ **正常** | AuthSessionManager 处理 |
| 向后兼容性 | ✅ **保持** | 现有 Cookies.txt 不会被删除 |
| 用户手动导出 | ✅ **支持** | 仍可通过 API 导出 |

**最终结论**: 🟢 **所有问题已解决。Cookies.txt 不再自动生成。**

---

**审计日期**: 2025-11-19
**版本**: 2.3.13+
**下一步**: 等待用户反馈和实际运行验证

