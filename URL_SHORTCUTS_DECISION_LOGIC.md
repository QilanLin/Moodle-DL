# 📎 URL 快捷方式决策逻辑

**日期**: 2025-11-19  
**类型**: 功能说明文档

---

## 🎯 快速回答

**问题**: 系统是怎么决定是否需要创建网页快捷方式的呢？

**答案**: 系统通过以下流程决定：

1. **检查配置** `download_urls` 是否为 true
2. **检查配置** `write_link` 是否为 true  
3. **自动检测** 操作系统类型
4. **生成** 对应格式的快捷方式

---

## 📊 决策流程图

```
URL 模块 中的链接
     ↓
是否启用下载 URLs？
   ├─ No  → 跳过，不创建任何文件
   └─ Yes → 继续
          ↓
      是否启用创建快捷方式？
         ├─ No  → 不创建快捷方式
         └─ Yes → 自动检测操作系统
                 ├─ macOS     → 创建 .webloc  ✓
                 ├─ Windows   → 创建 .URL     ✓
                 └─ Linux     → 创建 .desktop ✓
```

---

## 🔍 详细流程

### 1️⃣ 文件来源：URL 模块

**模块**: `moodle_dl/moodle/mods/url.py`

URL 模块在 Moodle 中用来创建指向外部网站的链接。`moodle-dl` 会：

1. 通过 API 获取 URL 模块信息
2. 为每个链接创建一个文件记录，类型为 `'url'`
3. 设置 `content_fileurl` 为实际的网址

**示例**:
```python
{
    'filename': 'Killed by Code Transparency in Implantable Medical Devices',
    'filepath': '/',
    'content_fileurl': 'https://example.com/article',
    'type': 'url',  # ← 关键标记
    'timemodified': 1634567890,
    'module_modname': 'url',
}
```

### 2️⃣ 配置检查阶段

**配置项**: `download_urls`

位置: `config.json`

```json
{
    "download_urls": true  // 默认 false
}
```

**代码**:
```python
# moodle_dl/moodle/mods/url.py (第 36-40 行)
@classmethod
def download_condition(cls, config: ConfigHelper, file: File) -> bool:
    return config.get_download_urls() or (
        not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted)
    )
```

如果 `download_urls = false`，这些链接文件会被完全跳过。

### 3️⃣ 快捷方式格式决策

**配置项们**: `write_link`, `write_webloc_link`, `write_url_link`, `write_desktop_link`

位置: `config.json`

```json
{
    "write_link": true,              // 默认: true (主开关)
    "write_webloc_link": false,      // 默认: false (macOS 专用)
    "write_url_link": false,         // 默认: false (Windows 专用)
    "write_desktop_link": false      // 默认: false (Linux 专用)
}
```

**决策逻辑** (`moodle_dl/config.py`, 第 388-401 行):

```python
def get_write_links(self) -> Dict:
    write_links = {
        'url': self.get_property_or('write_url_link', False),
        'webloc': self.get_property_or('write_webloc_link', False),
        'desktop': self.get_property_or('write_desktop_link', False),
    }
    
    # 如果启用了 write_link（默认），自动选择操作系统对应的格式
    if self.get_property_or('write_link', True):
        link_type = (
            'webloc' if sys.platform == 'darwin' else      # macOS
            'desktop' if sys.platform.startswith('linux') else  # Linux
            'url'  # Windows (Windows NT)
        )
        write_links[link_type] = True
    
    return write_links
```

### 4️⃣ 快捷方式生成

**触发条件**: 当下载器处理 `type='url'` 的文件时

位置: `moodle_dl/downloader/task.py` (第 664-677 行)

```python
async def create_shortcut(self):
    """Create a Shortcut to a URL"""
    logging.debug('[%d] Creating a shortcut', self.task_id)
    
    # 获取配置中指定的快捷方式格式
    for link_type, should_write in self.opts.write_links.items():
        if should_write:
            # 为每个启用的格式创建一个文件
            self.set_path(True, link_type)
            
            # 打开文件
            async with aiofiles.open(self.file.saved_to, 'w+', ...) as shortcut:
                # 选择对应的模板
                template_vars = {'url': self.file.content_fileurl}
                
                # 填充并写入
                await shortcut.write(LINK_TEMPLATES[link_type] % template_vars)
```

---

## 📋 快捷方式格式详解

### 1️⃣ .webloc 格式（macOS）

**文件扩展名**: `.webloc`

**格式**: Apple Property List (plist) XML

**模板** (`moodle_dl/utils.py`, 第 94-103 行):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>URL</key>
	<string>%(url)s</string>
</dict>
</plist>
```

**特点**:
- ✅ macOS Finder 可直接识别
- ✅ 双击即可打开链接
- ✅ 可拖到浏览器地址栏

### 2️⃣ .URL 格式（Windows）

**文件扩展名**: `.URL`

**格式**: Windows Internet Shortcut (INI 格式)

**模板** (`moodle_dl/utils.py`, 第 89-92 行):

```ini
[InternetShortcut]
URL=%(url)s
```

**特点**:
- ✅ Windows 资源管理器可识别
- ✅ 右键可查看/编辑属性
- ✅ 可固定到开始菜单

### 3️⃣ .desktop 格式（Linux）

**文件扩展名**: `.desktop`

**格式**: Desktop Entry Standard

**模板** (`moodle_dl/utils.py`, 第 105-112 行):

```ini
[Desktop Entry]
Encoding=UTF-8
Name=%(filename)s
Type=Link
URL=%(url)s
Icon=text-html
```

**特点**:
- ✅ Linux 文件管理器可识别
- ✅ 符合 Desktop Entry 标准
- ✅ 可配置图标

---

## 🔧 配置示例

### 场景 1: 默认行为（推荐）

```json
{
    "download_urls": true,
    "write_link": true,
    "write_webloc_link": false,
    "write_url_link": false,
    "write_desktop_link": false
}
```

**结果**: 
- 下载所有 URL 模块链接
- 根据操作系统自动创建快捷方式
- 无需手动配置

### 场景 2: 禁用快捷方式

```json
{
    "download_urls": true,
    "write_link": false,
    "write_webloc_link": false,
    "write_url_link": false,
    "write_desktop_link": false
}
```

**结果**: 
- 下载链接但不创建快捷方式
- 链接存储在数据库中但未保存为文件

### 场景 3: 禁用 URL 下载

```json
{
    "download_urls": false,
    "write_link": true
}
```

**结果**: 
- 完全跳过 URL 模块链接
- 不会在下载文件夹中出现

### 场景 4: 仅创建 .webloc（强制 macOS 格式）

```json
{
    "download_urls": true,
    "write_link": false,
    "write_webloc_link": true,
    "write_url_link": false,
    "write_desktop_link": false
}
```

**结果**: 
- 仅创建 .webloc 文件
- 即使在 Windows/Linux 上也创建 .webloc

---

## 💡 关键代码位置

| 功能 | 文件 | 行数 |
|------|------|------|
| URL 模块处理 | `moodle_dl/moodle/mods/url.py` | 12-45 |
| 下载 URLs 配置 | `moodle_dl/config.py` | 176-177 |
| 快捷方式格式决策 | `moodle_dl/config.py` | 388-401 |
| 快捷方式生成 | `moodle_dl/downloader/task.py` | 664-677 |
| 快捷方式模板 | `moodle_dl/utils.py` | 89-118 |

---

## 📊 实际示例

### 你的下载中的文件

```
Week 1 - Introduction and Overview/
├── 02 [Mandatory] Week 1 - Recorded Lecture 1 Handouts.pdf
├── ...
├── Killed by Code Transparency in Implantable Medical Devices.webloc  ← URL 快捷方式
├── How Google tests its software (by Google test engineering director James Whittaker).webloc  ← URL 快捷方式
└── ...
```

### 快捷方式文件内容示例

如果你在 macOS 上用文本编辑器打开 `.webloc` 文件，会看到：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>URL</key>
	<string>https://example.com/article</string>
</dict>
</plist>
```

---

## 🎯 总结

系统决定是否创建网页快捷方式的过程很简洁：

| 步骤 | 检查项 | 默认值 |
|------|--------|--------|
| 1 | 是否下载 URL 模块？ | `download_urls = false` |
| 2 | 是否创建快捷方式？ | `write_link = true` |
| 3 | 操作系统类型 | 自动检测 |
| 4 | 生成对应格式文件 | - |

**结果**: 
- ✅ macOS 用户 → .webloc 文件
- ✅ Windows 用户 → .URL 文件  
- ✅ Linux 用户 → .desktop 文件

这样设计的好处是**跨平台兼容**，无需用户手动指定格式！

---


