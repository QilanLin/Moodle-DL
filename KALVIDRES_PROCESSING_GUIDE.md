# Kalvidres URL 处理完整指南

## 问题：如何检测和下载 kalvidres 页面的文本+视频？

---

## ✅ 答案：不需要无头浏览器！

moodle-dl 已经使用纯 HTTP requests 实现了完整的流程。

---

## 📋 检测 kalvidres URL

### 方法：从 Moodle API 响应中识别

**不需要**额外检测！Moodle API 已经告诉我们模块类型：

```python
# Moodle API: core_course_get_contents 返回
{
    "modules": [
        {
            "id": 9159619,
            "url": "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619",
            "modname": "kalvidres",  # ← 关键标识！
            "name": "01-intro",
            "modicon": "https://.../kalvidres/1758608141/monologo",
            "modplural": "Kaltura Video Resource"
        }
    ]
}
```

### moodle-dl 的处理

```python
# moodle_dl/moodle/result_builder.py:86-88

if location['module_modname'] in ['kalvidres', 'helixmedia', 'lti']:
    # 重命名为 cookie_mod-kalvidres
    location['module_modname'] = 'cookie_mod-' + location['module_modname']
    # 添加到文件列表，稍后下载
    files += self._handle_cookie_mod(module_url, **location)
```

**结论**：通过 `modname == 'kalvidres'` 识别，无需访问页面。

---

## 🎬 下载视频：使用 yt-dlp（不是无头浏览器）

### yt-dlp 的工作流程

```python
# moodle_dl/downloader/extractors/kalvidres_lti.py

class KalvidresLtiIE(InfoExtractor):
    def _real_extract(self, url):
        # 1️⃣ 下载 kalvidres 页面 HTML
        view_webpage = self._download_webpage(url, video_id)
        # ↑ 使用 requests/urllib，不是浏览器
        # ↑ 这里就获得了完整 HTML！包括 Errata 文本！

        # 2️⃣ 提取 iframe URL
        iframe_url = extract_from_regex('<iframe class="kaltura-player-iframe" src="...">')

        # 3️⃣ 下载 LTI launch 页面
        launch_webpage = self._download_webpage(iframe_url)

        # 4️⃣ 提取并提交 LTI form
        form_data = self._form_hidden_inputs('ltiLaunchForm', launch_webpage)
        submit_page = self._download_webpage(action_url, data=form_data)

        # 5️⃣ 跟随 JavaScript 重定向
        redirect_url = extract_from_regex("window.location.href = '...'")
        redirect_page = self._download_webpage(redirect_url)

        # 6️⃣ 提取 Kaltura 视频 URL
        kaltura_url = KalturaIE._extract_url(redirect_page)

        # 7️⃣ 返回给 Kaltura extractor 下载视频
        return {'_type': 'url', 'url': kaltura_url, 'ie_key': 'Kaltura'}
```

### 关键技术栈

| 组件 | 作用 | 技术 |
|------|------|------|
| **HTTP 请求** | 获取页面 HTML | `requests` / `urllib` |
| **HTML 解析** | 提取 iframe/form | 正则表达式 |
| **Form 提交** | LTI 认证 | `urlencode_postdata` |
| **Cookie 管理** | 维持会话 | `MoodleDLCookieJar` |
| **视频提取** | Kaltura API | `KalturaIE` |

**结论**：纯 HTTP 操作，无需 Selenium/Playwright 等无头浏览器。

---

## 📝 下载文本：当前缺失的功能

### ⚠️ 问题

yt-dlp 在第 1 步就下载了完整的 HTML（包含 Errata 文本），但是：
- **只提取了 iframe**
- **丢弃了页面文本**

### ✨ 解决方案

在 yt-dlp extractor 中添加文本提取：

```python
# Enhanced version

def _real_extract(self, url):
    # 1. 下载页面
    view_webpage = self._download_webpage(url, video_id)

    # ✨ 新增：提取页面文本
    text_content = self._extract_text_content(view_webpage)

    # ✨ 保存文本为 .md 文件
    if text_content:
        self._save_text(text_content, f'{video_id}_notes.md')

    # 2. 继续原有的视频提取流程
    iframe_url = extract_iframe(view_webpage)
    # ... 其余步骤不变
```

### 实现方式

**方式 1: 增强 yt-dlp Extractor**（已实现）

```python
# 文件：kalvidres_lti_enhanced.py
# 位置：/Users/linqilan/CodingProjects/Moodle-DL/moodle_dl/downloader/extractors/

class KalvidresLtiEnhancedIE(InfoExtractor):
    def _extract_page_text(self, webpage):
        # 提取标题、Errata、描述等

    def _save_text_content(self, text_content, filename):
        # 保存为 Markdown
```

**方式 2: 独立的文本提取器**（已实现）

```python
# 文件：kalvidres_text_extractor.py
# 位置：/Users/linqilan/CodingProjects/Moodle-DL/moodle_dl/downloader/

class KalvidresTextExtractor:
    def extract_text_from_url(self, url, save_path):
        # 使用 moodle-dl 的 request_helper
        # 提取并保存文本
```

**方式 3: 在下载任务中集成**

```python
# 修改：moodle_dl/downloader/task.py

elif self.file.module_modname.startswith('cookie_mod-kalvidres'):
    # 1. 先提取页面文本
    if self.opts.download_kalvidres_text:  # 新配置项
        text_extractor = KalvidresTextExtractor(...)
        text_path = self.file.saved_to.replace('.mp4', '_notes.md')
        text_extractor.extract_text_from_url(self.file.content_fileurl, text_path)

    # 2. 然后下载视频
    await self.external_download_url(add_token=False, needs_moodle_cookies=True)
```

---

## 🔄 完整处理流程图

```
Moodle API
    ↓
识别 kalvidres 模块
    ↓
创建下载任务（cookie_mod-kalvidres）
    ↓
┌─────────────────────────────┐
│ 下载任务开始                │
├─────────────────────────────┤
│ 1. 提取页面文本（新增）     │
│    - 使用 requests + cookies│
│    - 解析 HTML              │
│    - 提取 Errata/描述       │
│    - 保存为 .md 文件        │
├─────────────────────────────┤
│ 2. 下载视频（现有）         │
│    - 调用 yt-dlp            │
│    - 使用 kalvidres extractor│
│    - 提取 Kaltura URL       │
│    - 下载视频文件           │
└─────────────────────────────┘
    ↓
完成！文本 + 视频
```

---

## 💻 实际使用示例

### 当前方式（只下载视频）

```bash
# 1. 导出浏览器 cookies
python3 export_browser_cookies.py

# 2. 运行 moodle-dl
moodle-dl --path /Users/linqilan/CodingProjects

# 结果：
# ✅ Week1-01-intro.mp4 (视频)
# ❌ 没有 Errata 文本
```

### 改进后方式（文本 + 视频）

```bash
# 1. 导出 cookies (同上)
python3 export_browser_cookies.py

# 2. 配置启用文本下载
# 在 config.json 添加:
# "download_kalvidres_text": true

# 3. 运行 moodle-dl
moodle-dl --path /Users/linqilan/CodingProjects

# 结果：
# ✅ Week1-01-intro.mp4 (视频)
# ✅ Week1-01-intro_notes.md (Errata + 描述)
```

### 手动提取文本（临时方案）

```bash
# 使用独立的文本提取脚本
python3 scrape_kalvidres.py

# 或使用提取器类
python3 << EOF
from moodle_dl.downloader.kalvidres_text_extractor import KalvidresTextExtractor
from moodle_dl.moodle.request_helper import RequestHelper

# ... 初始化 request_helper ...

extractor = KalvidresTextExtractor(request_helper, cookies_path)
text = extractor.extract_text_from_url(
    'https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619',
    save_path='Week1-01-intro_notes.md'
)
EOF
```

---

## 🛠️ 技术细节

### 为什么不需要无头浏览器？

| 任务 | 传统方案 | moodle-dl 方案 |
|------|---------|---------------|
| **访问页面** | Selenium WebDriver | `requests.get()` + cookies |
| **解析 HTML** | WebDriver.find_element | 正则表达式 / BeautifulSoup |
| **提交表单** | WebDriver.submit() | `requests.post()` + form data |
| **处理重定向** | WebDriver.get() | `requests.get()` (自动跟随) |
| **提取视频** | JavaScript execution | API 调用 / URL 解析 |

**优点**：
- ✅ 更快速（无需启动浏览器）
- ✅ 更轻量（无需 ChromeDriver）
- ✅ 更稳定（无浏览器崩溃）
- ✅ 更省资源（纯 HTTP）

### 唯一需要的：有效的 Cookies

```python
# 为什么需要 cookies？
# 1. Moodle 页面需要登录认证
# 2. SSO (Microsoft OAuth) 流程复杂
# 3. 直接使用浏览器 cookies 最简单

# 如何获取 cookies？
python3 export_browser_cookies.py  # 从浏览器导出

# cookies 包含什么？
MoodleSession=xxx          # Moodle 会话
buid=xxx                   # Microsoft SSO
fpc=xxx                    # Microsoft First Party Cookie
ApplicationGatewayAffinity=xxx  # 负载均衡
```

### HTML 解析示例

```python
import re
import html

# 1. 提取 iframe
iframe_pattern = r'<iframe[^>]+class="kaltura-player-iframe"[^>]+src=(["\'])(?P<url>[^"\']+)\1'
match = re.search(iframe_pattern, html_content)
iframe_url = html.unescape(match.group('url'))

# 2. 提取 Errata
errata_pattern = r'Errata:(.*?)(?=<iframe|<div|$)'
match = re.search(errata_pattern, html_content, re.DOTALL)
errata_text = clean_html(match.group(1))

# 3. 清理 HTML
def clean_html(html_text):
    text = re.sub(r'<br\s*/?>', '\n', html_text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return text.strip()
```

---

## 📦 已提供的工具

### 文件位置

```
/Users/linqilan/CodingProjects/Moodle-DL/
├── moodle_dl/downloader/
│   ├── extractors/
│   │   ├── kalvidres_lti.py              # 原始 extractor
│   │   └── kalvidres_lti_enhanced.py     # 增强版（文本+视频）
│   └── kalvidres_text_extractor.py       # 独立文本提取器
│
├── scrape_kalvidres.py                   # 手动爬取脚本
├── export_browser_cookies.py             # Cookie 导出工具
└── download_kalvidres.sh                 # 自动下载脚本
```

### 使用方法

**立即使用（无需修改代码）**：

```bash
# 手动提取文本
python3 scrape_kalvidres.py

# 手动下载视频
yt-dlp --cookies Cookies.txt \
  "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619"

# 或使用自动脚本（文本+视频）
./download_kalvidres.sh
```

**集成到 moodle-dl（需修改代码）**：

1. 使用增强版 extractor
2. 或在下载任务中添加文本提取
3. 添加配置选项 `download_kalvidres_text`

---

## 🎯 总结

### 检测 kalvidres URL

✅ **通过 Moodle API**
✅ `modname == 'kalvidres'`
❌ **不需要**访问页面检测
❌ **不需要**特殊识别逻辑

### 下载视频

✅ **使用 yt-dlp**（纯 HTTP）
✅ **KalvidresLtiIE** extractor
✅ **浏览器 cookies** 认证
❌ **不需要**无头浏览器
❌ **不需要** Selenium/Playwright

### 下载文本

⚠️ **当前未实现**（yt-dlp 丢弃了文本）
✅ **已提供增强版 extractor**
✅ **已提供独立提取器**
✅ **可轻松集成到 moodle-dl**

### 技术栈

| 层级 | 组件 | 技术 |
|------|------|------|
| **HTTP** | 页面请求 | `requests` / `urllib` |
| **认证** | Cookie 管理 | `MoodleDLCookieJar` |
| **解析** | HTML 提取 | 正则表达式 |
| **视频** | Kaltura 下载 | `yt-dlp` + `KalturaIE` |
| **文本** | 内容提取 | 正则 + `html.unescape` |

---

**最终答案**：
**不需要无头浏览器！** 使用纯 HTTP requests + cookies + 正则表达式即可完成所有任务。🎉
