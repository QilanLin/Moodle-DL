# Kalvidres 页面爬取完整指南

## 目标页面

**URL**: https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619

**内容**:
- ✅ Errata 勘误文本
- ✅ Kaltura 视频 (01-intro, 26分钟)
- ✅ 英文字幕

---

## 从 HAR 文件提取的信息

### 📹 视频详情

| 属性 | 值 |
|------|-----|
| **视频名称** | 01-intro |
| **描述** | intro video |
| **Entry ID** | 1_smw4vcpg |
| **Partner ID** | 2368101 |
| **时长** | 1567 秒 (26 分钟) |
| **观看次数** | 1536 |

### 📊 可用视频质量

| 分辨率 | 码率 | 文件大小 | Flavor ID |
|--------|------|---------|-----------|
| 640x360 | 288 kbps | 53.8 MB | 1_zn0l8j0w |
| 854x480 | 398 kbps | 74.3 MB | 1_qs3gu76b |
| 960x540 | 446 kbps | 83.3 MB | 1_6z73h0y8 |
| **1280x720** | 694 kbps | 130.0 MB | 1_w9vu0rz1 |
| **1920x1080** ⭐ | 1378 kbps | 258.0 MB | 1_wkhc74fb |

### 💬 字幕

- **语言**: English (British)
- **格式**: WebVTT
- **URL**: https://cfvod.kaltura.com/api_v3/index.php/service/caption_captionasset/action/serveWebVTT/captionAssetId/1_3nkhy4ph/segmentIndex/-1/version/1/captions.vtt

---

## 方法 1: 使用 yt-dlp (推荐) ⭐

**最简单、最可靠的方法**

### 前置要求

```bash
# 1. 确保有有效的浏览器 cookies
cd /Users/linqilan/CodingProjects/Moodle-DL
./check_cookies.sh

# 2. 如果 cookies 无效，重新导出
python3 export_browser_cookies.py
```

### 下载视频 + 字幕

```bash
# 自动选择最佳质量
yt-dlp --cookies /Users/linqilan/CodingProjects/Cookies.txt \
  "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619" \
  -o "Week1-01-intro.%(ext)s" \
  --write-subs --sub-lang en

# 或者使用生成的脚本
chmod +x /Users/linqilan/CodingProjects/Moodle-DL/download_kalvidres.sh
/Users/linqilan/CodingProjects/Moodle-DL/download_kalvidres.sh
```

**优点**:
- ✅ 自动选择最佳质量
- ✅ 自动下载字幕
- ✅ 处理 Kaltura 的特殊认证
- ✅ 支持断点续传

---

## 方法 2: 直接下载 MP4

如果 yt-dlp 不工作，可以直接下载 MP4：

### 最高质量 (1920x1080)

```bash
curl -L -b /Users/linqilan/CodingProjects/Cookies.txt \
  "https://cdnapisec.kaltura.com/p/2368101/sp/2368101/playManifest/entryId/1_smw4vcpg/flavorId/1_wkhc74fb/format/url/protocol/https/a.mp4" \
  -o "Week1-01-intro-1080p.mp4"
```

### 中等质量 (1280x720, 推荐)

```bash
curl -L -b /Users/linqilan/CodingProjects/Cookies.txt \
  "https://cdnapisec.kaltura.com/p/2368101/sp/2368101/playManifest/entryId/1_smw4vcpg/flavorId/1_w9vu0rz1/format/url/protocol/https/a.mp4" \
  -o "Week1-01-intro-720p.mp4"
```

### 下载字幕

```bash
curl "https://cfvod.kaltura.com/api_v3/index.php/service/caption_captionasset/action/serveWebVTT/captionAssetId/1_3nkhy4ph/segmentIndex/-1/version/1/captions.vtt" \
  -o "Week1-01-intro.en.vtt"
```

---

## 方法 3: 使用 Python 脚本爬取页面

### 爬取页面内容（包括 Errata 文本）

```bash
cd /Users/linqilan/CodingProjects/Moodle-DL
python3 scrape_kalvidres.py
```

**这个脚本会**:
1. ✅ 获取页面 HTML
2. ✅ 提取 Errata 勘误文本
3. ✅ 提取 Kaltura iframe URL
4. ✅ 获取视频 Entry ID
5. ✅ 保存所有信息到文件

**输出文件**:
- `errata_text.txt` - Errata 勘误文本
- `kaltura_video_info.txt` - 视频信息和下载命令
- `kalvidres_page_full.html` - 完整页面 HTML

---

## 方法 4: 使用 moodle-dl 批量下载

下载所有 47 个 kalvidres 视频：

```bash
# 1. 确保有浏览器 cookies
cd /Users/linqilan/CodingProjects/Moodle-DL
python3 export_browser_cookies.py

# 2. 运行 moodle-dl
./run_moodle_dl.sh

# 或者
moodle-dl --path /Users/linqilan/CodingProjects
```

**moodle-dl 会**:
- 自动下载所有 47 个视频
- 保存到对应的 Week 目录
- 使用 yt-dlp 提取视频
- 自动下载字幕（如果有）

---

## 如何获取 Errata 文本

由于 HAR 文件只包含 XHR 请求，没有页面 HTML，你需要：

### 方法 A: 使用 Python 爬取脚本

```bash
python3 scrape_kalvidres.py
```

这会自动提取 Errata 并保存到 `errata_text.txt`

### 方法 B: 手动提取

1. **在浏览器中登录** keats.kcl.ac.uk
2. **访问页面**: https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619
3. **查看 Errata 文本**（在视频播放器上方）
4. **复制文本**或使用浏览器开发者工具

### 方法 C: 使用 curl + cookies

```bash
# 获取页面 HTML
curl -b /Users/linqilan/CodingProjects/Cookies.txt \
  "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619" \
  -o kalvidres_page.html

# 在 HTML 中搜索 Errata
grep -A 20 "Errata" kalvidres_page.html
```

---

## 完整工作流程（推荐）

### 一次性设置

```bash
cd /Users/linqilan/CodingProjects/Moodle-DL

# 1. 导出浏览器 cookies
python3 export_browser_cookies.py

# 2. 验证 cookies
./check_cookies.sh
```

### 下载单个视频 + Errata

```bash
# 1. 爬取页面内容
python3 scrape_kalvidres.py

# 2. 下载视频
yt-dlp --cookies /Users/linqilan/CodingProjects/Cookies.txt \
  "https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619" \
  -o "Week1-01-intro.%(ext)s" \
  --write-subs
```

### 批量下载所有视频

```bash
# 使用 moodle-dl
./run_moodle_dl.sh
```

---

## 文件清单

### 已生成的文件

| 文件 | 描述 |
|------|------|
| `kalvidres_full_info.json` | 完整的 Kaltura API 响应 |
| `download_kalvidres.sh` | 自动下载脚本 |
| `scrape_kalvidres.py` | 页面爬取脚本 |
| `kaltura_response_8.json` | Kaltura API 响应（从 HAR） |

### 运行脚本后生成的文件

| 文件 | 描述 |
|------|------|
| `errata_text.txt` | Errata 勘误文本 |
| `kaltura_video_info.txt` | 视频信息汇总 |
| `kalvidres_page_full.html` | 完整页面 HTML |
| `Week1-01-intro.mp4` | 下载的视频文件 |
| `Week1-01-intro.en.vtt` | 英文字幕 |

---

## 故障排查

### 问题 1: "Cookies 无效"

```bash
# 重新导出 cookies
cd /Users/linqilan/CodingProjects/Moodle-DL
python3 export_browser_cookies.py

# 验证
./check_cookies.sh
```

### 问题 2: yt-dlp 下载失败

```bash
# 使用直接下载方法
curl -L -b Cookies.txt \
  "https://cdnapisec.kaltura.com/p/2368101/sp/2368101/playManifest/entryId/1_smw4vcpg/flavorId/1_w9vu0rz1/format/url/protocol/https/a.mp4" \
  -o video.mp4
```

### 问题 3: 找不到 Errata

```bash
# 使用爬取脚本
python3 scrape_kalvidres.py

# 检查输出文件
cat errata_text.txt
```

### 问题 4: 视频播放有问题

可能下载了 HLS 流（.m3u8）而不是 MP4。解决方案：

```bash
# 使用 ffmpeg 转换
ffmpeg -i video.m3u8 -c copy video.mp4
```

---

## 快速命令参考

```bash
# 检查 cookies 状态
./check_cookies.sh

# 导出浏览器 cookies
python3 export_browser_cookies.py

# 爬取单个页面
python3 scrape_kalvidres.py

# 下载单个视频
yt-dlp --cookies Cookies.txt "URL" -o "output.%(ext)s"

# 批量下载所有视频
./run_moodle_dl.sh

# 直接下载 MP4
./download_kalvidres.sh
```

---

## 关键 URLs

### 页面 URL
```
https://keats.kcl.ac.uk/mod/kalvidres/view.php?id=9159619
```

### Kaltura Entry
```
Entry ID: 1_smw4vcpg
Partner ID: 2368101
```

### 直接下载 (1080p)
```
https://cdnapisec.kaltura.com/p/2368101/sp/2368101/playManifest/entryId/1_smw4vcpg/flavorId/1_wkhc74fb/format/url/protocol/https/a.mp4
```

### 字幕
```
https://cfvod.kaltura.com/api_v3/index.php/service/caption_captionasset/action/serveWebVTT/captionAssetId/1_3nkhy4ph/segmentIndex/-1/version/1/captions.vtt
```

---

**总结**: 使用 `yt-dlp` + 浏览器 cookies 是最简单可靠的方法！🎉
