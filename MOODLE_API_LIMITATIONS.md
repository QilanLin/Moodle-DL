# Moodle Mobile API 的局限性

## 你的问题

> 若只使用 Moodle-dl 用的 Moodle Mobile API，仍能获得 activity-description 的文本（如 errata）但不能获取 Kaltura 视频？

**答案：都不能！** ❌

仅使用 Moodle Mobile API **既不能获取 activity-description 文本，也不能获取 Kaltura 视频**。

---

## Moodle Mobile API 返回了什么？

### API 调用
```python
# moodle_dl/moodle/core_handler.py:117
course_sections = self.client.post('core_course_get_contents', data)
```

### 实际返回内容

对于 kalvidres 模块，API 只返回：

```json
{
    "modules": [
        {
            "id": 9159619,
            "url": "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619",
            "modname": "kalvidres",
            "name": "01-intro",
            "modicon": "https://keats.kcl.ac.uk/theme/image.php/kcl/kalvidres/1758608141/monologo",
            "modplural": "Kaltura Video Resource"
        }
    ]
}
```

### ❌ API **没有**返回：

1. **Activity-description 的 HTML 内容**
   - API 不返回 `<div class="activity-description">...</div>`
   - 不返回 Errata 文本
   - 不返回任何页面文本内容

2. **Kaltura 视频 URL**
   - API 不返回 iframe URL
   - 不返回 LTI launch URL
   - 不返回 Kaltura 视频的直接链接

### ✅ API **只返回**：

- ✅ **模块类型** (`modname: "kalvidres"`)
- ✅ **页面 URL** (`url: "https://..."`)
- ✅ **模块名称** (`name: "01-intro"`)

---

## 要获取内容，必须访问页面 HTML

### 1️⃣ 获取 activity-description 文本

**必须**访问页面 URL：

```python
# 步骤 1: 从 API 获取 URL
api_response = {
    "url": "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619"
}

# 步骤 2: 访问页面 HTML（需要 cookies）
response = requests.get(api_response['url'], cookies=moodle_cookies)
html_content = response.text

# 步骤 3: 解析 HTML 提取 activity-description
pattern = r'<div\s+class="activity-description"[^>]*>(.*?)</div>\s*</div>'
match = re.search(pattern, html_content, re.DOTALL)
text_content = clean_html(match.group(1))
```

**关键**：
- ❌ API **不提供** HTML 内容
- ✅ 必须额外 HTTP GET 请求页面
- ✅ 需要有效的 Moodle cookies（登录认证）

---

### 2️⃣ 获取 Kaltura 视频

**必须**访问页面 HTML 并执行多步流程：

```python
# 步骤 1: 从 API 获取 URL（同上）
url = api_response['url']

# 步骤 2: 下载 kalvidres 页面
view_webpage = download_webpage(url, cookies=moodle_cookies)

# 步骤 3: 提取 iframe URL
iframe_pattern = r'<iframe class="kaltura-player-iframe" src="([^"]+)">'
iframe_url = extract(iframe_pattern, view_webpage)

# 步骤 4: 下载 LTI launch 页面
launch_webpage = download_webpage(iframe_url)

# 步骤 5: 提取并提交 LTI form
form_data = extract_hidden_inputs('ltiLaunchForm', launch_webpage)
submit_page = post(action_url, data=form_data)

# 步骤 6: 跟随 JavaScript 重定向
redirect_url = extract("window.location.href = '...'", submit_page)
redirect_page = download_webpage(redirect_url)

# 步骤 7: 提取 Kaltura 视频 URL
kaltura_url = extract_kaltura_url(redirect_page)
```

**关键**：
- ❌ API **不提供** 视频 URL
- ✅ 必须访问页面 HTML
- ✅ 必须执行 LTI 认证流程（多步 HTTP 请求）
- ✅ 需要 Moodle cookies

---

## moodle-dl 的实际工作流程

### 流程图

```
1️⃣ Moodle API (core_course_get_contents)
   ↓
   返回：modname="kalvidres", url="https://..."
   ❌ 没有文本内容
   ❌ 没有视频 URL

2️⃣ 识别为 cookie_mod-kalvidres
   ↓
   (result_builder.py:86-88)

3️⃣ 创建下载任务 (Task)
   ↓

4️⃣ 访问页面 HTML（task.py:898-910）
   ├─→ 提取 activity-description 文本 ✅ (task.py:700-774)
   │   └─→ 保存为 *_notes.md
   │
   └─→ 调用 yt-dlp 下载视频 ✅ (task.py:372-449)
       └─→ 执行 LTI 流程获取 Kaltura URL
           └─→ 下载视频文件
```

### 代码位置

#### API 调用（只获取元数据）
```python
# moodle_dl/moodle/core_handler.py:117
course_sections = self.client.post('core_course_get_contents', data)
# 返回: {"modname": "kalvidres", "url": "https://..."}
```

#### 识别 kalvidres 模块
```python
# moodle_dl/moodle/result_builder.py:86-88
if location['module_modname'] in ['kalvidres', 'helixmedia', 'lti']:
    location['module_modname'] = 'cookie_mod-' + location['module_modname']
    files += self._handle_cookie_mod(module_url, **location)
```

#### 提取页面文本（访问 HTML）
```python
# moodle_dl/downloader/task.py:700-774
async def extract_kalvidres_text(self, url: str, save_path: str):
    # 1. 访问页面 URL（需要 cookies）
    async with session.get(url, headers=self.RQ_HEADER) as response:
        html_content = await response.text()

    # 2. 提取 activity-description
    pattern = r'<div\s+class="activity-description"[^>]*>(.*?)</div>'
    match = re.search(pattern, html_content, re.DOTALL)

    # 3. 保存文本
    await self._save_kalvidres_text(text_data, save_path)
```

#### 下载视频（访问 HTML + LTI 流程）
```python
# moodle_dl/downloader/task.py:372-449
async def download_using_yt_dlp(self, dl_url: str, infos: HeadInfo):
    # yt-dlp 内部会：
    # 1. 访问页面 URL
    # 2. 提取 iframe
    # 3. 执行 LTI 认证
    # 4. 获取 Kaltura URL
    # 5. 下载视频
    ydl = yt_dlp.YoutubeDL(ydl_opts)
    ydl.download(dl_url)
```

---

## 对比：API vs 访问页面

| 内容 | Moodle API | 访问页面 HTML |
|------|-----------|--------------|
| **模块类型** | ✅ `modname: "kalvidres"` | ✅ |
| **页面 URL** | ✅ `url: "https://..."` | ✅ |
| **模块名称** | ✅ `name: "01-intro"` | ✅ |
| **Activity-description 文本** | ❌ 不提供 | ✅ 需要解析 HTML |
| **Kaltura 视频 URL** | ❌ 不提供 | ✅ 需要 LTI 流程 |
| **需要登录** | ✅ Token 认证 | ✅ Cookie 认证 |

---

## 为什么 API 不返回这些内容？

### 1. Activity-description 不在 API 设计范围内

Moodle Mobile API 主要返回：
- 课程结构（sections, modules）
- 文件列表（PDFs, 图片等）
- 基本元数据（名称、URL、图标）

**但不返回**：
- 页面 HTML 内容
- 模块内部的描述文本
- 动态生成的内容

### 2. Kaltura 视频需要 LTI 认证

Kaltura 集成使用 **LTI (Learning Tools Interoperability)** 协议：
- 需要动态生成 LTI launch 表单
- 需要签名和认证
- 视频 URL 是临时的、会话相关的

**Moodle API 不会**：
- 生成 LTI 认证 token
- 返回第三方平台的 URL
- 处理复杂的认证流程

### 3. 设计哲学

Moodle Mobile API 设计用于：
- ✅ 获取课程结构
- ✅ 下载静态文件（PDFs, 图片）
- ✅ 提供基本信息

**不是用于**：
- ❌ 渲染完整的页面内容
- ❌ 处理第三方集成（LTI）
- ❌ 执行复杂的认证流程

---

## 实际示例

### 示例 1: 获取 Errata 文本

#### ❌ 仅用 API（不可能）
```python
# Moodle API 调用
api_response = client.post('core_course_get_contents', {'courseid': 134647})

# API 返回
{
    "modules": [{
        "modname": "kalvidres",
        "url": "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619",
        "name": "01-intro"
    }]
}

# ❌ 没有 activity-description
# ❌ 没有 Errata 文本
```

#### ✅ 访问页面 HTML（正确方法）
```python
# 步骤 1: 从 API 获取 URL
url = api_response['modules'][0]['url']

# 步骤 2: 访问页面（需要 cookies）
response = requests.get(url, cookies=moodle_cookies)
html_content = response.text

# 步骤 3: 提取 activity-description
pattern = r'<div\s+class="activity-description"[^>]*>(.*?)</div>\s*</div>'
match = re.search(pattern, html_content, re.DOTALL)

# 步骤 4: 清理 HTML，提取文本
errata_text = clean_html_preserve_structure(match.group(1))

# 结果:
# **Errata:**
# • The pentium bug was actually discovered in 1994...
# • GCC on Macs is of course just an alias...
```

---

### 示例 2: 获取 Kaltura 视频

#### ❌ 仅用 API（不可能）
```python
# Moodle API 调用
api_response = client.post('core_course_get_contents', {'courseid': 134647})

# API 返回
{
    "modules": [{
        "modname": "kalvidres",
        "url": "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619"
    }]
}

# ❌ 没有 Kaltura 视频 URL
# ❌ 没有 iframe URL
# ❌ 没有 LTI launch URL
```

#### ✅ 访问页面 + LTI 流程（正确方法）
```python
# 这正是 yt-dlp 的 kalvidres_lti extractor 做的事情

# 步骤 1: 访问 kalvidres 页面
view_page = requests.get(url, cookies=moodle_cookies).text

# 步骤 2: 提取 iframe
iframe_url = extract('<iframe class="kaltura-player-iframe" src="([^"]+)">')

# 步骤 3-7: LTI 认证流程
# (省略详细步骤，见 KALVIDRES_PROCESSING_GUIDE.md)

# 最终结果:
# https://cdnapisec.kaltura.com/p/2368101/sp/236810100/playManifest/entryId/1_smw4vcpg/...
```

---

## 总结

### 问题回答

> 若只使用 Moodle Mobile API，仍能获得 activity-description 的文本（如 errata）但不能获取 Kaltura 视频？

**答案**：

❌ **都不能！**

| 内容 | 仅用 Moodle API | 必须访问页面 HTML |
|------|----------------|------------------|
| Activity-description 文本 | ❌ 不能 | ✅ 能 |
| Kaltura 视频 | ❌ 不能 | ✅ 能 |

### Moodle API 的作用

Moodle API 只用于：
1. ✅ **识别** kalvidres 模块（`modname: "kalvidres"`）
2. ✅ **获取** 页面 URL
3. ✅ **提供** 基本元数据（名称、图标）

### 要获取实际内容

**必须**访问页面 HTML：
1. ✅ 使用 API 提供的 URL
2. ✅ 带上有效的 Moodle cookies
3. ✅ 解析 HTML 提取内容

### moodle-dl 的工作方式

```
Moodle API
    ↓ (只提供 URL)
访问页面 HTML
    ├─→ 提取文本 (activity-description)
    └─→ 执行 LTI 流程 → 获取视频
```

---

**关键点**：
- Moodle API **只是起点**，提供 URL 和元数据
- 实际内容（文本和视频）**都需要访问页面 HTML**
- 两者都需要有效的认证（token 或 cookies）

这就是为什么 moodle-dl 需要：
1. ✅ Moodle API token（获取课程结构）
2. ✅ Browser cookies（访问需要登录的页面）
