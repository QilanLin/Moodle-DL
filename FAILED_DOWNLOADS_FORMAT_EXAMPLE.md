# 失败下载列表显示格式说明

## 改进内容

之前的失败下载列表只显示文件名和错误信息，无法快速定位问题。现在新增了以下信息：

### 📋 显示内容

每个失败的下载会显示：

1. **文件名**（青色，显眼）
2. **错误信息**（红色）
3. **目标路径**（灰色）- 文件应该保存到的本地路径
4. **来源 URL**（灰色）- 文件在 Moodle 服务器上的下载地址

## 📊 显示示例

### 之前的格式

```
尝试下载文件时出错，请查看日志以获取更多详细信息。失败的下载列表：

Fibonacci numbers recursion.zip
	Response payload is not completed: <TransferEncodingError: 400, message='Not enough data to satisfy transfer length header.'>
Video 1
	yt-dlp 无法下载该 URL。你可以通过运行 `moodle-dl --ignore-ytdl-errors` 一次来忽略此错误。
```

### 改进后的格式

```
尝试下载文件时出错，请查看日志以获取更多详细信息。失败的下载列表：

Fibonacci numbers recursion.zip
  错误: Response payload is not completed: <TransferEncodingError: 400, message='Not enough data to satisfy transfer length header.'>
  目标: /Users/你的用户名/Moodle/Data Structures/Week 3/Fibonacci numbers recursion.zip
  来源: https://keats.kcl.ac.uk/pluginfile.php/12345678/mod_resource/content/1/Fibonacci%20numbers%20recursion.zip

Video 1
  错误: yt-dlp 无法下载该 URL。你可以通过运行 `moodle-dl --ignore-ytdl-errors` 一次来忽略此错误。
  目标: /Users/你的用户名/Moodle/Data Structures/Week 3/Video 1.mp4
  来源: https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=87654321

Lists slides1-9.zip
  错误: Response payload is not completed: <TransferEncodingError: 400, message='Not enough data to satisfy transfer length header.'>
  目标: /Users/你的用户名/Moodle/Data Structures/Week 5/Lists slides1-9.zip
  来源: https://keats.kcl.ac.uk/pluginfile.php/23456789/mod_resource/content/2/Lists%20slides1-9.zip
```

## 💡 新格式的优势

### 1. 快速定位问题

**场景：TransferEncodingError**
- **目标路径** → 可以检查文件是否部分下载，是否可以手动删除
- **来源 URL** → 可以在浏览器中测试下载，确认服务器是否正常

### 2. 手动下载备选方案

如果自动下载失败，可以：
1. 复制 **来源 URL** 到浏览器
2. 手动下载文件
3. 保存到 **目标路径** 指定的位置

### 3. 调试和报告问题

向技术支持或开发者报告问题时，可以提供：
- 完整的错误信息
- 源 URL（便于重现问题）
- 目标路径（便于理解文件结构）

### 4. 批量处理

通过脚本可以：
```bash
# 提取所有失败的 URL
grep "来源:" failed_downloads.txt | sed 's/  来源: //' > urls.txt

# 使用 wget 或 curl 批量下载
wget -i urls.txt
```

## 🔍 URL 截断说明

为了提高可读性，超过 120 字符的 URL 会被截断：

**原始 URL（很长）：**
```
https://keats.kcl.ac.uk/pluginfile.php/12345678/mod_resource/content/1/very_long_filename_with_lots_of_characters_that_makes_the_url_extremely_long.zip?token=abcdef123456789
```

**显示为：**
```
来源: https://keats.kcl.ac.uk/pluginfile.php/12345678/mod_res...?token=abcdef123456789
```

- 保留前 60 个字符（包含域名和主要路径）
- 保留后 50 个字符（通常包含文件扩展名和参数）
- 中间用 `...` 代替

**完整 URL 查找方法：**
如需完整 URL，可以在日志文件中搜索文件名：
```bash
grep "Fibonacci numbers recursion.zip" MoodleDL.log
```

## 🎨 颜色说明

终端中的颜色编码：
- **青色（Cyan）**：文件名 - 最显眼，快速识别哪个文件失败
- **红色（Red）**：错误信息 - 警告色，表示问题描述
- **灰色（Info）**：路径和 URL - 补充信息，不分散注意力

## 📝 实际使用场景

### 场景 1：网络中断导致的下载失败

```
Merge sort 3
  错误: TimeoutError()
  目标: /Users/linqilan/Moodle/Data Structures/Week 10/Merge sort 3.mp4
  来源: https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=87654321
```

**操作建议：**
1. 检查网络连接
2. 运行 `moodle-dl --retry-failed` 重试
3. 如果持续失败，在浏览器中打开 URL 手动下载

### 场景 2：服务器返回不完整数据

```
Fibonacci numbers recursion.zip
  错误: Response payload is not completed: <TransferEncodingError: 400>
  目标: /Users/linqilan/Moodle/Data Structures/Week 3/Fibonacci numbers recursion.zip
  来源: https://keats.kcl.ac.uk/pluginfile.php/12345678/...
```

**操作建议：**
1. 检查目标路径，删除可能存在的不完整文件：
   ```bash
   rm "/Users/linqilan/Moodle/Data Structures/Week 3/Fibonacci numbers recursion.zip"
   ```
2. 刷新 cookies（如果过期）：
   ```bash
   moodle-dl --refresh-cookies
   ```
3. 重试下载：
   ```bash
   moodle-dl --retry-failed
   ```

### 场景 3：yt-dlp 无法下载视频

```
Video 1
  错误: yt-dlp 无法下载该 URL。你可以通过运行 `moodle-dl --ignore-ytdl-errors` 一次来忽略此错误。
  目标: /Users/linqilan/Moodle/Data Structures/Week 3/Video 1.mp4
  来源: https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=87654321
```

**操作建议：**

**选项 1：忽略此错误**
```bash
moodle-dl --ignore-ytdl-errors
```

**选项 2：手动下载**
1. 在浏览器中打开源 URL
2. 使用浏览器的下载功能或录屏工具
3. 保存到目标路径

**选项 3：更新 yt-dlp**
```bash
pip install --upgrade yt-dlp
moodle-dl --retry-failed
```

## 🔧 相关命令

```bash
# 查看失败的下载
moodle-dl --verbose --log-to-file

# 刷新 cookies
moodle-dl --refresh-cookies

# 重试失败的下载
moodle-dl --retry-failed

# 忽略 yt-dlp 错误
moodle-dl --ignore-ytdl-errors

# 查看详细日志
tail -f MoodleDL.log
```

## 📚 技术细节

### 实现位置
`moodle_dl/notifications/console/console_service.py`

### 数据来源
- **文件名**：`task.file.content_filename`
- **错误信息**：`task.status.get_error_text()`
- **目标路径**：`Path(task.destination) / task.filename` 或 `task.file.saved_to`
- **来源 URL**：`task.file.content_fileurl`

### 相关类型
- `Task`：下载任务对象（来自 `moodle_dl/downloader/task.py`）
- `File`：文件元数据对象（来自 `moodle_dl/types.py`）
- `TaskStatus`：任务状态对象（来自 `moodle_dl/types.py`）

