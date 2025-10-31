# Moodle Mobile API 对不同模块类型的支持

## 你的问题

> 所以只要识别到不支持的模块类型（modname: "kalvidres"），moodle mobile api 就不能返回有效内容了？

**答案：不是！** ❌

这取决于**模块的类型**，不是简单的"支持"或"不支持"。

---

## 三种模块类型

### 类型 1: 文件类模块 ✅ API 完全支持

**特点**：API 返回 `contents` 数组，包含**直接可下载的文件 URL**

#### 示例：`resource` (文件)

**API 返回**：
```json
{
  "modname": "resource",
  "name": "Expected Behaviour",
  "contents": [
    {
      "type": "file",
      "filename": "Expectations of behaviour.pdf",
      "fileurl": "https://keats.kcl.ac.uk/webservice/pluginfile.php/12842648/mod_resource/content/1/Expectations%20of%20behaviour.pdf",
      "filesize": 227430,
      "mimetype": "application/pdf"
    }
  ]
}
```

**✅ API 提供**：
- 文件名
- 直接下载 URL
- 文件大小
- MIME 类型

**✅ moodle-dl 可以**：
- 直接下载文件
- 无需访问页面

**其他类似模块**：
- `resource` (文件)
- `folder` (文件夹)
- 部分 `url` (如果指向文件)

---

### 类型 2: 页面/活动类模块 ⚠️ API 部分支持

**特点**：API 可能返回 `description` 字段，包含**部分 HTML 内容**

#### 示例：`label` (标签)

**API 返回**：
```json
{
  "modname": "label",
  "name": "RESOURCES [MANDATORY]",
  "description": "<div class=\"no-overflow\"><p>Please read carefully...</p></div>"
}
```

**⚠️ API 提供**：
- 基本描述信息
- 部分 HTML 内容
- **但可能不完整**

**⚠️ 局限性**：
- 不是完整页面内容
- 可能缺少动态生成的内容
- 可能缺少嵌入的资源

**其他类似模块**：
- `label` (标签)
- `quiz` (测验 - 只有描述，不是题目)
- `page` (页面 - 可能只有摘要)

---

### 类型 3: 外部集成/LTI 模块 ❌ API 不支持内容

**特点**：API **只返回 URL**，不返回任何实际内容

#### 示例：`kalvidres` (Kaltura 视频)

**API 返回**：
```json
{
  "id": 9159619,
  "modname": "kalvidres",
  "name": "intro video (26 mins)",
  "url": "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619",
  "modplural": "Kaltura Video Resource"

  // ❌ 没有 contents
  // ❌ 没有 description
  // ❌ 没有视频 URL
  // ❌ 没有页面文本
}
```

**❌ API 不提供**：
- 页面内容
- 视频 URL
- Activity-description
- 任何实际内容

**❌ 必须访问页面**：
- 获取 activity-description
- 执行 LTI 认证
- 获取视频 URL

**其他类似模块**：
- `kalvidres` (Kaltura 视频)
- `helixmedia` (Helix 媒体)
- `lti` (LTI 工具)
- 部分 `url` (指向外部网站)

---

## 对比表格

| 模块类型 | API 返回 `contents` | API 返回 `description` | 需要访问页面 | 示例 |
|---------|-------------------|----------------------|------------|------|
| **文件类** | ✅ 有，含下载 URL | ❌ 无 | ❌ 不需要 | `resource`, `folder` |
| **页面类** | ❌ 无 | ⚠️ 有，但可能不完整 | ⚠️ 建议访问 | `label`, `page`, `quiz` |
| **外部/LTI** | ❌ 无 | ❌ 无 | ✅ 必须访问 | `kalvidres`, `helixmedia`, `lti` |

---

## 实际 API 响应对比

### ✅ 文件类模块（`resource`）

```json
{
  "modname": "resource",
  "name": "Expected Behaviour",
  "url": "https://keats.kcl.ac.uk/mod/resource/view.php?id=8992305",
  "contents": [  // ✅ 关键！包含文件信息
    {
      "type": "file",
      "filename": "Expectations of behaviour.pdf",
      "filepath": "/",
      "filesize": 227430,
      "fileurl": "https://keats.kcl.ac.uk/webservice/pluginfile.php/12842648/mod_resource/content/1/Expectations%20of%20behaviour.pdf",
      "timecreated": 1758207379,
      "timemodified": 1758207386,
      "mimetype": "application/pdf",
      "isexternalfile": false
    }
  ]
}
```

**✅ 可以直接下载**：
```python
file_url = module['contents'][0]['fileurl']
download(file_url)  # 直接下载，无需访问页面
```

---

### ⚠️ 页面类模块（`label`）

```json
{
  "modname": "label",
  "name": "RESOURCES [MANDATORY]",
  "url": "https://keats.kcl.ac.uk/mod/label/view.php?id=8992304",
  "description": "<div class=\"no-overflow\"><p>Please read carefully the documents below...</p></div>"
  // ⚠️ 有 description，但可能不完整
}
```

**⚠️ 可以使用 description**：
```python
description = module['description']
# 但这可能不是完整的页面内容
```

---

### ❌ 外部/LTI 模块（`kalvidres`）

```json
{
  "modname": "kalvidres",
  "name": "intro video (26 mins)",
  "url": "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619",
  "modplural": "Kaltura Video Resource",
  "visible": 1,
  "completion": 1

  // ❌ 没有 contents
  // ❌ 没有 description
  // ❌ 只有 URL
}
```

**❌ 必须访问页面**：
```python
url = module['url']
html = requests.get(url, cookies=moodle_cookies).text
# 然后才能提取内容
```

---

## moodle-dl 的处理策略

### 策略 1: 文件类模块（直接下载）

```python
# moodle_dl/moodle/result_builder.py:90-91
elif location['module_modname'].startswith(('resource', 'akarifolder', 'url')):
    files += self._handle_files(module_contents, **location)
    # module_contents 来自 API 的 contents 数组
```

**流程**：
```
API 返回 contents → 提取 fileurl → 直接下载 ✅
```

---

### 策略 2: 页面类模块（使用 description）

```python
# moodle_dl/moodle/result_builder.py:82-84
if module_description is not None:
    files += self._handle_description(module_description, **location)
    # module_description 来自 API 的 description 字段
```

**流程**：
```
API 返回 description → 保存为文件 ✅
```

---

### 策略 3: 外部/LTI 模块（访问页面）

```python
# moodle_dl/moodle/result_builder.py:86-88
if location['module_modname'] in ['kalvidres', 'helixmedia', 'lti']:
    location['module_modname'] = 'cookie_mod-' + location['module_modname']
    files += self._handle_cookie_mod(module_url, **location)
    # 创建任务，稍后访问页面
```

**流程**：
```
API 返回 URL → 访问页面 → 提取内容 ✅
```

---

## 关键区别

### 文件类模块

**API 返回**：
```json
{
  "contents": [
    {
      "fileurl": "https://keats.kcl.ac.uk/webservice/pluginfile.php/..."
    }
  ]
}
```

**特点**：
- ✅ 有 `contents` 数组
- ✅ 包含 `fileurl`
- ✅ 可以直接下载

---

### LTI/外部模块

**API 返回**：
```json
{
  "url": "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=..."
}
```

**特点**：
- ❌ 没有 `contents`
- ❌ 只有页面 `url`
- ❌ 必须访问页面

---

## 为什么会有这种差异？

### 文件类模块

- Moodle 直接管理文件
- 文件存储在 Moodle 服务器
- API 可以生成直接下载 URL
- **Moodle 知道文件的一切信息**

### LTI/外部模块

- 内容由**第三方平台**提供（如 Kaltura）
- Moodle 只是一个**入口/桥梁**
- 需要复杂的认证流程
- **Moodle 不知道实际内容**

---

## 实际测试

### 测试 1: `resource` 模块（文件）

```python
import requests

api_response = {
    "modname": "resource",
    "contents": [
        {
            "fileurl": "https://keats.kcl.ac.uk/webservice/pluginfile.php/12842648/mod_resource/content/1/Expectations%20of%20behaviour.pdf"
        }
    ]
}

# ✅ 可以直接下载
file_url = api_response['contents'][0]['fileurl']
file_data = requests.get(file_url + f'?token={token}').content
# 成功！
```

---

### 测试 2: `kalvidres` 模块（LTI）

```python
api_response = {
    "modname": "kalvidres",
    "url": "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619"
    # ❌ 没有 contents
    # ❌ 没有 fileurl
}

# ❌ 无法直接下载
# 必须访问页面：
html = requests.get(api_response['url'], cookies=cookies).text
# 然后提取 iframe
# 然后执行 LTI 认证
# 然后获取 Kaltura URL
# 然后才能下载视频
```

---

## 总结

### 问题回答

> 所以只要识别到不支持的模块类型（modname: "kalvidres"），moodle mobile api 就不能返回有效内容了？

**答案：不完全正确**

正确理解：
1. ✅ **文件类模块**：API 返回完整的文件信息和下载 URL
2. ⚠️ **页面类模块**：API 返回部分内容（description）
3. ❌ **LTI/外部模块**：API 只返回 URL，不返回内容

### 判断标准

| 判断依据 | 含义 |
|---------|------|
| 有 `contents` 数组 | ✅ API 支持，可直接下载 |
| 有 `description` 字段 | ⚠️ API 部分支持 |
| 只有 `url` 字段 | ❌ API 不支持，必须访问页面 |

### moodle-dl 的策略

```
if has_contents:
    直接下载 ✅
elif has_description:
    使用 description ⚠️
else:
    访问页面 URL ❌
```

---

**关键点**：

不是"支持"或"不支持"的简单二分法，而是：
1. **完全支持**（文件类）- 有 `contents` 和 `fileurl`
2. **部分支持**（页面类）- 有 `description`
3. **不支持内容**（LTI/外部）- 只有 `url`

`kalvidres` 属于第三类，但 `resource` 属于第一类，所以**不能一概而论**。
