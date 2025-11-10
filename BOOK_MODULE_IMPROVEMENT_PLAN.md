# Book 模块改进计划

## 目标结构

```
Week 1 - Software Engineering/
├── Week 1 - Software Engineering and Software Lifecycles.html    ← Print Book（所有章节合并）
├── Chapter 1 - Introduction/
│   ├── Chapter 1.html                                           ← 章节HTML
│   ├── Video 01.mp4                                             ← 嵌入式Kaltura视频
│   ├── Slides.pdf                                               ← 附件
│   └── Exercise.zip
├── Chapter 2 - Lifecycles/
│   ├── Chapter 2.html
│   ├── Video 02.mp4
│   └── Reading.pdf
└── Table of Contents.html
```

## 关键改进点

### 1. 章节文件夹命名
- **当前**: 使用数字ID作为文件夹（691946, 691947等）
- **目标**: 使用章节标题作为文件夹名（Chapter 1 - Introduction, Chapter 2 - Lifecycles等）

### 2. Kaltura视频处理
- **当前**: Print Book中嵌入完整的iframe，视频单独处理
- **目标**:
  - 从章节HTML中提取Kaltura视频，使用统一的转换逻辑
  - 视频保存到各章节的文件夹中
  - Print Book中的视频替换为指向相对路径的video标签（如 `Chapter 1 - Introduction/Video 01.mp4`）

### 3. 处理流程变更

#### 当前流程
```
Mobile API → 处理章节 → 生成Print Book → 提取视频（未完成）
```

#### 改进流程
```
Mobile API
  ├─ 获取章节列表和章节HTML
  ├─ 为每个章节创建文件夹（使用标题）
  ├─ 提取并转换Kaltura视频（统一逻辑）
  ├─ 组织附件到章节文件夹
  └─ 生成TOC

Print Book API
  ├─ 获取完整Print Book HTML
  ├─ 提取章节-视频映射
  └─ 替换iframe为相对路径video标签
```

## 实现细节

### 步骤1: 提取章节标题

从Mobile API返回的数据中获取章节标题。可能的来源：
1. `chapter_content.get('filename')` - 例如 "691946/index.html" (需要从对应的TOC中查找)
2. TOC (Table of Contents) 中的 `title` 字段

建议使用TOC中的标题，因为它更清晰。

### 步骤2: 统一Kaltura转换

复用 result_builder.py 中的逻辑：
```python
# 检测和转换Kaltura URLs
if '/filter/kaltura/lti_launch.php' in url:
    entry_id = 从URL提取entry_id
    url = f'https://{moodle_domain}/browseandembed/index/media/entryid/{entry_id}'
    module_modname = 'cookie_mod-kalvidres'
```

### 步骤3: Print Book处理

1. 下载Print Book HTML（已有）
2. 提取章节-视频映射（已有 `_extract_chapter_video_mapping_from_print_book`）
3. **新增**: 替换iframe为相对路径video标签

```python
# Print Book中的视频iframe src: /filter/kaltura/lti_launch.php?...
# 替换为: Chapter 1 - Introduction/Video 01.mp4
# 对应关系来自之前下载的章节信息
```

## 代码修改位置

### moodle_dl/moodle/mods/book.py

1. **新增方法**: `_get_chapter_title_for_folder(chapter_id)`
   - 从TOC或Mobile API数据中获取格式化的章节标题
   - 用于创建文件夹名

2. **新增方法**: `_convert_kaltura_url(url, entry_id)`
   - 统一的Kaltura URL转换逻辑
   - 便于复用

3. **新增方法**: `_convert_print_book_with_relative_links(html, chapter_mapping, downloaded_videos)`
   - 替换Print Book中的iframe为相对路径video标签
   - 需要知道哪个视频属于哪个章节

4. **修改**: `real_fetch_mod_entries()` 主流程
   - 在处理章节时立即创建文件夹结构
   - 提取和转换视频时设置正确的filepath

5. **修改**: Print Book处理部分
   - 替换iframe为相对路径video标签而不是完整video标签

## 关键变量

### 章节映射
```python
chapters_info = {
    '691946': {
        'title': 'Chapter 1 - Introduction',
        'folder_name': 'Chapter 1 - Introduction',
        'videos': [
            {'entry_id': '1_xxxxx', 'filename': 'Video 01.mp4'},
            {'entry_id': '1_yyyyy', 'filename': 'Video 02.mp4'}
        ],
        'index': 0
    },
    ...
}
```

### Print Book视频到章节的映射
```python
video_to_chapter_folder = {
    '1_xxxxx': 'Chapter 1 - Introduction',
    '1_yyyyy': 'Chapter 1 - Introduction',
    '1_zzzzz': 'Chapter 2 - Lifecycles'
}
```

## 测试清单

- [ ] 章节文件夹使用标题而不是数字ID
- [ ] 每个章节文件夹包含所有相关文件（HTML、视频、附件）
- [ ] Print Book HTML中的视频是相对路径（../Chapter 1 - Introduction/Video.mp4）
- [ ] 视频文件实际存在于对应章节文件夹中
- [ ] Print Book HTML在浏览器中可正确播放视频
- [ ] 附件在对应章节文件夹中
- [ ] Table of Contents.html 在根目录

## 潜在问题和解决方案

### 问题1: 章节标题中有特殊字符
- **解决**: 使用PathTools中的名称清理函数

### 问题2: 章节标题重复
- **解决**: 添加序号前缀（已在下载时处理）

### 问题3: Print Book中的视频顺序与Mobile API不一致
- **解决**: 使用entry_id作为唯一标识符进行映射

### 问题4: 某些视频只在Print Book中，不在章节中
- **解决**: 检查映射是否存在，如果不存在则使用root目录或 "Other Videos" 文件夹
