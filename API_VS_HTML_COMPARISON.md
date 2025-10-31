# Moodle API vs 访问页面 HTML 对比

## 快速答案

| 获取内容 | 仅用 Moodle API | 必须访问页面 HTML |
|---------|----------------|------------------|
| **模块类型识别** | ✅ `modname: "kalvidres"` | ✅ |
| **页面 URL** | ✅ | ✅ |
| **Activity-description 文本** | ❌ | ✅ 需要解析 HTML |
| **Kaltura 视频** | ❌ | ✅ 需要 LTI 流程 |

---

## 详细对比

### 方式 1: 仅使用 Moodle API ❌

```python
# API 调用
response = client.post('core_course_get_contents', {'courseid': 134647})

# 得到什么？
{
    "modname": "kalvidres",  # ✅ 知道是 kalvidres
    "url": "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619",  # ✅ 有 URL
    "name": "01-intro"  # ✅ 有名称
}

# 得不到什么？
# ❌ 没有 activity-description
# ❌ 没有 Errata 文本
# ❌ 没有 Kaltura 视频 URL
```

### 方式 2: API + 访问页面 HTML ✅

```python
# 步骤 1: 从 API 获取 URL
api_data = client.post('core_course_get_contents', ...)
url = api_data['modules'][0]['url']  # ✅ 从 API 获取

# 步骤 2: 访问页面（需要 cookies）
response = requests.get(url, cookies=moodle_cookies)
html = response.text

# 步骤 3: 提取 activity-description
pattern = r'<div class="activity-description"[^>]*>(.*?)</div>\s*</div>'
match = re.search(pattern, html, re.DOTALL)
text = clean_html(match.group(1))  # ✅ 获得 Errata 文本

# 步骤 4: 提取 Kaltura 视频（通过 yt-dlp）
# - 提取 iframe URL
# - 执行 LTI 认证
# - 获取 Kaltura URL  # ✅ 获得视频
```

---

## 流程图

```
┌─────────────────────────────────────────────────────────┐
│ Moodle Mobile API (core_course_get_contents)           │
├─────────────────────────────────────────────────────────┤
│ 输入: courseid=134647                                   │
│                                                         │
│ 输出:                                                   │
│   ✅ modname: "kalvidres"  (识别模块类型)               │
│   ✅ url: "https://..."    (页面地址)                   │
│   ✅ name: "01-intro"      (模块名称)                   │
│                                                         │
│   ❌ 没有页面 HTML 内容                                 │
│   ❌ 没有 activity-description                          │
│   ❌ 没有视频 URL                                       │
└─────────────────────────────────────────────────────────┘
                        ↓
                        ↓ (使用 API 返回的 URL)
                        ↓
┌─────────────────────────────────────────────────────────┐
│ 访问页面 HTML (GET + cookies)                           │
├─────────────────────────────────────────────────────────┤
│ 输入: url (from API) + moodle_cookies                   │
│                                                         │
│ 输出: 完整的 HTML 页面                                  │
│   ├─→ <title>intro video | KEATS</title>               │
│   ├─→ <h1>intro video (26 mins)</h1>                   │
│   ├─→ <div class="activity-description">               │
│   │       <p><b>Errata:</b></p>                        │
│   │       <ul>                                          │
│   │         <li>The pentium bug...</li>                │
│   │       </ul>                                         │
│   │   </div>                                            │
│   └─→ <iframe class="kaltura-player-iframe"            │
│           src="https://...lti/launch..."></iframe>     │
└─────────────────────────────────────────────────────────┘
           ↓                           ↓
           ↓                           ↓
  ┌────────────────┐         ┌─────────────────┐
  │ 提取文本       │         │ 提取视频        │
  ├────────────────┤         ├─────────────────┤
  │ 1. 找到 div    │         │ 1. 提取 iframe  │
  │    activity-   │         │    URL          │
  │    description │         │                 │
  │                │         │ 2. 访问 LTI     │
  │ 2. 提取 HTML   │         │    launch       │
  │                │         │                 │
  │ 3. 转换为      │         │ 3. 提交表单     │
  │    Markdown    │         │                 │
  │                │         │ 4. 获取         │
  │ 4. 保存        │         │    Kaltura URL  │
  │    *_notes.md  │         │                 │
  │                │         │ 5. 下载视频     │
  └────────────────┘         └─────────────────┘
         ✅                          ✅
```

---

## moodle-dl 的实际实现

### 代码位置

#### 1. API 调用（获取 URL）
```python
# moodle_dl/moodle/core_handler.py:117
course_sections = self.client.post('core_course_get_contents', data)
```

#### 2. 识别 kalvidres
```python
# moodle_dl/moodle/result_builder.py:86-88
if location['module_modname'] in ['kalvidres', 'helixmedia', 'lti']:
    location['module_modname'] = 'cookie_mod-' + location['module_modname']
    files += self._handle_cookie_mod(module_url, **location)
    # module_url 来自 API 返回的 URL
```

#### 3. 访问页面提取文本
```python
# moodle_dl/downloader/task.py:700-774
async def extract_kalvidres_text(self, url: str, save_path: str):
    # url 来自 API
    async with session.get(url, headers=self.RQ_HEADER) as response:
        html_content = await response.text()  # ✅ 获取 HTML

    # 提取 activity-description
    pattern = r'<div\s+class="activity-description"[^>]*>(.*?)</div>'
    match = re.search(pattern, html_content, re.DOTALL)  # ✅ 解析 HTML
```

#### 4. 下载视频
```python
# moodle_dl/downloader/task.py:372-449
async def download_using_yt_dlp(self, dl_url: str, ...):
    # dl_url 来自 API
    ydl = yt_dlp.YoutubeDL(ydl_opts)
    ydl.download(dl_url)  # yt-dlp 内部访问 HTML + LTI 流程
```

---

## 为什么 API 不返回这些内容？

### 1. Activity-description 是页面内容

- Moodle API 设计用于返回**结构化数据**（JSON）
- 页面 HTML 内容**不是结构化数据**
- API 不会返回模块内部的 HTML

### 2. Kaltura 视频需要 LTI 认证

- Kaltura 使用 **LTI (Learning Tools Interoperability)** 协议
- 需要动态生成认证 token
- 视频 URL 是临时的、会话相关的
- API 不会处理第三方平台的认证

### 3. API 的设计目的

Moodle Mobile API 用于：
- ✅ 获取课程结构
- ✅ 列出资源和活动
- ✅ 下载静态文件（PDFs）

**不是用于**：
- ❌ 渲染页面内容
- ❌ 处理 LTI 集成
- ❌ 访问第三方平台

---

## 实际示例

### 示例：获取某个 kalvidres 模块

#### 第 1 步：API 调用
```bash
POST /webservice/rest/server.php
wsfunction=core_course_get_contents
wstoken=451d8ccfcac580505c984527356d9f67
courseid=134647
```

#### 第 2 步：API 返回（JSON）
```json
[
  {
    "id": 123,
    "name": "Week 1 (Introduction)",
    "modules": [
      {
        "id": 9159619,
        "url": "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619",
        "name": "intro video (26 mins)",
        "modname": "kalvidres",
        "modplural": "Kaltura Video Resource",
        "indent": 0,
        "onclick": "",
        "afterlink": null,
        "customdata": "",
        "noviewlink": false,
        "completion": 0
      }
    ]
  }
]
```

**✅ 从 API 得到**：
- 模块类型: `kalvidres`
- 页面 URL: `https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619`
- 模块名称: `intro video (26 mins)`

**❌ 从 API 得不到**：
- Errata 文本
- 视频 URL

---

#### 第 3 步：访问页面 HTML
```bash
GET https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619
Cookie: MoodleSession=xxx; buid=xxx; ...
```

#### 第 4 步：HTML 响应（227KB）
```html
<!DOCTYPE html>
<html>
<head>
    <title>intro video (26 mins) | KEATS</title>
</head>
<body>
    <div id="region-main">
        <h1>intro video (26 mins)</h1>

        <!-- ✅ Activity-description（文本内容） -->
        <div class="activity-description" id="intro">
            <div class="no-overflow">
                <p><b>Errata:</b></p>
                <ul>
                    <li>The pentium bug was actually discovered in 1994...</li>
                    <li>GCC on Macs is of course just an alias...</li>
                </ul>
            </div>
        </div>

        <!-- ✅ Kaltura iframe（视频入口） -->
        <div class="kaltura-player-container">
            <iframe class="kaltura-player-iframe"
                    src="https://keats.kcl.ac.uk/mod/lti/launch.php?id=9159619">
            </iframe>
        </div>
    </div>
</body>
</html>
```

**✅ 从 HTML 得到**：
- Activity-description 的完整 HTML
- Errata 文本
- iframe URL（视频入口）

---

#### 第 5 步：LTI 流程（获取视频）
```
iframe URL → LTI launch → 提交表单 → 重定向 → Kaltura URL
```

最终获得：
```
https://cdnapisec.kaltura.com/p/2368101/sp/236810100/playManifest/
entryId/1_smw4vcpg/format/applehttp/protocol/https/...
```

---

## 总结表格

| 数据 | 来源 | 需要 |
|------|------|------|
| **模块类型** (`kalvidres`) | ✅ Moodle API | Token |
| **页面 URL** | ✅ Moodle API | Token |
| **模块名称** | ✅ Moodle API | Token |
| **页面标题** | ❌ 需访问 HTML | Cookies |
| **Activity-description** | ❌ 需访问 HTML | Cookies |
| **Errata 文本** | ❌ 需访问 HTML | Cookies |
| **iframe URL** | ❌ 需访问 HTML | Cookies |
| **Kaltura 视频 URL** | ❌ 需访问 HTML + LTI | Cookies |

---

## 关键要点

### Moodle API 的作用

```
Moodle API 只是"目录"，告诉你：
  - 这个模块是什么类型（kalvidres）
  - 这个模块在哪里（URL）
  - 这个模块叫什么（name）

但不告诉你：
  - 模块里面有什么内容（需要访问页面）
  - 视频在哪里（需要 LTI 认证）
```

### 必须访问页面 HTML

```
API 提供的 URL → 访问页面 HTML → 提取内容

内容包括：
  ✅ Activity-description（文本）
  ✅ Kaltura 视频（通过 LTI）
```

### moodle-dl 的策略

```
1. 使用 API 获取课程结构（快速、高效）
2. 识别需要特殊处理的模块（kalvidres）
3. 访问页面 HTML 获取实际内容（文本 + 视频）
```

---

**最终答案**：

仅使用 Moodle Mobile API **无法获取** activity-description 文本和 Kaltura 视频。

必须：
1. 用 API 获取 URL
2. 访问 HTML 提取内容
