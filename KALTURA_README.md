# Moodle-DL Embedded Kaltura 视频处理文档

## 文档导航

本目录包含关于 Moodle-DL 中 Embedded Kaltura 视频（Book 模块中的内嵌 LTI 视频）处理的完整分析。

### 可用文档

1. **KALTURA_SUMMARY.txt** (7.9 KB)
   - 执行总结，关键发现
   - 三层处理架构
   - 三种视频检测方式
   - 四阶段 URL 转换
   - 快速浏览，5-10 分钟理解全貌

2. **KALTURA_QUICK_REFERENCE.md** (7.7 KB)
   - 快速参考表
   - 正则模式一览
   - URL 格式演变
   - 函数调用链路
   - 代码片段示例
   - 开发和调试时使用

3. **KALTURA_ANALYSIS.md** (19 KB)
   - 完整深度分析
   - 12 个详细章节
   - 系统架构、流程、API、配置
   - 包含完整代码位置和解释
   - 深入学习时参考

---

## 快速开始

### 我是新手，想快速了解
→ 先读 **KALTURA_SUMMARY.txt** 的前两部分

### 我需要找特定的代码位置
→ 查看 **KALTURA_QUICK_REFERENCE.md** 的"关键代码位置总结"表

### 我需要深入理解实现细节
→ 阅读 **KALTURA_ANALYSIS.md**

### 我在开发新功能或调试
→ 使用 **KALTURA_QUICK_REFERENCE.md** 作为速查表

---

## 核心概念速记

### 三层处理架构
```
检测层 (BookMod + ResultBuilder)
  ↓
转换层 (URL 阶段转换)
  ↓
下载层 (yt-dlp extractors)
```

### 三种检测方式
1. **Book 章节内** (book.py)
   - 从 Mobile API fileurl 获取完整 HTML
   - 正则提取 iframe，创建 kalvidres_embedded File

2. **ResultBuilder** (result_builder.py)
   - 在所有 HTML 中检测 lti_launch.php URLs
   - 快速转换为 cookie_mod-kalvidres

3. **Print Book** (book.py + Playwright)
   - 使用浏览器获取完整 HTML
   - 章节-视频映射，保留目录结构

### 四阶段 URL 转换
```
Phase 1: lti_launch.php?source=... (HTML中)
Phase 2: browseandembed/...entryid/... (Kaltura官方)
Phase 3: embedIframeJs/...?entry_id=... (Kaltura CDN)
Phase 4: a.mp4 或 a.m3u8 (实际媒体)
```

---

## 关键代码位置速查

| 功能 | 文件 | 行数 |
|------|------|------|
| Book 模块初始化 | `moodle_dl/moodle/mods/book.py` | 25-233 |
| 章节 HTML 获取 | `moodle_dl/moodle/mods/book.py` | 288-316 |
| 视频检测 | `moodle_dl/moodle/mods/book.py` | 318-385 |
| Print Book | `moodle_dl/moodle/mods/book.py` | 390-691 |
| URL 转换 | `moodle_dl/moodle/result_builder.py` | 314-331 |
| 视频下载 | `moodle_dl/downloader/task.py` | 1094-1121 |
| 提取器 | `moodle_dl/downloader/extractors/` | 多个文件 |

---

## 常用正则模式

```regex
检测 LTI:           /filter/kaltura/lti_launch\.php
提取 entry_id:      entryid[/%]([^/%&]+)
提取 partnerId:     partnerId[=:](\d+)
提取 uiconf_id:     /playerSkin/(\d+)
```

详见 KALTURA_QUICK_REFERENCE.md

---

## 调试和故障排除

### 启用详细日志
```bash
moodle-dl --verbose --log-to-file
tail -f ~/.moodle-dl/MoodleDL.log | grep -i kaltura
```

### 常见问题
- **Print Book 下载失败**: 运行 `moodle-dl --init --sso` 刷新 cookies
- **视频无法下载**: 检查网络连接和 Kaltura CDN 可访问性
- **Entry ID 提取失败**: 检查正则模式和 URL 格式

详见 KALTURA_ANALYSIS.md 第 12 章

---

## 文件结构

```
KALTURA_README.md (本文件)
KALTURA_SUMMARY.txt (执行总结)
KALTURA_QUICK_REFERENCE.md (快速参考)
KALTURA_ANALYSIS.md (深度分析)
```

---

## 相关源文件

关键实现文件位置（相对于项目根目录）：

```
moodle_dl/
├── moodle/
│   ├── mods/book.py                    (Book 模块)
│   ├── result_builder.py               (URL 处理)
│   └── moodle_service.py               (主服务)
├── downloader/
│   ├── task.py                         (下载任务)
│   └── extractors/
│       ├── kalvidres_embedded.py       (Embedded 提取器)
│       ├── kalvidres_lti.py            (LTI 提取器)
│       └── __init__.py                 (提取器注册)
└── ...
```

---

## 版本和兼容性

- Moodle 版本: 3.0+ (MOD_MIN_VERSION = 2015111600)
- Python: 3.7+
- 关键依赖: aiohttp, Playwright, yt-dlp

---

## 更新历史

- 2025-11-09: 初始分析文档创建
  - 三份详细文档
  - 共 1115 行分析
  - 覆盖完整的 Embedded Kaltura 处理流程

---

## 使用建议

1. **新手开发者**
   - 先读 SUMMARY，了解全貌
   - 再读 QUICK_REFERENCE，找到代码
   - 最后读 ANALYSIS，深入理解

2. **项目维护者**
   - 使用 QUICK_REFERENCE 快速定位问题
   - 参考 ANALYSIS 理解设计意图
   - 在 SUMMARY 中追踪架构变化

3. **问题排查**
   - 查看日志中的关键词
   - 对照 QUICK_REFERENCE 的正则表
   - 检查相应的代码位置

---

## 其他资源

查看项目中的其他文档：
- `CLAUDE.md` - Claude 开发指南
- `README.md` - 项目概述
- `AUTHENTICATION.md` - 认证细节
- `MOODLE_API_LIMITATIONS.md` - API 限制

---

**最后更新**: 2025-11-09  
**分析工具**: Claude Code + Manual Code Review  
**覆盖范围**: 完整的 Embedded Kaltura 视频处理流程
