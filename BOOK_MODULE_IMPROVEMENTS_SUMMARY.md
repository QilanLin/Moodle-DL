# Book 模块改进总结

## 实现完成

我已经成功为 Moodle-DL 的 book 模块实现了重大改进，目标是实现期望的目录结构：

```
Week 1 - Software Engineering/
├── Week 1 - Software Engineering and Software Lifecycles.html    ← Print Book（所有章节合并）
├── 01 - Chapter 1 - Introduction/
│   ├── Chapter 1.html
│   ├── Video 01.mp4
│   ├── Slides.pdf
│   └── Exercise.zip
├── 02 - Chapter 2 - Lifecycles/
│   ├── Chapter 2.html
│   ├── Video 02.mp4
│   └── Reading.pdf
└── Table of Contents.html
```

## 🎯 Mobile API 优先原则

本实现遵循以下核心原则：

> **能用 Moodle Mobile API 完成的就尽量用 Mobile API 完成**

### 原则应用

| 内容 | 获取方式 | 来源 | 说明 |
|------|--------|------|------|
| 章节列表 | Mobile API | `core_course_get_contents` | ✅ 官方 Web Service |
| 章节 HTML | Mobile API | API 返回的 `content` 字段 + `fileurl` | ✅ API 数据 |
| Kaltura 视频 | 从 API 返回的 HTML 中提取 | Mobile API HTML 内容 | ✅ API 派生 |
| 章节附件 | result_builder 自动提取 | API HTML 中的 URL | ✅ API 派生 |
| 文件夹结构 | 由 API 数据确定 | chapter ID + TOC 标题 | ✅ API 数据 |
| **Print Book HTML** | **Playwright** | **不在 Mobile API 中** | ✅ **合理使用** |

### 为什么 Playwright 仅用于 Print Book

Print Book 功能不在 Moodle Mobile API 列表中，因此：
- ✅ 可以合理使用 Playwright（web 爬虫）获取 Print Book HTML
- ✅ 但 Print Book 获取后立即修改为相对路径，指向已通过 API 下载的本地文件
- ✅ 所以 Print Book 也**间接依赖 Mobile API**（链接指向 API 下载的内容）

### 数据流向

```
Mobile API (core_course_get_contents)
  ├─ Chapter 1 HTML → 01 - Chapter 1/Chapter 1.html
  ├─ Chapter 1 Videos → 01 - Chapter 1/Video.mp4
  ├─ Chapter 2 HTML → 02 - Chapter 2/Chapter 2.html
  ├─ Chapter 2 Videos → 02 - Chapter 2/Video.mp4
  └─ ...所有章节内容...
       ↓
Playwright (获取 Print Book)
  ├─ 获取 Print Book HTML
  ├─ 修改 iframe → <video src="01 - Chapter 1/Video.mp4">
  └─ 保存为 Book Name.html
       ↓
结果：书籍、章节、视频全部组织完毕
```

---

## 关键改进

### 1. 章节文件夹结构改进

**之前**：章节用数字ID作为文件夹名
- `691946/index.html`
- `691947/ISE-week01-Overview.pptx`

**之后**：章节用标题创建易识别的文件夹名
- `01 - Chapter 1 - Introduction/Chapter 1.html`
- `02 - Chapter 2 - Lifecycles/Chapter 2.html`

**实现位置**：`moodle_dl/moodle/mods/book.py` 第120-124行
```python
# 从TOC获取章节标题，用于创建文件夹名
chapter_title = self._get_chapter_title_from_toc(chapter_id, book_toc)
# 格式化文件夹名：添加序号并清理路径
chapter_folder_name = PT.path_to_absolute_os_path(f'{chapter_count:02d} - {chapter_title}')
```

### 2. 统一 Kaltura 视频处理

**之前**：
- 视频只在章节中通过一般的URL提取被处理
- Print Book中的视频嵌入iframe，未能和章节视频关联

**之后**：
- 所有章节中的 Kaltura 视频使用统一的转换逻辑
- URL 转换为标准的 `browseandembed` 格式（与其他模块一致）
- 视频保存在对应章节的文件夹中
- 使用 `entry_id` 作为唯一标识符

**实现位置**：`moodle_dl/moodle/mods/book.py`

新方法 `_convert_kaltura_url_to_kalvidres()` (第1023-1049行)：
```python
# 从 /filter/kaltura/lti_launch.php?...entryid/1_xxxxx...
# 转换为 https://moodle.domain/browseandembed/index/media/entryid/1_xxxxx
```

章节处理中的应用（第151-189行）：
```python
# 提取该章节中的Kaltura视频并转换URL
for idx, iframe_src in enumerate(matches, 1):
    iframe_src = html.unescape(iframe_src)
    # 转换URL到标准格式
    converted_url, entry_id = self._convert_kaltura_url_to_kalvidres(iframe_src)
    # 添加到contents数组
    chapter_content['contents'].append({
        'filename': video_filename,
        'filepath': f'/{chapter_folder_name}/',
        'fileurl': converted_url,
        ...
    })
```

### 3. Print Book HTML 链接优化

**之前**：
- Print Book HTML 包含所有嵌入式 Kaltura iframe
- 视频通过完整的 `<iframe>` 标签嵌入

**之后**：
- Print Book HTML 中的 iframe 被替换为指向相对路径的 HTML5 `<video>` 标签
- 相对路径指向对应章节文件夹中的视频文件
- 例如：`<source src="01 - Chapter 1 - Introduction/Video 01.mp4">`
- 避免重复下载和节省存储空间

**实现位置**：`moodle_dl/moodle/mods/book.py` 第1051-1115行

新方法 `_create_linked_print_book_html()` 功能：
1. 建立 `entry_id` 到相对路径的映射
2. 查找 Print Book 中所有 Kaltura iframe
3. 替换为 HTML5 video 标签，使用相对路径链接

使用示例（第224-228行）：
```python
modified_print_book_html = self._create_linked_print_book_html(
    print_book_html,
    chapter_mapping_for_print_book
)
```

### 4. 文件组织改进

**新增辅助方法**：

1. `_get_chapter_title_from_toc(chapter_id, toc)` (第997-1021行)
   - 从 TOC（目录）中查找章节标题
   - 支持递归搜索子章节
   - 作为备选返回默认名称

2. `_convert_kaltura_url_to_kalvidres(url)` (第1023-1049行)
   - 统一转换 Kaltura LTI launch URL
   - 提取 entry_id
   - 转换为标准的 kalvidres 格式
   - 与 result_builder.py 中的逻辑一致

3. `_create_linked_print_book_html(print_book_html, chapter_mapping)` (第1051-1115行)
   - 替换 iframe 为链接的 video 标签
   - 构建 entry_id 到路径的映射
   - 生成相对路径的 video 标签

## 数据流改进

### 旧流程
```
Mobile API
  └─ 章节HTML (含Kaltura iframe)
       └─ result_builder 自动URL提取
            └─ kalvidres下载器处理视频

Print Book API
  └─ 完整HTML (含Kaltura iframe)
       └─ 保存原始HTML
```

### 新流程
```
Mobile API
  ├─ 获取章节HTML和附件
  ├─ 提取Kaltura视频 → 转换URL → 添加到chapter contents
  ├─ 创建章节文件夹：02 - Chapter 2 - Lifecycles/
  ├─ 组织所有文件到章节文件夹
  └─ 建立 chapter_id → {title, folder_name, videos[]}的映射

Print Book API
  ├─ 获取完整Print Book HTML
  ├─ 使用章节映射链接视频到相对路径
  │   (entry_id → "02 - Chapter 2 - Lifecycles/Video 01.mp4")
  ├─ 替换iframe为HTML5 video标签
  └─ 保存修改后的HTML
```

## 代码位置变更

### 修改的文件
- `moodle_dl/moodle/mods/book.py`

### 关键代码位置

| 功能 | 位置 | 行数 |
|------|------|------|
| 章节标题提取 | `_get_chapter_title_from_toc()` | 997-1021 |
| Kaltura URL转换 | `_convert_kaltura_url_to_kalvidres()` | 1023-1049 |
| Print Book HTML链接 | `_create_linked_print_book_html()` | 1051-1115 |
| 章节文件夹创建 | 主流程 | 120-124 |
| 章节Kaltura提取 | 主流程 | 151-189 |
| Print Book处理 | 主流程 | 211-249 |

## 文件结构变更

### 章节映射数据结构

```python
chapters_by_id = {
    '691946': {
        'title': 'Chapter 1 - Introduction',
        'folder_name': '01 - Chapter 1 - Introduction',
        'index': 1,
        'content': {...},  # 完整的chapter_content对象
        'videos': [
            {
                'entry_id': '1_xxxxx',
                'filename': 'Video 01.mp4',
                'original_url': '/filter/kaltura/lti_launch.php?...',
                'converted_url': 'https://moodle.domain/browseandembed/...'
            },
            ...
        ]
    },
    ...
}
```

### Print Book 视频映射

```python
entry_id_to_path = {
    '1_xxxxx': '01 - Chapter 1 - Introduction/Video 01.mp4',
    '1_yyyyy': '02 - Chapter 2 - Lifecycles/Video 01.mp4',
    ...
}
```

## 优势

1. **更好的用户体验**：
   - 文件夹名清晰易识别，而不是数字ID
   - 所有章节相关文件组织在一个文件夹中
   - 符合直观的教科书式组织

2. **一致的视频处理**：
   - Kaltura 视频处理方式与其他模块一致
   - 使用标准的 URL 转换逻辑
   - 便于维护和调试

3. **节省存储空间**：
   - 避免在 Print Book HTML 中重新嵌入 iframe 视频
   - 视频只在章节文件夹中存储一份
   - Print Book 直接链接到已下载的视频

4. **更好的 Print Book 可用性**：
   - Print Book HTML 包含本地视频链接
   - 在离线环境中仍可播放视频
   - 相对路径链接避免绝对路径问题

5. **提高代码可维护性**：
   - 新增的 helper 方法有清晰的责任
   - 逻辑分离，便于单元测试
   - 易于扩展（例如支持其他视频格式）

## 兼容性

- 仅修改了 book 模块，不影响其他模块
- 向后兼容的改进（Print Book 是可选的）
- 使用的 API 和库与现有代码一致

## 测试建议

1. **基本功能测试**：
   - 验证章节文件夹名称是否正确
   - 确认视频保存在正确的章节文件夹中
   - 检查 Print Book HTML 中的相对路径是否有效

2. **视频链接测试**：
   - 在浏览器中打开 Print Book HTML
   - 验证视频是否正确播放
   - 检查相对路径是否工作正常

3. **数据完整性测试**：
   - 验证所有章节都被处理
   - 确认所有视频都被转换
   - 检查是否有遗漏的文件

## 已知限制

1. **Print Book 视频映射的局限性**：
   - 如果视频只在 Print Book 中出现，不在章节中，可能无法正确映射
   - 解决方案：为这种情况添加后备路径或创建 "Other Videos" 文件夹

2. **TOC 标题依赖**：
   - 如果 TOC 不包含完整的章节标题信息，会使用备选名称
   - 这不影响功能，但文件夹名可能不够描述性

3. **相对路径限制**：
   - 相对路径链接仅在文件夹结构保持不变时有效
   - 如果用户移动 Print Book HTML 文件，链接可能失效

## 未来改进方向

1. **支持多种视频格式**：
   - 扩展 `_convert_kaltura_url_to_kalvidres()` 支持 HelixMedia 等
   - 自动检测视频格式并适应

2. **更智能的文件夹命名**：
   - 基于内容分析的自动分类
   - 支持自定义命名规则

3. **Print Book 优化**：
   - 压缩 Print Book HTML（移除不必要的脚本和样式）
   - 添加导航快捷方式

4. **离线功能增强**：
   - 自动检测和修复相对路径问题
   - 生成 offline index 便于导航

## 完成日期

2025年11月9日

## 验证清单

- [x] 添加了3个新的 helper 方法
- [x] 修改了主流程中的章节处理
- [x] 修改了 Print Book 的处理
- [x] 代码通过语法检查
- [x] 保持了向后兼容性
- [x] 文档已更新
