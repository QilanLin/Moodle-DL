# Moodle-DL 中 Embedded Kaltura 视频处理完整流程分析

## 1. 系统架构概览

### 1.1 核心处理链路
```
HTML 页面 
  ↓
正则提取 iframe 标签（src="/filter/kaltura/lti_launch.php?..."）
  ↓
URL 转换（lti_launch.php URL → browseandembed URL）
  ↓
entry_id 提取
  ↓
File 对象创建（module_modname='cookie_mod-kalvidres'）
  ↓
yt-dlp 下载
```

### 1.2 关键文件位置

| 功能 | 文件路径 |
|------|----------|
| **Book 模块处理** | `moodle_dl/moodle/mods/book.py` |
| **URL 转换逻辑** | `moodle_dl/moodle/result_builder.py` (行 314-331) |
| **视频下载** | `moodle_dl/downloader/task.py` (行 1094-1121) |
| **提取器（Embedded）** | `moodle_dl/downloader/extractors/kalvidres_embedded.py` |
| **提取器（LTI）** | `moodle_dl/downloader/extractors/kalvidres_lti.py` |
| **提取器注册** | `moodle_dl/downloader/extractors/__init__.py` |

---

## 2. Kaltura 视频检测流程

### 2.1 检测方式（三种）

#### 方式 1: Book 模块内的 Embedded 视频
**位置**: `book.py` 第 335-385 行

```python
# 正则模式匹配
kaltura_pattern = r'<iframe[^>]+src="([^"]*filter/kaltura/lti_launch\.php[^"]*)"'

# 提取步骤:
1. 在章节 HTML 中查找 iframe 标签
2. 检查 src 属性是否包含 "/filter/kaltura/lti_launch.php"
3. 从 URL 的 source 参数中解码 Kaltura URL
4. 用正则 /entryid/([^/]+) 提取 entry_id
5. 创建特殊的 File 对象（type='kalvidres_embedded'）
```

**创建的 File 对象属性**:
```python
{
    'filename': 'Chapter Name - Video 1',
    'filepath': '/691947/',  # 章节 ID
    'fileurl': 'https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?...',
    'type': 'kalvidres_embedded',
    'entry_id': '1_er5gtb0g',
    'timemodified': int(time.time())
}
```

#### 方式 2: ResultBuilder URL 提取
**位置**: `result_builder.py` 第 314-331 行

```python
# 在描述/HTML 中提取所有 iframe src
urls = re.findall(r'src=[\'"]?([^\'" >]+)', content_html)

# 检查是否为 Kaltura LTI URL
if '/filter/kaltura/lti_launch.php' in url:
    # 提取 entry_id
    entry_id_match = re.search(r'entryid[/%]([^/%&]+)', url)
    
    # 转换为标准 kalvidres URL 格式
    url = f'https://{moodle_domain}/browseandembed/index/media/entryid/{entry_id}'
    module_modname = 'cookie_mod-kalvidres'
```

#### 方式 3: Print Book 中的视频
**位置**: `book.py` 第 693-748 行

```python
# 在完整 Print Book HTML 中查找
kaltura_pattern = r'<iframe[^>]+class="kaltura-player-iframe"[^>]+src="([^"]*filter/kaltura/lti_launch\.php[^"]*)"[^>]*>'

# 提取章节-视频映射关系
_extract_chapter_video_mapping_from_print_book()
```

---

## 3. URL 转换流程

### 3.1 LTI Launch URL 结构
```
https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?
  source=https%3A%2F%2Fkaf.keats.kcl.ac.uk%2Fbrowseandembed%2Findex%2Fmedia%2F
         entryid%2F1_er5gtb0g%2F...
```

### 3.2 ResultBuilder 转换（简单路径）
**位置**: `result_builder.py` 第 314-331 行

```
LTI URL 
  ↓ 正则: r'entryid[/%]([^/%&]+)'
  ↓ 提取 entry_id = "1_er5gtb0g"
  ↓ 构造 URL = "https://keats.kcl.ac.uk/browseandembed/index/media/entryid/1_er5gtb0g"
  ↓ 设置 module_modname = "cookie_mod-kalvidres"
  ↓ 创建 File 对象
```

**关键代码**:
```python
# 第 318-331 行
if url_parts.hostname == self.moodle_domain and '/filter/kaltura/lti_launch.php' in url_parts.path:
    entry_id_match = re.search(r'entryid[/%]([^/%&]+)', url)
    if entry_id_match:
        entry_id = entry_id_match.group(1)
        # 转换为 kalvidres URL 格式
        url = f'https://{self.moodle_domain}/browseandembed/index/media/entryid/{entry_id}'
        location['module_modname'] = 'cookie_mod-kalvidres'
        kaltura_converted = True
```

### 3.3 Task.py 转换（完整路径）
**位置**: `task.py` 第 853-960 行（extract_kalvidres_video_url）

```
kalvidres/view.php URL 或 lti_launch.php URL
  ↓ 步骤1: 获取 kalvidres 页面 HTML
  ↓ 步骤2: 在 iframe 中查找 lti_launch.php URL
  ↓ 步骤3: 请求 lti_launch.php，提取 target_link_uri（browseandembed URL）
  ↓ 步骤4: 从 browseandembed URL 提取:
           - entry_id (正则: /entryid/([^/]+)/)
           - uiconf_id (正则: /playerSkin/(\d+))
  ↓ 步骤5: 请求 browseandembed 页面，提取 partnerId
  ↓ 步骤6: 检测 Kaltura CDN 域名
  ↓ 步骤7: 构造最终的 Kaltura iframe URL
           https://cdnapisec.kaltura.com/p/2368101/sp/236810100/embedIframeJs/
           uiconf_id/123/partner_id/2368101?iframeembed=true&entry_id=1_smw4vcpg
  ↓ yt-dlp 下载
```

**关键代码位置**:
```python
# 第 886-891 行: 从 kalvidres 页面提取 lti_launch URL
iframe_match = re.search(r'<iframe[^>]+src="([^"]*lti_launch\.php[^"]*)"', html_content)
lti_launch_url = iframe_match.group(1).replace('&amp;', '&')

# 第 904-910 行: 从 lti_launch 提取 browseandembed URL
target_uri_match = re.search(r'name="target_link_uri"\s+value="([^"]+)"', lti_html)
browseandembed_url = target_uri_match.group(1)

# 第 913-926 行: 提取 entry_id 和 uiconf_id
entry_id_match = re.search(r'/entryid/([^/]+)/', browseandembed_url)
uiconf_id_match = re.search(r'/playerSkin/(\d+)', browseandembed_url)

# 第 935-940 行: 从 browseandembed 页面提取 partnerId
partner_id_match = re.search(r'partnerId[=:](\d+)', browseandembed_response.text)

# 第 954-957 行: 构造最终 URL
kaltura_iframe_url = (
    f'https://{kaltura_cdn}/p/{partner_id}/sp/{partner_id}00/embedIframeJs/'
    f'uiconf_id/{uiconf_id}/partner_id/{partner_id}?iframeembed=true&entry_id={entry_id}'
)
```

---

## 4. Book.py 中的当前实现

### 4.1 Book 章节下载流程
**位置**: `book.py` 第 25-233 行

```
real_fetch_mod_entries()
  ↓
方案A: Mobile API (core_course_get_contents)
  ├─ 获取章节元数据和 fileurl
  ├─ 为每个章节调用 _fetch_chapter_html()
  ├─ 从完整 HTML 中提取嵌入视频
  └─ 设置 type='html' 触发 URL 提取

  ↓

方案B: Playwright (Print Book)
  ├─ 使用 Playwright 获取完整 Print Book HTML
  ├─ 提取章节-视频映射关系
  ├─ 创建完整 HTML 文件
  └─ 视频通过标准 URL 提取处理
```

### 4.2 关键参数设置
**位置**: `book.py` 第 127-135 行

```python
# CRITICAL: 设置 type='html' 使 result_builder 自动提取 URL
chapter_content['type'] = 'html'

# 这样 result_builder 会:
# 1. 调用 _find_all_urls()
# 2. 查找所有 src 属性
# 3. 检测 lti_launch.php URLs
# 4. 转换为 kalvidres 格式
# 5. 创建 cookie_mod-kalvidres File 对象
```

### 4.3 章节 HTML 获取
**位置**: `book.py` 第 288-316 行

```python
async def _fetch_chapter_html(self, fileurl: str) -> str:
    """
    关键点:
    1. fileurl 已是完整的 Moodle webservice URL
    2. 需要添加 token 参数用于认证
    3. 返回完整的 HTML 内容（包含嵌入 iframe）
    """
    authenticated_url = f"{fileurl}{separator}token={self.client.token}"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(authenticated_url, timeout=30) as response:
            if response.status == 200:
                html_content = await response.text(encoding='utf-8')
                return html_content
```

---

## 5. 当前 Book 模块 API 使用

### 5.1 Mobile API 调用
**位置**: `book.py` 第 36-42 行

```python
# API: mod_book_get_books_by_courses
books = (
    await self.client.async_post(
        'mod_book_get_books_by_courses',
        self.get_data_for_mod_entries_endpoint(courses)
    )
).get('books', [])

# 返回数据结构:
# {
#   "id": 123,
#   "course": 456,
#   "coursemodule": 789,  # module_id
#   "name": "Book Name",
#   "timemodified": 1234567890,
#   ...
# }
```

### 5.2 Mobile API 章节内容
**位置**: `book.py` 第 57-62 行

```python
# API: core_course_get_contents (for book section)
book_contents = core_contents.get(course_id).get(module_id).get('contents', [])

# 返回结构:
# [
#   {
#     "type": "file",
#     "filename": "index.html",
#     "filepath": "/",
#     "fileurl": "https://moodle/webservice/pluginfile.php/...",
#     "filesize": 1000,
#     "timemodified": 1234567890,
#     "content": "<!DOCTYPE html>..."  # 只是简短的标题，不含视频
#   },
#   {
#     "type": "file",
#     "filename": "691947/index.html",
#     "filepath": "/",
#     "fileurl": "https://moodle/webservice/pluginfile.php/...chapter/691947/...",
#     "content": "Application Types"  # SHORT - 需要从 fileurl 获取完整内容
#   },
#   ...
# ]
```

### 5.3 Print Book API
**位置**: `book.py` 第 390-691 行

```python
# URL: /mod/book/tool/print/index.php?id={module_id}
# 使用 Playwright 获取（需要浏览器会话）

print_book_url = f"{url_base}/mod/book/tool/print/index.php?id={module_id}"

# 使用 Playwright:
# 1. 加载 cookies（从 Cookies.txt 转换为 Playwright 格式）
# 2. 初始化课程页面（确保 session 活跃）
# 3. 导航到 Print Book URL
# 4. 等待 DOM 加载（domcontentloaded）
# 5. 提取完整 HTML

# 自动 cookies 刷新机制:
# - 检测重定向（login/enrol/auth）
# - 调用 CookieManager.refresh_cookies()
# - 自动重试一次
```

---

## 6. Kaltura 视频下载流程

### 6.1 Download Task 处理
**位置**: `task.py` 第 1094-1121 行

```python
if self.file.module_modname == 'cookie_mod-kalvidres':
    # 特殊处理 kalvidres 视频
    
    # 步骤1: 提取并保存文本内容（作为 _notes.md）
    await self.extract_kalvidres_text(self.file.content_fileurl, text_path)
    
    # 步骤2: 提取实际的 Kaltura iframe URL
    kaltura_url = await self.extract_kalvidres_video_url(self.file.content_fileurl)
    
    # 步骤3: 临时替换 URL 并下载
    if kaltura_url:
        original_url = self.file.content_fileurl
        self.file.content_fileurl = kaltura_url  # 替换为可下载的 iframe URL
        
        # 使用 yt-dlp 下载（Kaltura 提取器处理）
        await self.external_download_url(
            add_token=False,
            delete_if_successful=True,
            needs_moodle_cookies=True
        )
        
        self.file.content_fileurl = original_url  # 恢复原始 URL
    else:
        # 降级处理: 尝试使用原始 URL
        await self.external_download_url(...)
```

### 6.2 Kaltura 提取器
**位置**: `extractors/kalvidres_embedded.py` 第 11-63 行

```python
class KalvidresEmbeddedIE(InfoExtractor):
    """
    处理 lti_launch.php URLs
    
    流程:
    1. 从 URL 参数中提取 source（已包含完整的 browseandembed URL）
    2. 从 source 中提取 entry_id
    3. 请求 browseandembed 页面
    4. 从页面中提取 partnerId
    5. 构造 Kaltura URL: kaltura:partner_id:entry_id
    6. yt-dlp 内置的 KalturaIE 处理最终下载
    """
    
    IE_NAME = 'kalvidresEmbedded'
    _VALID_URL = r'(?P<scheme>https?://)(?P<host>[^/]+)(?P<path>.*)?/filter/kaltura/lti_launch\.php\?.*'
    
    def _real_extract(self, url):
        # 解析 URL 参数
        parsed_url = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed_url.query)
        
        # 提取 source 参数（包含 browseandembed URL）
        kaltura_source = params.get('source', [''])[0]
        
        # 从 source 提取 entry_id
        entry_id_match = re.search(r'/entryid/([^/]+)', kaltura_source)
        entry_id = entry_id_match.group(1)
        
        # 请求 browseandembed 页面获取 partnerId
        browse_page = self._download_webpage(kaltura_source, entry_id)
        partner_id_match = re.search(r'"partnerId"\s*:\s*(\d+)', browse_page)
        partner_id = partner_id_match.group(1)
        
        # 返回 Kaltura URL 供 yt-dlp 的 KalturaIE 处理
        return {
            '_type': 'url',
            'url': f'kaltura:{partner_id}:{entry_id}',
            'ie_key': 'Kaltura',
        }
```

---

## 7. 视频链接到本地文件

### 7.1 Print Book HTML 中的替换
**位置**: `book.py` 第 750-793 行

```python
def _replace_kaltura_iframes_with_video_tags(self, html_content, video_list):
    """
    将 iframe 替换为 HTML5 video 标签，指向本地文件
    """
    
    # 对每个视频:
    video_tag = f'''<div class="kaltura-video-container">
    <video controls>
        <source src="{relative_path}" type="video/mp4">
        <p>Your browser does not support HTML5 video.</p>
    </video>
    <p>{video_name}</p>
</div>'''
    
    # 使用正则替换原始 iframe
    iframe_pattern = r'<iframe[^>]*src="' + re.escape(iframe_src) + r'"[^>]*>.*?</iframe>'
    modified_html = re.sub(iframe_pattern, video_tag, modified_html, flags=re.DOTALL)
```

### 7.2 章节内视频路径组织
**位置**: `book.py` 第 206-208 行

```python
# 文件结构:
# 下载目录/
#   ├── Table of Contents.html
#   ├── Chapter 1/
#   │   ├── index.html
#   │   ├── Video 01 (1_er5gtb0g).mp4
#   │   └── Video 02 (1_xyz1234).mp4
#   ├── Chapter 2/
#   │   ├── index.html
#   │   └── Video (1_abc5678).mp4
#   └── BookName.html (Print Book)

# 在 chapter HTML 中，video src 使用相对路径:
# <source src="Video 01 (1_er5gtb0g).mp4" type="video/mp4">

# 在 Print Book HTML 中，video src 也使用相对路径:
# <source src="691947/Video 01 (1_er5gtb0g).mp4" type="video/mp4">
#         ^^^^^^^ 章节文件夹名
```

---

## 8. 完整的数据流示例

### 8.1 从 Print Book HTML 到下载

```
Print Book HTML:
<div class="book_chapter" id="ch691947">
    <h2>2. Week Overview</h2>
    <iframe class="kaltura-player-iframe"
            src="https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?
                 source=https%3A%2F%2Fkaf.keats.kcl.ac.uk%2F
                 browseandembed%2Findex%2Fmedia%2Fentryid%2F1_er5gtb0g%2F...">
    </iframe>
</div>

↓ book.py: _extract_chapter_video_mapping_from_print_book()
↓ 正则匹配 id="ch691947" 和 /entryid/1_er5gtb0g

{
    "691947": ["1_er5gtb0g"]  # chapter_id -> [entry_ids]
}

↓ result_builder.py: _find_all_urls()
↓ 检测 /filter/kaltura/lti_launch.php
↓ 转换为: https://keats.kcl.ac.uk/browseandembed/index/media/entryid/1_er5gtb0g
↓ 设置 module_modname = 'cookie_mod-kalvidres'

File 对象:
{
    'module_modname': 'cookie_mod-kalvidres',
    'content_filename': 'Kaltura Video 1_er5gtb0g',
    'content_fileurl': 'https://keats.kcl.ac.uk/browseandembed/index/media/entryid/1_er5gtb0g',
    'content_filepath': '/691947/',
    'content_type': 'description-url'
}

↓ task.py: download()
↓ 检测 module_modname == 'cookie_mod-kalvidres'
↓ 调用 extract_kalvidres_video_url()

task.py (extract_kalvidres_video_url):
1. GET https://keats.kcl.ac.uk/browseandembed/index/media/entryid/1_er5gtb0g
2. 解析 HTML，提取 partnerId (例如: 2368101)
3. 构造: https://cdnapisec.kaltura.com/p/2368101/sp/236810100/embedIframeJs/
        uiconf_id/123/partner_id/2368101?iframeembed=true&entry_id=1_er5gtb0g

↓ yt-dlp (KalturaIE extractor)
↓ 从 Kaltura iframe 页面提取 m3u8/mp4 URLs
↓ 下载视频

↓ 本地保存:
下载目录/
  └── Books/
      └── Book Name/
          └── 691947/  (章节文件夹)
              ├── index.html
              └── Kaltura Video 1_er5gtb0g.mp4
```

---

## 9. Kaltura 视频 API 数据结构

从 `kaltura_response_8.json` 解析：

```json
[
  {
    "id": "1_smw4vcpg",
    "name": "01-intro",
    "description": "intro video",
    "duration": 1567,  // 秒
    "downloadUrl": "https://cdnapisec.kaltura.com/p/2368101/.../download",
    "dataUrl": "https://cdnapisec.kaltura.com/p/2368101/.../playManifest/..."
  },
  {
    "sources": [
      {
        "format": "applehttp",
        "url": "https://cdnapisec.kaltura.com/p/2368101/.../a.m3u8"  // HLS
      },
      {
        "format": "url",
        "url": "https://cdnapisec.kaltura.com/p/2368101/.../a.mp4"  // 直接 MP4
      },
      {
        "format": "mpegdash",
        "url": "https://cdnapisec.kaltura.com/p/2368101/.../manifest.mpd"  // DASH
      }
    ]
  }
]
```

---

## 10. 关键代码位置总结

| 功能 | 文件 | 行数 | 说明 |
|------|------|------|------|
| Book 模块初始化 | book.py | 25-233 | 双 API 方案 |
| 章节 HTML 获取 | book.py | 288-316 | 通过 webservice URL + token |
| Embedded 视频检测 | book.py | 318-385 | 从章节 HTML 提取 |
| Print Book 获取 | book.py | 390-691 | Playwright 浏览器 |
| Print Book 视频提取 | book.py | 693-748 | 章节-视频映射 |
| HTML 中 iframe 替换 | book.py | 750-793 | video 标签化 |
| LTI URL 检测 | result_builder.py | 314-331 | 描述中的视频转换 |
| 文件对象创建 | result_builder.py | 432-450 | kalvidres_embedded 处理 |
| URL 提取 | result_builder.py | 242-370 | _find_all_urls 全局处理 |
| 视频下载 | task.py | 1094-1121 | cookie_mod-kalvidres 处理 |
| 文本提取 | task.py | 766-851 | 从 kalvidres 页面提取 |
| 视频 URL 提取 | task.py | 853-960 | Kaltura iframe URL 构造 |
| Embedded 提取器 | kalvidres_embedded.py | 11-63 | yt-dlp 集成提取器 |
| LTI 提取器 | kalvidres_lti.py | 12-63 | 完整 LTI 流程 |
| 提取器注册 | __init__.py | 18 | 自动发现和注册 |

---

## 11. 视频转换的三种方式总结

### 方式 1: 简单转换（ResultBuilder）
```
lti_launch.php URL → browseandembed URL → cookie_mod-kalvidres File
```
- 位置: `result_builder.py:314-331`
- 用途: 描述/HTML 中嵌入的 iframe
- 处理: 直接正则转换，最快

### 方式 2: 完整转换（Task）
```
kalvidres/view.php → lti_launch.php → browseandembed → Kaltura iframe → MP4
```
- 位置: `task.py:853-960`
- 用途: 下载时才提取（获取最新的 Kaltura CDN）
- 处理: 链式 HTTP 请求，最准确

### 方式 3: Book 模块提取（Book.py）
```
Print Book HTML → 章节-视频映射 → embedded File → 正确的本地路径
```
- 位置: `book.py:693-979`
- 用途: Book 模块内的组织结构
- 处理: 保留章节组织，方便导航

---

## 12. 配置和调试

### 12.1 关键配置项
```json
{
  "download_books": true,                    // 启用 book 模块
  "download_also_with_cookie": true,         // 启用 cookie_mod 下载
  "preferred_browser": "firefox",            // 用于 Playwright
  "skip_cert_verify": false                  // SSL 验证
}
```

### 12.2 调试方法
```bash
# 启用详细日志
moodle-dl --verbose --log-to-file

# 查看日志
cat ~/.moodle-dl/MoodleDL.log | grep -i kaltura

# 调试 Print Book HTML
cat /tmp/print_book_debug.html

# 调试 Playwright
cat /tmp/playwright_debug_*.html
```

### 12.3 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| Print Book 下载失败 | Cookies 过期 | 运行 `moodle-dl --init --sso` |
| 视频无法下载 | Kaltura CDN 不可达 | 检查网络连接 |
| entry_id 提取失败 | URL 格式变化 | 更新正则模式 |
| 本地文件路径错误 | 章节组织混乱 | 检查 _extract_chapter_video_mapping |

