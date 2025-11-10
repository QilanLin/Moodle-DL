# Kaltura 视频处理 - 快速参考表

## 正则模式

| 用途 | 正则 | 匹配示例 |
|------|------|----------|
| **检测 LTI Launch URL** | `/filter/kaltura/lti_launch\.php` | https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?source=... |
| **提取 Entry ID** | `entryid[/%]([^/%&]+)` | 从 URL 中提取 `1_er5gtb0g` |
| **提取 Partner ID** | `partnerId[=:](\d+)` | 从 HTML 中提取 `2368101` |
| **提取 UIConf ID** | `/playerSkin/(\d+)` | 从 URL 中提取 `123456` |
| **匹配 iframe 标签** | `<iframe[^>]+src="([^"]*lti_launch\.php[^"]*)"` | 整个 iframe src 属性 |
| **Kaltura iframe** | `<iframe[^>]+class="kaltura-player-iframe"` | 带有特定 class 的 iframe |
| **章节 div** | `<div[^>]*class="[^"]*book_chapter[^"]*"[^>]*id="ch(\d+)"` | Print Book 中的章节 div |

## URL 格式演变

```
阶段1: LTI Launch URL (在 HTML 中)
https://keats.kcl.ac.uk/filter/kaltura/lti_launch.php?source=...&nonce=...

↓ 提取 source 参数并 URL 解码

阶段2: Browse & Embed URL (Kaltura 官方 UI)
https://kaf.keats.kcl.ac.uk/browseandembed/index/media/entryid/1_er5gtb0g/playerSkin/123456

↓ 提取 partnerId 和构造 Kaltura iframe

阶段3: Kaltura Iframe URL (yt-dlp 可处理)
https://cdnapisec.kaltura.com/p/2368101/sp/236810100/embedIframeJs/
uiconf_id/123456/partner_id/2368101?iframeembed=true&entry_id=1_er5gtb0g

↓ yt-dlp KalturaIE 处理

阶段4: 实际媒体 URLs
- HLS: https://cdnapisec.kaltura.com/.../format/applehttp/a.m3u8
- MP4: https://cdnapisec.kaltura.com/.../format/url/name/a.mp4
- DASH: https://cdnapisec.kaltura.com/.../format/mpegdash/manifest.mpd
```

## 函数调用链路

```
BookMod.real_fetch_mod_entries()
├─ _fetch_chapter_html(fileurl)
│  └─ aiohttp GET (with token) → HTML 内容
├─ _extract_kaltura_videos_from_html()
│  ├─ 正则匹配 lti_launch.php
│  ├─ 提取 entry_id
│  └─ 创建 File(type='kalvidres_embedded')
├─ _fetch_print_book_html()
│  ├─ Playwright 获取 HTML
│  ├─ 自动 cookies 刷新
│  └─ 返回完整 Print Book HTML
├─ _extract_chapter_video_mapping_from_print_book()
│  └─ 返回 {chapter_id: [entry_ids]}
└─ 返回 book_files

↓

ResultBuilder.get_files_in_sections()
├─ _handle_files()
│  ├─ 处理 File(type='kalvidres_embedded')
│  └─ 创建 File(type='cookie_mod')
└─ _find_all_urls()
   ├─ 检测 /filter/kaltura/lti_launch.php
   ├─ 提取 entry_id
   ├─ 转换 URL
   ├─ 设置 module_modname = 'cookie_mod-kalvidres'
   └─ 创建 File

↓

DownloadTask.run()
├─ module_modname == 'cookie_mod-kalvidres'?
├─ extract_kalvidres_text()
│  ├─ 请求 kalvidres 页面 HTML
│  ├─ 用正则提取 activity-description
│  └─ 保存为 _notes.md
├─ extract_kalvidres_video_url()
│  ├─ GET kalvidres page → 提取 lti_launch URL
│  ├─ GET lti_launch.php → 提取 browseandembed URL
│  ├─ GET browseandembed → 提取 partnerId
│  └─ 构造 Kaltura iframe URL
└─ external_download_url()
   └─ yt-dlp 下载

↓

yt-dlp KalturaIE
├─ 解析 Kaltura iframe URL
├─ 从页面提取 playback 信息
├─ 获取 m3u8/mp4 URL
└─ wget/curl 下载
```

## File 对象状态变化

```
1. BookMod 创建 (type='kalvidres_embedded'):
   {
     'filename': 'Chapter 1 - Video 1',
     'filepath': '/691947/',
     'fileurl': 'https://.../filter/kaltura/lti_launch.php?...',
     'type': 'kalvidres_embedded',
     'entry_id': '1_er5gtb0g'
   }

2. ResultBuilder 转换 (type='cookie_mod'):
   {
     'filename': 'Kaltura Video 1_er5gtb0g',
     'filepath': '/691947/',
     'fileurl': 'https://.../browseandembed/index/media/entryid/1_er5gtb0g',
     'module_modname': 'cookie_mod-kalvidres',
     'content_type': 'description-url'
   }

3. Download 处理:
   - 文本: 下载目录/.../691947/Kaltura Video 1_er5gtb0g_notes.md
   - 视频: 下载目录/.../691947/Kaltura Video 1_er5gtb0g.mp4
```

## 关键代码片段

### 1. 检测 Kaltura 视频
```python
kaltura_pattern = r'<iframe[^>]+src="([^"]*filter/kaltura/lti_launch\.php[^"]*)"'
matches = re.findall(kaltura_pattern, html_content, re.IGNORECASE)
for iframe_src in matches:
    source_match = re.search(r'[?&]source=([^&]+)', iframe_src)
    kaltura_source = urllib.parse.unquote(source_match.group(1))
    entry_id_match = re.search(r'/entryid/([^/]+)', kaltura_source)
    entry_id = entry_id_match.group(1)
```

### 2. 转换 LTI URL
```python
# ResultBuilder 方式
if '/filter/kaltura/lti_launch.php' in url:
    entry_id_match = re.search(r'entryid[/%]([^/%&]+)', url)
    entry_id = entry_id_match.group(1)
    url = f'https://{moodle_domain}/browseandembed/index/media/entryid/{entry_id}'
    module_modname = 'cookie_mod-kalvidres'
```

### 3. 获取章节 HTML
```python
async def _fetch_chapter_html(self, fileurl: str) -> str:
    separator = '&' if '?' in fileurl else '?'
    authenticated_url = f"{fileurl}{separator}token={self.client.token}"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(authenticated_url, timeout=30) as response:
            if response.status == 200:
                return await response.text(encoding='utf-8')
```

### 4. 构造 Kaltura iframe URL
```python
# task.py 方式
kaltura_iframe_url = (
    f'https://{kaltura_cdn}/p/{partner_id}/sp/{partner_id}00/embedIframeJs/'
    f'uiconf_id/{uiconf_id}/partner_id/{partner_id}'
    f'?iframeembed=true&entry_id={entry_id}'
)
```

### 5. 创建 Video 标签
```python
video_tag = f'''<div class="kaltura-video-container">
    <video controls style="width: 100%; max-width: 608px;">
        <source src="{relative_path}" type="video/mp4">
        <p>Your browser does not support HTML5 video.</p>
    </video>
</div>'''
```

## 文件路径例子

### Book 模块下载后的目录结构
```
~/.moodle-dl/
  └── courses/
      └── COMP101 (Software Engineering)/
          └── Books/
              └── Java Programming Guide/
                  ├── Table of Contents.html
                  ├── 691941/
                  │   ├── index.html
                  │   └── Video (1_er5gtb0g).mp4
                  ├── 691947/
                  │   ├── index.html
                  │   ├── Video 01 (1_xyz1234).mp4
                  │   └── Video 02 (1_abc5678).mp4
                  ├── 691950/
                  │   ├── index.html
                  │   └── Video (1_def9012).mp4
                  └── Java Programming Guide.html  (Print Book)
```

## 日志关键词搜索

```bash
# 查找所有 Kaltura 相关日志
grep -i kaltura ~/.moodle-dl/MoodleDL.log

# 查找视频提取
grep "Extracting.*video" ~/.moodle-dl/MoodleDL.log

# 查找 URL 转换
grep "Converted Kaltura" ~/.moodle-dl/MoodleDL.log

# 查找下载错误
grep -i "kalvidres.*failed\|error" ~/.moodle-dl/MoodleDL.log

# 查看 Print Book 问题
grep "print book\|Playwright" ~/.moodle-dl/MoodleDL.log
```

## 环境变量和配置

```json
// config.json 关键字段
{
  "download_books": true,                    // Book 模块开关
  "download_also_with_cookie": true,         // Cookie-based 下载开关
  "preferred_browser": "firefox",            // Playwright 浏览器
  "skip_cert_verify": false,                 // SSL 证书验证
  "misc_files_path": "~/.moodle-dl",        // Cookies 位置
  "download_parallel": 1                     // 降低并发（默认）
}
```

## 常用命令

```bash
# 首次设置 SSO 登录和获取 cookies
moodle-dl --init --sso

# 刷新 cookies（Print Book 失败时）
moodle-dl --init --sso

# 只下载 Books
moodle-dl --verbose --log-to-file

# 查看下载进度中的 Kaltura 视频
tail -f ~/.moodle-dl/MoodleDL.log | grep -i kaltura

# 测试特定课程
moodle-dl --course-id 456
```

