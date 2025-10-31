# Kalvidres 通用文本提取指南

## 关键改进：不硬编码关键词

### ❌ 之前的问题

之前的实现硬编码了 "Errata" 关键词：

```python
# ❌ 硬编码方式
if 'Errata' in html_content:
    errata_pattern = r'Errata:(.*?)(?=<div|<iframe|$)'
    match = re.search(errata_pattern, html_content)
```

**问题**：
- "Errata" 只是一个例子，不是所有页面都有
- 其他页面可能有不同的内容（描述、说明、注意事项等）
- 硬编码限制了通用性

---

### ✅ 改进后的方案

基于实际 HTML 结构，使用 **通用的 DOM 选择器**：

```python
# ✅ 通用方式：提取 activity-description 区域
pattern = r'<div\s+class="activity-description"[^>]*>(.*?)</div>\s*</div>'
match = re.search(pattern, html_content, re.DOTALL)
```

**优点**：
- ✅ 适用于所有 kalvidres 页面
- ✅ 不依赖特定关键词
- ✅ 提取完整的页面文本内容
- ✅ 保留格式（列表、粗体、链接等）

---

## Kalvidres 页面 HTML 结构

基于真实 HAR 文件分析：

```html
<!DOCTYPE html>
<html>
<head>
    <title>intro video (26 mins) | KEATS</title>
    <!-- ... -->
</head>
<body>
    <!-- 导航栏 -->

    <div id="region-main">
        <!-- 页面内容 -->

        <!-- 1. 模块名称 -->
        <h1>intro video (26 mins)</h1>

        <!-- 2. 核心文本内容（关键！） -->
        <div class="activity-description" id="intro">
            <div class="no-overflow">
                <p><b>Errata:</b></p>
                <ul>
                    <li>The pentium bug was actually discovered in 1994...</li>
                    <li>GCC on Macs is of course just an alias...</li>
                </ul>
            </div>
        </div>

        <!-- 3. Kaltura 视频 iframe -->
        <div class="kaltura-player-container">
            <iframe class="kaltura-player-iframe" src="..."></iframe>
        </div>

        <!-- 导航链接 -->
    </div>
</body>
</html>
```

### 关键区域

| 区域 | 选择器 | 内容 |
|------|--------|------|
| **页面标题** | `<title>` | "intro video (26 mins) \| KEATS" |
| **模块名称** | `<h1>` | "intro video (26 mins)" |
| **文本内容** | `<div class="activity-description">` | **所有页面文本**（Errata/描述/说明等） |
| **视频** | `<iframe class="kaltura-player-iframe">` | Kaltura 视频播放器 |

---

## 通用提取逻辑

### 1. HTML 结构提取

```python
def extract_text_content(html_content):
    text_data = {}

    # 提取页面标题
    title_match = re.search(r'<title>([^<]+)</title>', html_content)
    if title_match:
        text_data['page_title'] = html.unescape(title_match.group(1))

    # 提取模块名称
    h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', html_content, re.DOTALL)
    if h1_match:
        text_data['module_name'] = clean_html(h1_match.group(1))

    # ✨ 提取 activity-description（核心内容）
    activity_desc = extract_activity_description(html_content)
    if activity_desc:
        text_data['activity_description'] = activity_desc

    return text_data
```

### 2. Activity Description 提取

```python
def extract_activity_description(html_content):
    """
    提取 activity-description 区域
    这里包含页面的所有文本内容（不限于 Errata）
    """
    pattern = r'<div\s+class="activity-description"[^>]*>(.*?)</div>\s*</div>'
    match = re.search(pattern, html_content, re.DOTALL)

    if match:
        content_html = match.group(1)
        return clean_html_preserve_structure(content_html)

    return None
```

### 3. HTML 清理（保留格式）

```python
def clean_html_preserve_structure(html_text):
    """
    清理 HTML 但保留基本结构
    转换为 Markdown 格式
    """
    # 转换 <br> 为换行
    text = re.sub(r'<br\s*/?>', '\n', html_text)

    # 转换段落
    text = re.sub(r'</p>\s*<p[^>]*>', '\n\n', text)

    # 转换列表项
    text = re.sub(r'<li[^>]*>', '\n• ', text)

    # 保留粗体（转换为 Markdown）
    text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text)
    text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text)

    # 保留斜体
    text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text)
    text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text)

    # 保留链接（转换为 Markdown）
    text = re.sub(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r'[\2](\1)', text)

    # 移除所有其他标签
    text = re.sub(r'<[^>]+>', '', text)

    # 解码 HTML 实体
    text = html.unescape(text)

    # 清理空白
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    text = re.sub(r' +', ' ', text)

    return text.strip()
```

---

## 提取结果示例

### 输入：HTML

```html
<div class="activity-description" id="intro">
    <div class="no-overflow">
        <p><b>Errata:</b></p>
        <ul>
            <li>The pentium bug was actually discovered in 1994, not "around 2000".</li>
            <li>GCC on Macs is of course just an alias to clang from the LLVM project.</li>
        </ul>
    </div>
</div>
```

### 输出：Markdown

```markdown
**Errata:**

• The pentium bug was actually discovered in 1994, not "around 2000".

• GCC on Macs is of course just an alias to clang from the LLVM project.
```

**保留的格式**：
- ✅ 粗体（`**Errata:**`）
- ✅ 列表项（`•`）
- ✅ 换行结构
- ✅ 可读性

---

## 适用范围

这个通用方法适用于：

### ✅ 任何 kalvidres 页面

不限于特定内容：
- Errata（勘误）
- Description（描述）
- Notes（注意事项）
- Instructions（说明）
- Additional information（补充信息）
- **任何在 activity-description 中的文本**

### ✅ 保留的格式

- 粗体/斜体
- 列表（有序/无序）
- 链接
- 段落结构
- 换行

### ✅ Markdown 输出

生成的 Markdown 文件可以：
- 直接在 Markdown 编辑器中查看
- 转换为 PDF/HTML
- 在 GitHub/GitLab 中显示
- 方便阅读和分享

---

## 使用示例

### 方法 1: 使用通用提取器类

```python
from moodle_dl.downloader.kalvidres_text_extractor_generic import KalvidresTextExtractor

# 初始化
extractor = KalvidresTextExtractor(request_helper, cookies_path)

# 提取文本
text_data = extractor.extract_text_from_url(
    url='https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619',
    save_path='Week1-intro_notes.md'
)

# 访问提取的内容
print(text_data['page_title'])           # 页面标题
print(text_data['module_name'])          # 模块名称
print(text_data['activity_description']) # 核心文本内容
```

### 方法 2: 独立脚本测试

```bash
# 使用测试脚本验证
python3 test_generic_extractor.py
```

**输出**：
```
✅ 提取了 3 个字段
📄 页面标题: intro video (26 mins) | KEATS
📌 模块名称: intro video (26 mins)
📝 Activity Description:
   **Errata:**
   • The pentium bug was actually discovered in 1994...
   • GCC on Macs is of course just an alias...
```

### 方法 3: 集成到 moodle-dl

在 `task.py` 的 `cookie_mod-kalvidres` 下载流程中添加：

```python
elif self.file.module_modname.startswith('cookie_mod-kalvidres'):
    # 1. 提取页面文本（新增）
    from moodle_dl.downloader.kalvidres_text_extractor_generic import KalvidresTextExtractor

    extractor = KalvidresTextExtractor(self.request_helper, self.cookies_path)
    text_path = self.file.saved_to.replace('.mp4', '_notes.md')

    extractor.extract_text_from_url(
        self.file.content_fileurl,
        save_path=text_path
    )

    # 2. 下载视频（现有）
    await self.external_download_url(
        add_token=False,
        needs_moodle_cookies=True
    )
```

---

## 技术对比

### 硬编码方式 ❌

```python
# 搜索 "Errata" 关键词
if 'Errata' in html_content:
    pattern = r'Errata:(.*?)(?=<div|<iframe|$)'
    match = re.search(pattern, html_content)
```

**问题**：
- 只能提取包含 "Errata" 的页面
- 无法处理其他类型的内容
- 依赖特定文本格式
- 不通用

### 通用方式 ✅

```python
# 提取 activity-description DOM 结构
pattern = r'<div\s+class="activity-description"[^>]*>(.*?)</div>\s*</div>'
match = re.search(pattern, html_content, re.DOTALL)
```

**优点**：
- 适用于所有 kalvidres 页面
- 基于 HTML 结构，不依赖文本内容
- 提取完整的页面说明
- 保留格式和结构

---

## 实际测试结果

### 测试文件

- **输入**: `/Users/linqilan/CodingProjects/example_html_resposne_HAR`
- **HTML 大小**: 227,231 字符
- **输出**: `/Users/linqilan/CodingProjects/Moodle-DL/kalvidres_extracted_generic.md`

### 提取结果

```markdown
# intro video (26 mins) | KEATS

## intro video (26 mins)

**Errata:**

• The pentium bug was actually discovered in 1994, not "around 2000".

• GCC on Macs is of course just an alias to clang from the LLVM project; it is not the C-compiler from the GNU Software Foundation.
```

### 验证

- ✅ 页面标题：提取成功
- ✅ 模块名称：提取成功
- ✅ 文本内容：提取成功
- ✅ 粗体格式：保留（`**Errata:**`）
- ✅ 列表结构：保留（`•`）
- ✅ 字符数：216 字符
- ✅ 行数：5 行

---

## 文件结构

```
/Users/linqilan/CodingProjects/Moodle-DL/
├── moodle_dl/downloader/
│   ├── kalvidres_text_extractor.py          # 原始版本（硬编码）
│   └── kalvidres_text_extractor_generic.py  # ✨ 通用版本（推荐）
│
├── test_generic_extractor.py                # 通用提取器测试
├── kalvidres_extracted_generic.md           # 提取结果示例
│
└── GENERIC_TEXT_EXTRACTION_GUIDE.md         # 本指南
```

---

## 关键要点总结

### 1. 不要硬编码关键词
❌ 不要搜索 "Errata"、"Description" 等特定文本
✅ 使用 HTML 结构（`class="activity-description"`）

### 2. 基于 DOM 结构提取
❌ 不要依赖文本内容识别
✅ 使用 CSS 选择器/类名提取

### 3. 保留格式
❌ 不要只提取纯文本
✅ 保留粗体、列表、链接等格式

### 4. 转换为 Markdown
❌ 不要保留 HTML 标签
✅ 转换为可读的 Markdown

### 5. 通用性优先
❌ 不要为单个示例优化
✅ 设计适用于所有页面的方案

---

## 下一步

### 立即可用

```bash
# 测试通用提取器
python3 test_generic_extractor.py
```

### 集成到 moodle-dl

1. 替换 `kalvidres_text_extractor.py` 为通用版本
2. 在下载任务中调用提取器
3. 保存文本为 `.md` 文件
4. 与视频一起下载

### 扩展应用

通用提取逻辑可以应用于：
- ✅ Kalvidres 页面
- ✅ Helixmedia 页面
- ✅ 其他 LTI 集成页面
- ✅ 任何包含 `activity-description` 的 Moodle 页面

---

**总结**：使用基于 **HTML 结构** 的通用提取方法，而不是硬编码特定关键词，实现了更强的通用性和可维护性。🎉
