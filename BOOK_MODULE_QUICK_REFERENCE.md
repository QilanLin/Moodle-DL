# Book 模块改进 - 快速参考

## 改进概览

将 Book 模块下载结构从数字ID组织改为**章节标题组织**，并实现**Print Book视频相对路径链接**。

```
改前：691946/index.html, 691947/PPTX, ...
改后：01 - Chapter 1 - Introduction/Chapter 1.html, Video 01.mp4, ...
      02 - Chapter 2 - Lifecycles/Chapter 2.html, Video 01.mp4, ...
```

## 关键变更

| 方面 | 之前 | 之后 |
|------|------|------|
| **文件夹名** | 数字ID (691946) | 章节标题 (01 - Chapter 1 - Introduction) |
| **视频位置** | 分散（一部分在章节中） | 统一在章节文件夹中 |
| **Kaltura处理** | 依赖result_builder自动提取 | 显式提取和转换 |
| **Print Book视频** | iframe标签嵌入 | 相对路径video标签链接 |
| **视频URL格式** | 原始lti_launch.php | 标准browseandembed格式 |

## 实现细节

### 新增方法 (3个)

```python
# 1. 从TOC获取章节标题
def _get_chapter_title_from_toc(chapter_id: str, toc: List[Dict]) -> str:
    # 从目录中查找对应的章节标题

# 2. 转换Kaltura URL到标准格式
def _convert_kaltura_url_to_kalvidres(url: str) -> Tuple[str, str]:
    # 从 /filter/kaltura/lti_launch.php?...entryid/1_xxxxx
    # 转换为 https://domain/browseandembed/index/media/entryid/1_xxxxx

# 3. 链接Print Book中的视频
def _create_linked_print_book_html(print_book_html: str, chapter_mapping: Dict) -> str:
    # 替换iframe为相对路径video标签
```

### 修改位置

| 位置 | 修改内容 |
|------|--------|
| 第120-124行 | 提取章节标题并创建文件夹名 |
| 第132行 | 设置chapter_content的filepath为章节文件夹 |
| 第151-189行 | 提取Kaltura视频并添加到contents |
| 第211-249行 | 使用新helper方法链接Print Book视频 |
| 第997-1115行 | 新增3个helper方法 |

## 数据流

### Mobile API 处理

```
book_contents[1:] (章节)
  ├─ 获取章节ID和标题
  ├─ 从TOC查找标题 (helper 1)
  ├─ 创建文件夹: "01 - Title"
  ├─ 下载章节HTML
  ├─ 提取Kaltura iframe
  ├─ 转换URL (helper 2)
  └─ 添加到chapter_content['contents']
       └─ result_builder处理下载
```

### Print Book 处理

```
print_book_html
  ├─ 查找所有Kaltura iframe
  ├─ 对每个iframe:
  │  ├─ 提取entry_id
  │  ├─ 在chapter_mapping中查找
  │  ├─ 生成相对路径: "NN - Title/Video XX.mp4"
  │  └─ 替换为video标签
  └─ 返回修改后的HTML
```

## 文件位置

```
moodle_dl/moodle/mods/book.py

Line 997-1021:   _get_chapter_title_from_toc()
Line 1023-1049:  _convert_kaltura_url_to_kalvidres()
Line 1051-1115:  _create_linked_print_book_html()
```

## 关键数据结构

### chapters_by_id
```python
{
  'chapter_id': {
    'title': '章节标题',
    'folder_name': '01 - 章节标题',
    'index': 1,
    'content': {...},  # 完整chapter_content对象
    'videos': [
      {
        'entry_id': '1_xxxxx',
        'filename': 'Video 01.mp4',
        'original_url': '...',
        'converted_url': '...'
      }
    ]
  }
}
```

### 相对路径映射
```python
entry_id_to_path = {
  '1_xxxxx': '01 - Chapter 1/Video 01.mp4',
  '1_yyyyy': '02 - Chapter 2/Video 01.mp4'
}
```

## 预期结果

### 目录结构
```
Week 1 - Software Engineering/
├── Week 1 - Software Engineering.html    ← Print Book
├── Table of Contents.html
├── 01 - Chapter 1 - Introduction/
│   ├── Chapter 1.html
│   ├── Video 01.mp4
│   ├── Video 02.mp4
│   └── Slides.pdf
├── 02 - Chapter 2 - Lifecycles/
│   ├── Chapter 2.html
│   ├── Video 01.mp4
│   └── Reading.pdf
└── 03 - Chapter 3 - ...
```

### Print Book HTML 片段
```html
<!-- 之前 -->
<iframe class="kaltura-player-iframe"
        src="/filter/kaltura/lti_launch.php?..."></iframe>

<!-- 之后 -->
<div class="kaltura-video-container">
  <video controls>
    <source src="01 - Chapter 1 - Introduction/Video 01.mp4" type="video/mp4">
  </video>
</div>
```

## 验证命令

```bash
# 检查文件夹结构
find . -maxdepth 1 -type d -name "[0-9][0-9] - *"

# 检查Print Book中的video标签
grep -c "<video controls" "*.html"

# 检查视频文件
find . -name "*.mp4" | wc -l

# 查看日志
grep "Chapter folder name" ~/.moodle-dl/MoodleDL.log
grep "Converted.*Kaltura" ~/.moodle-dl/MoodleDL.log
```

## 日志示例

```
📁 Chapter folder name: 01 - Chapter 1 - Introduction (ID: 691946)
🎬 Extracted Kaltura video 1: entry_id=1_xxxxx, filename=Video 01.mp4
✅ Converted Kaltura URL to kalvidres: entry_id=1_xxxxx
🎬 Found 3 Kaltura iframe(s) in print book to link
✅ Replaced iframe with linked video: 01 - Chapter 1 - Introduction/Video 01.mp4
✅ Converted 3 Kaltura iframe(s) to linked video tags in print book
✅ Created complete print book HTML with linked videos: Week 1 - ...html
```

## 错误排查

### 问题：仍然使用数字文件夹

```bash
# 检查
grep "_get_chapter_title_from_toc" ~/.moodle-dl/MoodleDL.log

# 原因：可能是chapter_title为空或使用了备选
```

### 问题：Print Book中仍有iframe

```bash
# 检查
grep "Converted.*Kaltura iframe" ~/.moodle-dl/MoodleDL.log

# 原因：可能是章节映射为空或iframe regex不匹配
```

### 问题：视频文件不存在

```bash
# 检查转换
grep "entry_id=" ~/.moodle-dl/MoodleDL.log

# 原因：Kaltura URL转换失败或result_builder未识别
```

## 配置

无需特殊配置，改进自动启用：
- 下载 book 时自动应用
- 不影响其他模块
- 与现有 config.json 兼容

## 性能

- **下载时间**：略增（多了Print Book处理和TOC查询）
- **存储空间**：相同或更少（避免视频重复）
- **用户体验**：大幅提升（更清晰的文件夹组织）

## 兼容性

- ✅ 与现有 Moodle API 兼容
- ✅ 不影响其他模块（forum, page, etc.)
- ✅ 向后兼容（可选特性）

## 已知限制

1. **TOC依赖**：如果TOC不完整，会使用备选名称
2. **相对路径**：在文件夹结构改变时可能失效
3. **孤立视频**：只在Print Book中的视频可能无法正确映射

## 下一步

1. ✅ 实现完成
2. ⏳ 测试验证（参考 BOOK_MODULE_TESTING_GUIDE.md）
3. ⏳ 性能测试（大型book）
4. ⏳ 集成验收测试

## 文档位置

- **改进计划**：BOOK_MODULE_IMPROVEMENT_PLAN.md
- **实现总结**：BOOK_MODULE_IMPROVEMENTS_SUMMARY.md
- **测试指南**：BOOK_MODULE_TESTING_GUIDE.md
- **快速参考**：本文件

## 代码位置

- **实现文件**：moodle_dl/moodle/mods/book.py
- **新方法**：第997-1115行
- **修改流程**：第56-249行

---

**实现日期**：2025年11月9日
**状态**：✅ 代码实现完成，待测试验证
