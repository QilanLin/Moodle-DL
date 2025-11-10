# Book 模块改进 - 测试指南

## 概述

本文档提供了验证 Book 模块改进是否正确实现的测试步骤。

## 预期目录结构

改进后的下载应该生成以下目录结构：

```
Week 1 - Software Engineering and Software Lifecycles/
├── Week 1 - Software Engineering and Software Lifecycles.html
│   ├── <相对链接到各章节视频>
│   ├── <HTML5 video标签而不是iframe>
│   └── <完整的目录和导航>
│
├── Table of Contents.html
│   └── <目录结构，链接各章节>
│
├── 01 - Chapter 1 - Introduction/
│   ├── Chapter 1.html
│   │   ├── <章节内容>
│   │   └── <嵌入式Kaltura视频的iframe>
│   ├── Video 01.mp4 (如果存在)
│   ├── Video 02.mp4 (如果存在)
│   ├── Slides.pdf
│   └── Exercise.zip
│
├── 02 - Chapter 2 - Lifecycles/
│   ├── Chapter 2.html
│   ├── Video 01.mp4
│   ├── Reading.pdf
│   └── ...其他附件
│
└── 03 - Chapter 3 - ...
    ├── Chapter 3.html
    ├── Video 01.mp4
    └── ...
```

## 测试检查清单

### 1. 文件夹结构检查

- [ ] **章节文件夹命名**
  - 检查：`find "Week 1 - Software Engineering" -maxdepth 1 -type d`
  - 期望：`01 - Chapter 1 - Introduction`, `02 - Chapter 2 - Lifecycles` 等
  - 当前状态：应该 **不再** 看到纯数字ID（如691946, 691947等）

- [ ] **章节文件夹内的文件**
  - 检查：`ls "01 - Chapter 1 - Introduction/"`
  - 期望：
    - 章节HTML：`Chapter 1.html` 或类似名称
    - 视频文件：`Video 01.mp4`, `Video 02.mp4` 等
    - 附件文件：PDF, ZIP, PPTX 等

- [ ] **根目录文件**
  - 检查：`ls "Week 1 - Software Engineering/"`
  - 期望：
    - `Week 1 - Software Engineering and Software Lifecycles.html` (Print Book)
    - `Table of Contents.html`
    - 各章节文件夹

### 2. Print Book HTML 检查

**在浏览器中打开 Print Book HTML 并验证：**

- [ ] **视频播放**
  ```bash
  grep -n "video controls" "Week 1 - Software Engineering and Software Lifecycles.html"
  ```
  期望：看到多个 `<video controls>` 标签而不是 `<iframe class="kaltura-player-iframe">`

- [ ] **相对路径链接**
  ```bash
  grep -n '<source src=' "Week 1 - Software Engineering and Software Lifecycles.html" | head -5
  ```
  期望：看到类似 `<source src="01 - Chapter 1 - Introduction/Video 01.mp4">` 的相对路径

- [ ] **视频是否可播放**
  - 用浏览器打开 Print Book HTML
  - 滚动到各个视频位置
  - 点击播放按钮，验证视频能否正常加载和播放
  - 验证进度条和音量控制工作正常

- [ ] **导航功能**
  - 检查目录（TOC）是否完整
  - 验证章节标题和页面内容是否匹配

### 3. 章节HTML 检查

**对于每个章节HTML文件：**

- [ ] **内容是否完整**
  ```bash
  wc -c "01 - Chapter 1 - Introduction/Chapter 1.html"
  ```
  期望：文件大小合理（数KB到数MB，取决于内容）

- [ ] **Kaltura iframe 是否仍在**
  ```bash
  grep -c 'filter/kaltura/lti_launch' "01 - Chapter 1 - Introduction/Chapter 1.html"
  ```
  期望：仍然包含原始的 Kaltura iframe（因为这是章节的原始内容）

- [ ] **视频是否可播放**
  - 用浏览器打开章节HTML
  - 验证嵌入的 Kaltura 视频是否能播放
  - 或者验证是否有下载的视频文件链接

### 4. 视频文件检查

- [ ] **视频文件存在**
  ```bash
  find "Week 1 - Software Engineering" -name "*.mp4" -type f
  ```
  期望：列出所有下载的视频文件和它们的路径

- [ ] **视频文件大小**
  ```bash
  du -h "01 - Chapter 1 - Introduction/"*.mp4
  ```
  期望：文件大小合理（通常从MB到GB不等）

- [ ] **视频文件是否可播放**
  - 使用 ffmpeg 检查：`ffmpeg -i "01 - Chapter 1 - Introduction/Video 01.mp4" 2>&1 | head -10`
  - 或者用播放器打开验证

### 5. 日志检查

**运行下载时，检查日志中的相关信息：**

```bash
grep -i "book" ~/.moodle-dl/MoodleDL.log | grep -i "chapter"
```

期望：看到类似这样的日志：
```
📁 Chapter folder name: 01 - Chapter 1 - Introduction (ID: 691946)
🎬 Extracted Kaltura video 1: entry_id=1_xxxxx, filename=Video 01.mp4
✅ Chapter 691946 processed with 1 video(s)
✅ Converted 2 Kaltura iframe(s) to linked video tags in print book
```

### 6. 文件对比检查

**对比改进前后的文件结构：**

旧结构示例：
```
Week 1 - Software Engineering/
├── 691946/
│   └── index.html
├── 691947/
│   └── ISE-week01-Overview.pptx
├── 691948/
│   └── ISE-week01-Software Engineering.pptx
...
```

新结构示例：
```
Week 1 - Software Engineering/
├── 01 - Chapter 1 - Introduction/
│   ├── Chapter 1.html
│   ├── Video 01.mp4
│   └── Video 02.mp4
├── 02 - Chapter 2 - Lifecycles/
│   ├── Chapter 2.html
│   ├── Video 01.mp4
│   └── ...
...
```

检查命令：
```bash
# 旧结构：只有纯数字文件夹
find . -maxdepth 1 -type d -name "[0-9]*" | wc -l

# 新结构：应该没有纯数字文件夹，而是有 "NN - " 开头的文件夹
find . -maxdepth 1 -type d -name "[0-9][0-9] - *" | head -10
```

## 深度验证步骤

### 1. 验证 Kaltura 转换

```bash
# 检查转换日志
grep "Converted Kaltura URL to kalvidres" ~/.moodle-dl/MoodleDL.log

# 期望输出：
# ✅ Converted Kaltura URL to kalvidres: entry_id=1_xxxxx
```

### 2. 验证 Print Book 链接

```bash
# 查看Print Book HTML中的video标签
grep -A 2 '<video controls' "Week 1 - Software Engineering and Software Lifecycles.html" | head -10

# 期望：看到相对路径链接
# <source src="01 - Chapter 1 - Introduction/Video 01.mp4" type="video/mp4">
```

### 3. 验证 TOC 映射

```bash
# 检查TOC是否完整
grep '<li' "Table of Contents.html" | head -20

# 期望：看到所有章节标题
```

## 故障排除

### 问题 1: 章节仍然使用数字ID

**症状**：文件夹仍然是 `691946/`, `691947/` 等

**可能原因**：
1. 代码未正确更新或安装
2. `_get_chapter_title_from_toc()` 返回了备选名称

**验证**：
```bash
# 检查book.py是否已更新
grep "_get_chapter_title_from_toc" /path/to/book.py

# 检查日志中是否有"Chapter folder name"输出
grep "Chapter folder name" ~/.moodle-dl/MoodleDL.log
```

### 问题 2: Print Book HTML 中仍有 iframe

**症状**：Print Book HTML 中包含 `<iframe class="kaltura-player-iframe">`

**可能原因**：
1. `_create_linked_print_book_html()` 未被调用
2. iframe 的 class 属性不匹配
3. 章节映射为空

**验证**：
```bash
# 检查是否调用了链接方法
grep "Converted.*Kaltura iframe" ~/.moodle-dl/MoodleDL.log

# 检查章节映射是否为空
grep "chapter_mapping_for_print_book" ~/.moodle-dl/MoodleDL.log
```

### 问题 3: 视频未被下载

**症状**：章节文件夹中没有 .mp4 文件

**可能原因**：
1. Kaltura URL 转换失败
2. `result_builder` 未识别视频URL
3. 下载被跳过或失败

**验证**：
```bash
# 检查Kaltura转换是否成功
grep "Kaltura" ~/.moodle-dl/MoodleDL.log | grep -i "entry_id"

# 检查下载任务
grep "Download.*Video" ~/.moodle-dl/MoodleDL.log
```

## 性能指标

下载完成后，检查以下指标：

- [ ] **下载时间**：应该类似或略长于原来（多了Print Book处理）
- [ ] **存储空间**：应该相同或更少（避免了视频重复嵌入）
- [ ] **文件数量**：应该相同，只是组织方式不同
- [ ] **HTTP请求数**：可能略多（多了TOC查询）

## 报告问题

如果遇到问题，请收集以下信息：

1. **日志文件内容**：`~/.moodle-dl/MoodleDL.log` (搜索 "book" 和 "video")
2. **目录结构对比**：用树形结构显示实际下载的文件夹
3. **HTML 文件片段**：Print Book HTML 中关于视频的部分
4. **系统信息**：Python 版本、操作系统、Moodle 版本

## 完整验证脚本

```bash
#!/bin/bash
# book_verification.sh

DOWNLOAD_DIR="$1"
if [ -z "$DOWNLOAD_DIR" ]; then
    echo "Usage: $0 <download_directory>"
    exit 1
fi

echo "=== Book Module Improvements Verification ==="
echo ""

echo "1. Checking folder structure..."
cd "$DOWNLOAD_DIR"

# Count new-style folders (NN - Title)
NEW_STYLE=$(find . -maxdepth 1 -type d -name "[0-9][0-9] - *" | wc -l)
echo "   New-style folders (NN - Title): $NEW_STYLE"

# Count old-style folders (pure numbers)
OLD_STYLE=$(find . -maxdepth 1 -type d -name "[0-9]*" | wc -l)
echo "   Old-style folders (pure numbers): $OLD_STYLE"

echo ""
echo "2. Checking Print Book HTML..."
if [ -f "*.html" ]; then
    IFRAME_COUNT=$(grep -c 'kaltura-player-iframe' *.html 2>/dev/null || echo 0)
    VIDEO_COUNT=$(grep -c '<video controls' *.html 2>/dev/null || echo 0)
    echo "   Kaltura iframe tags: $IFRAME_COUNT (should be 0)"
    echo "   HTML5 video tags: $VIDEO_COUNT (should be > 0)"
fi

echo ""
echo "3. Checking video files..."
MP4_COUNT=$(find . -name "*.mp4" | wc -l)
echo "   Total MP4 files: $MP4_COUNT"

# Check video files per chapter
for chapter in [0-9][0-9]*; do
    if [ -d "$chapter" ]; then
        count=$(ls "$chapter"/*.mp4 2>/dev/null | wc -l)
        echo "   $chapter: $count videos"
    fi
done

echo ""
echo "=== Verification Complete ==="
```

使用方式：
```bash
chmod +x book_verification.sh
./book_verification.sh "/path/to/downloaded/week"
```

## 成功标志

改进实现成功的标志：

✅ 所有章节文件夹使用 "NN - Title" 格式
✅ 每个章节文件夹包含该章节的所有文件
✅ Print Book HTML 包含 HTML5 video 标签而不是 iframe
✅ Video 标签使用相对路径指向章节文件夹中的视频
✅ 所有视频文件存在且可播放
✅ Print Book HTML 在浏览器中可正常显示和导航
✅ 日志中显示了 Kaltura 转换和视频链接操作

## 下一步

验证完成后：

1. **测试其他 book 模块**：验证多个不同课程的 book 下载
2. **测试边界情况**：
   - 没有视频的章节
   - 有多个视频的章节
   - 嵌套章节结构
3. **性能测试**：大型 book（100+ 章节）的下载性能
4. **兼容性测试**：不同 Moodle 版本的 book 模块

## 反馈

如果改进工作正常，请反馈：
- 哪些方面工作最好
- 是否有任何不符合预期的地方
- 有无后续改进建议
