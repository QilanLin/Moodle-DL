# Kalvidres 处理指南

## 什么是 Kalvidres？

**Kalvidres** 是 Moodle 的一个插件模块，用于嵌入 Kaltura 视频内容。

**特点**：
- ✅ 包含视频（Kaltura 托管）
- ✅ 可能包含文本描述或注释
- ✅ 支持字幕
- ❌ 不在标准 Moodle API 中暴露内容

**示例**：
- 课程视频讲座
- 补充说明视频
- 勘误（Errata）视频

---

## moodle-dl 如何处理 Kalvidres

### 自动识别

Moodle API (`core_course_get_contents`) 会标识 kalvidres 模块：

```json
{
  "id": 9159619,
  "name": "01-intro",
  "modname": "kalvidres",
  "contents": []  // ❌ 空的！API 不返回 kalvidres 内容
}
```

**关键问题**：
- Moodle API 识别了 kalvidres 模块
- 但 `contents` 字段为空
- 需要访问页面 HTML 才能提取视频链接和文本

---

### 处理流程

```
1. Moodle API 返回 kalvidres 模块信息
   ↓
2. moodle-dl 识别为需要 cookie 的模块
   ↓
3. 使用 Cookies 访问页面 HTML
   → https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=MODULE_ID
   ↓
4. 从 HTML 提取：
   a) 页面描述文本（通用 DOM 提取）
   b) Kaltura 视频链接
   c) 字幕链接
   ↓
5. 下载视频和字幕
   ↓
6. 保存文本到 .txt 文件
```

---

## 技术实现

### 1. 通用文本提取

**不使用硬编码关键词！**

之前的错误做法：
```python
# ❌ 硬编码 "Errata"
if 'Errata' in html:
    extract_errata_text()
```

**现在的做法**：
```python
# ✅ 通用文本提取（使用正则表达式）

# 1. 提取 activity-description 区域
pattern = r'<div\s+class="activity-description"[^>]*>(.*?)</div>\s*</div>'
match = re.search(pattern, html_content, re.DOTALL)

if match:
    content_html = match.group(1)
    text = clean_html_preserve_structure(content_html)

# 2. 提取其他内容区域作为补充
region_pattern = r'<div[^>]*id="region-main"[^>]*>(.*?)</div>'
region_match = re.search(region_pattern, html_content, re.DOTALL)
```

**提取的内容**：
- `activity-description` - 标准活动描述（核心内容）
- `region-main` 中的段落 - 补充内容
- 页面标题（`<title>` 标签）
- 模块名称（`<h1>` 标签）

**代码位置**：
- `moodle_dl/downloader/kalvidres_text_extractor_generic.py`

---

### 2. Kaltura 视频提取

**实现**：查找页面中的 Kaltura 嵌入代码

```python
# 查找 Kaltura iframe
iframe = soup.find('iframe', src=lambda s: s and 'kaltura.com' in s)

if iframe:
    kaltura_url = iframe['src']
    # 从 URL 提取视频 ID
    # 下载视频和字幕
```

**下载工具**：
- 使用 `yt-dlp` 下载 Kaltura 视频
- 自动下载可用的字幕

**代码位置**：
- `moodle_dl/download/kaltura_downloader.py`

---

### 3. Cookie 认证

**为什么需要 Cookies？**

kalvidres 页面不在 Moodle API 中暴露，必须：
1. 使用浏览器 cookies 访问页面 HTML
2. 从 HTML 提取视频链接和文本

**使用的 Cookies**：
- `MoodleSession` - Moodle 会话（可自动生成）
- `buid`, `fpc` - Microsoft SSO（必须从浏览器导出）

**相关文档**：
- [浏览器 Cookie 导出指南](BROWSER_COOKIE_EXPORT.md)
- [认证指南](AUTHENTICATION.md)

---

## 用户视角

### 如何下载 Kalvidres 内容

#### 1. 确保已导出 Cookies

```bash
moodle-dl --init --sso

# 选择 "Yes" 当询问是否下载需要 cookie 的文件
# 从浏览器导出 cookies
```

#### 2. 正常运行 moodle-dl

```bash
moodle-dl

# moodle-dl 会自动：
# ✅ 识别 kalvidres 模块
# ✅ 访问页面 HTML
# ✅ 提取文本和视频
# ✅ 下载所有内容
```

---

### 下载的文件结构

```
课程名称/
├── Week 1/
│   ├── 01-intro.mp4           # Kaltura 视频
│   ├── 01-intro.en.vtt        # 英文字幕
│   └── 01-intro.txt           # 页面文本描述
```

**文本文件示例** (`01-intro.txt`):
```
Errata:
Page 1, line 3: "compiler" should be "interpreter"
Page 5, figure 2: the arrow should point left

Video: 01-intro (26 minutes)
```

---

## 常见问题

### Q1: 为什么 kalvidres 视频没有下载？

**可能原因**：

1. ❌ 没有导出浏览器 cookies

**解决**：
```bash
python3 export_browser_cookies.py
```

2. ❌ Cookies 已过期（特别是 buid/fpc）

**解决**：
```bash
# 重新从浏览器导出
moodle-dl --init --sso
```

3. ❌ yt-dlp 未安装

**解决**：
```bash
pip install yt-dlp
# 或
brew install yt-dlp
```

---

### Q2: 文本内容为空或不完整

**原因**：页面 HTML 结构与预期不同

**调试**：
```bash
# 1. 启用详细日志
moodle-dl --verbose

# 2. 检查日志中的 HTML 提取信息
cat MoodleDL.log | grep "activity-description"

# 3. 手动访问页面查看 HTML 结构
# 在浏览器中访问 kalvidres 页面，查看开发者工具
```

**报告问题**：
如果某个页面的文本无法提取，请提供：
- 页面 URL
- HTML 结构（开发者工具截图）
- 期望提取的内容

---

### Q3: 视频下载失败

**症状**：
```
ERROR: Failed to download Kaltura video
ERROR: HTTP 403 Forbidden
```

**原因**：
- 视频需要额外的认证
- Kaltura 服务器限制

**解决**：
```bash
# 1. 确保 cookies 有效
cat ~/.moodle-dl/Cookies.txt | grep -E "buid|fpc"

# 2. 尝试手动下载
yt-dlp "https://cdnapisec.kaltura.com/p/..."

# 3. 如果手动也失败，可能是服务器限制
# 尝试在浏览器中下载
```

---

## 技术细节

### HTML 结构识别

moodle-dl 查找以下 HTML 结构：

```html
<!-- 示例 1: 标准活动描述 -->
<div class="activity-description">
  <p>Errata: Page 1, line 3...</p>
  <iframe src="https://cdnapisec.kaltura.com/..."></iframe>
</div>

<!-- 示例 2: 通用内容框 -->
<div class="box generalbox">
  <p>注意事项...</p>
  <div class="no-overflow">
    <iframe src="https://cdnapisec.kaltura.com/..."></iframe>
  </div>
</div>

<!-- 示例 3: 介绍文本 -->
<div id="intro">
  <p>本视频介绍...</p>
</div>
```

---

### 文本提取逻辑

```python
def _extract_text_content(html_content):
    """从 HTML 提取文本内容（通用方法）"""
    text_data = {}

    # 1. 提取页面标题（从 <title> 标签）
    title_match = re.search(r'<title>([^<]+)</title>', html_content)
    if title_match:
        text_data['page_title'] = html.unescape(title_match.group(1).strip())

    # 2. 提取模块名称（从 <h1> 标签）
    h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', html_content, re.DOTALL)
    if h1_match:
        h1_text = clean_html(h1_match.group(1))
        if h1_text:
            text_data['module_name'] = h1_text

    # 3. 提取 activity-description（核心内容）
    pattern = r'<div\s+class="activity-description"[^>]*>(.*?)</div>\s*</div>'
    match = re.search(pattern, html_content, re.DOTALL)
    if match:
        content_html = match.group(1)
        text_data['activity_description'] = clean_html_preserve_structure(content_html)

    # 4. 提取 region-main 中的其他文本内容（作为补充）
    region_pattern = r'<div[^>]*id="region-main"[^>]*>(.*?)</div>\s*(?=<div[^>]*class="mt-5|$)'
    region_match = re.search(region_pattern, html_content, re.DOTALL)
    if region_match:
        # 提取段落并过滤导航文本
        paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', region_match.group(1), re.DOTALL)
        clean_paras = [clean_html(p) for p in paragraphs
                       if clean_html(p) and len(clean_html(p)) > 20]
        if clean_paras:
            text_data['additional_content'] = '\n\n'.join(clean_paras)

    return text_data
```

**特点**：
- ✅ 使用正则表达式提取（不依赖 BeautifulSoup）
- ✅ 不依赖特定关键词（如 "Errata"）
- ✅ 提取多层次内容（标题、描述、补充内容）
- ✅ 保留文本结构（段落、列表、粗体等）
- ✅ 过滤导航文本和短段落

---

### Kaltura 视频下载

```python
def download_kaltura_video(url, output_path):
    """使用 yt-dlp 下载 Kaltura 视频"""
    import yt_dlp

    ydl_opts = {
        'outtmpl': output_path,
        'writesubtitles': True,      # 下载字幕
        'writeautomaticsub': True,   # 下载自动生成的字幕
        'quiet': False,
        'no_warnings': False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
```

---

## 相关文档

- [浏览器 Cookie 导出指南](BROWSER_COOKIE_EXPORT.md) - 如何导出 cookies
- [认证指南](AUTHENTICATION.md) - Token 和 Cookies 的区别
- [Moodle API 限制](MOODLE_API_LIMITATIONS.md) - 为什么某些内容需要 cookies

---

## 总结

### 关键要点

1. ✅ Kalvidres 内容不在 Moodle API 中，需要访问页面 HTML
2. ✅ 必须导出浏览器 cookies（特别是 Microsoft SSO 的 buid/fpc）
3. ✅ 文本提取使用通用 DOM 选择器，不硬编码关键词
4. ✅ 视频下载使用 yt-dlp
5. ✅ 自动下载字幕（如果可用）

### 依赖

- **Python 库**: `beautifulsoup4`, `yt-dlp`
- **Cookies**: MoodleSession, buid, fpc
- **网络**: 可访问 Kaltura CDN

**完全自动化，无需手动操作！**
