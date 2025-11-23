# 数据库文件下载状态分类详解

**文档版本**: 1.0  
**数据库版本**: v9  
**更新日期**: 2025年11月

---

## 📊 核心概念

Moodle-DL 使用 **双重标识系统** 来追踪文件下载状态：

1. **传统方式**: `saved_to` 字段（路径判断）
2. **明确状态**: `download_status` 字段（状态标识）

---

## 🗄️ 核心表结构

### `files` 表

文件主表，存储所有课程文件信息和下载状态。

```sql
CREATE TABLE files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    course_fullname TEXT NOT NULL,
    module_id INTEGER NOT NULL,
    section_name TEXT NOT NULL,
    content_filename TEXT NOT NULL,
    content_fileurl TEXT NOT NULL,
    
    -- 下载状态相关字段
    saved_to TEXT NOT NULL,                    -- 文件保存路径（空=未下载）
    download_status TEXT DEFAULT 'pending',    -- 下载状态（pending/success/failed）
    download_attempts INTEGER DEFAULT 0,        -- 尝试次数
    consecutive_failures INTEGER DEFAULT 0,     -- 连续失败次数
    last_download_at INTEGER DEFAULT 0,         -- 最后下载时间
    last_failed_at INTEGER DEFAULT 0,           -- 最后失败时间
    last_failed_reason TEXT,                    -- 失败原因
    
    -- 其他字段...
    time_stamp INTEGER DEFAULT 0 NOT NULL,
    modified INTEGER DEFAULT 0 NOT NULL,
    deleted INTEGER DEFAULT 0 NOT NULL,
    moved INTEGER DEFAULT 0 NOT NULL,
    notified INTEGER DEFAULT 0 NOT NULL
);
```

### `incomplete_downloads` 表

断点续传表，存储未完成下载的进度信息。

```sql
CREATE TABLE incomplete_downloads (
    download_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    file_url TEXT NOT NULL,
    total_bytes INTEGER DEFAULT 0,
    downloaded_bytes INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    server_supports_range INTEGER DEFAULT 0,
    etag TEXT,
    last_modified TEXT,
    start_time INTEGER NOT NULL,
    last_update_time INTEGER NOT NULL,
    error_reason TEXT,
    
    FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE
);
```

---

## 📋 状态分类

### 1️⃣ 未下载 (Pending)

**判断条件**:
```sql
WHERE saved_to = '' 
  AND download_status = 'pending'
  AND download_attempts = 0
```

**字段值**:
| 字段 | 值 |
|------|-----|
| `saved_to` | `''` (空字符串) |
| `download_status` | `'pending'` |
| `download_attempts` | `0` |
| `consecutive_failures` | `0` |
| `last_download_at` | `0` |

**说明**: 
- 文件从未被下载过
- 或已通过 `--reset-downloaded-files` 重置为未下载状态

---

### 2️⃣ 已下载 (Success)

**判断条件**:
```sql
WHERE saved_to != '' 
  AND download_status = 'success'
  AND consecutive_failures = 0
```

**字段值**:
| 字段 | 值 |
|------|-----|
| `saved_to` | `/path/to/file.pdf` (实际路径) |
| `download_status` | `'success'` |
| `consecutive_failures` | `0` |
| `last_download_at` | `1700000000` (时间戳) |
| `last_failed_reason` | `NULL` |

**说明**:
- 文件已成功下载并保存到磁盘
- 下载路径已记录
- 失败计数器已重置

---

### 3️⃣ 下载失败 (Failed)

**判断条件**:
```sql
WHERE download_status = 'failed'
  AND consecutive_failures >= 1
```

**字段值**:
| 字段 | 值 |
|------|-----|
| `saved_to` | **目标路径** (如 `/path/to/file.pdf`) ⭐ |
| `download_status` | `'failed'` |
| `download_attempts` | `≥1` |
| `consecutive_failures` | `≥1` |
| `last_failed_at` | `1700000000` (时间戳) |
| `last_failed_reason` | `'网络错误: 连接超时'` |

**重要说明** ⭐:
- **即使下载失败，`saved_to` 也会记录文件本应被下载到的目标路径！**
- 这是因为在下载开始前（`_prepare_download` 阶段），`set_path()` 方法就已经设置了目标路径
- 下载失败时，虽然文件被删除，但 `saved_to` 字段保留了路径信息
- 这个设计便于：重试时直接使用记录的路径、失败文件统计、错误诊断

**重试策略**:
```python
if consecutive_failures < 5:
    # 允许自动重试
    can_retry = True
else:
    # 需要人工干预
    can_retry = False
    require_manual_action = True
```

**说明**:
- 文件下载失败
- 记录了失败原因
- 支持自动重试（失败次数 < 5）
- 超过 5 次需要人工干预

---

### 4️⃣ 部分下载 (Incomplete)

**判断条件**:
```sql
-- 存在于 incomplete_downloads 表中
SELECT * FROM incomplete_downloads 
WHERE status = 'pending'
  AND attempts < 5
  AND server_supports_range = 1
```

**字段值**:
| 字段 | 值 |
|------|-----|
| `file_id` | `123` (关联 files 表) |
| `downloaded_bytes` | `5242880` (5 MB) |
| `total_bytes` | `10485760` (10 MB) |
| `status` | `'pending'` |
| `server_supports_range` | `1` |
| `etag` | `"abc123"` |

**断点续传条件**:
1. ✅ `server_supports_range = 1` (服务器支持 Range 请求)
2. ✅ `downloaded_bytes > 0` (已有部分数据)
3. ✅ `attempts < 5` (重试次数未超限)
4. ✅ 文件未被修改 (通过 ETag/Last-Modified 验证)

**说明**:
- 下载中断（网络问题、用户中断等）
- 可以从断点处继续下载
- 需要服务器支持 HTTP Range 请求

---

## 🔄 状态转换流程

```
┌─────────────┐
│ 新文件入库   │
│ (pending)   │
└──────┬──────┘
       │ download_status = 'pending'
       │ saved_to = ''
       │ download_attempts = 0
       ▼
┌─────────────┐
│ 开始下载     │
└──────┬──────┘
       │ download_attempts++
       ▼
    ┌──────┐
    │下载中 │
    └──┬───┘
       │
   ┌───┴────┬─────────┐
   │        │         │
   ▼        ▼         ▼
┌─────┐ ┌──────┐ ┌───────┐
│成功  │ │失败   │ │中断   │
└──┬──┘ └───┬──┘ └───┬───┘
   │        │        │
   │        │        ▼
   │        │   ┌────────────┐
   │        │   │保存到       │
   │        │   │incomplete_ │
   │        │   │downloads   │
   │        │   └─────┬──────┘
   │        │         │
   │        │    ┌────┴────┐
   │        │    │ 断点续传 │
   │        │    └────┬────┘
   │        │         │
   │        │    ┌────┴────┐
   │        │    │完成/失败 │
   │        │    └─────────┘
   │        │
   ▼        ▼
┌─────────────┐  ┌──────────────┐
│download_    │  │download_     │
│status =     │  │status =      │
│'success'    │  │'failed'      │
│             │  │              │
│saved_to =   │  │saved_to =    │
│'/path'      │  │'/path' ⭐    │
│             │  │              │
│consecutive_ │  │consecutive_  │
│failures = 0 │  │failures++    │
│             │  │              │
│             │  │last_failed_  │
│             │  │reason = '...'│
└─────────────┘  └──────┬───────┘
                        │
                 注意：失败时也保留 saved_to！
                        │
                   ┌────┴────┐
                   │ <5 次？  │
                   └────┬────┘
                        │
              ┌─────────┼─────────┐
              │ Yes             │ No
              ▼                 ▼
        ┌──────────┐     ┌───────────┐
        │允许重试   │     │需要人工   │
        │回到下载   │     │干预       │
        └──────────┘     └───────────┘
```

---

## 📝 实际查询示例

### 查询未下载文件

```sql
SELECT course_fullname, section_name, content_filename
FROM files
WHERE saved_to = '' 
  AND download_status = 'pending'
ORDER BY course_id, section_name;
```

### 查询已下载文件

```sql
SELECT course_fullname, section_name, content_filename, saved_to
FROM files
WHERE saved_to != '' 
  AND download_status = 'success'
ORDER BY last_download_at DESC;
```

### 查询失败文件（带重试次数）

```sql
SELECT 
    course_fullname,
    section_name,
    content_filename,
    consecutive_failures,
    last_failed_reason,
    CASE 
        WHEN consecutive_failures < 5 THEN '可重试'
        ELSE '需人工干预'
    END as retry_status
FROM files
WHERE download_status = 'failed'
ORDER BY consecutive_failures DESC, last_failed_at DESC;
```

### 查询可断点续传的文件

```sql
SELECT 
    f.content_filename,
    i.downloaded_bytes,
    i.total_bytes,
    ROUND(i.downloaded_bytes * 100.0 / i.total_bytes, 2) as progress_percent,
    i.attempts
FROM incomplete_downloads i
JOIN files f ON i.file_id = f.file_id
WHERE i.status = 'pending'
  AND i.attempts < 5
  AND i.server_supports_range = 1
ORDER BY progress_percent DESC;
```

### 统计各课程下载情况

```sql
SELECT 
    course_fullname,
    COUNT(*) as total_files,
    SUM(CASE WHEN download_status = 'success' THEN 1 ELSE 0 END) as downloaded,
    SUM(CASE WHEN download_status = 'failed' THEN 1 ELSE 0 END) as failed,
    SUM(CASE WHEN download_status = 'pending' THEN 1 ELSE 0 END) as pending,
    ROUND(
        SUM(CASE WHEN download_status = 'success' THEN 1 ELSE 0 END) * 100.0 / COUNT(*),
        2
    ) as success_rate
FROM files
GROUP BY course_fullname
ORDER BY success_rate DESC;
```

---

## 🔍 代码实现位置

### 保存成功文件

**位置**: `moodle_dl/database.py` (第 802 行)

```python
def save_file(self, file: File, course_id: int, course_fullname: str):
    # 保存成功下载的文件
    # - 更新 saved_to 路径
    # - 设置 download_status = 'success'
```

### 保存失败文件

**位置**: `moodle_dl/database.py` (第 980 行)

```python
def save_failed_file(self, file: File, course_id: int, 
                     course_fullname: str, error_message: str):
    # 记录下载失败的文件
    # - 设置 download_status = 'failed'
    # - 增加 consecutive_failures
    # - 记录 last_failed_reason
```

### 标记下载成功

**位置**: `moodle_dl/database.py` (第 1066 行)

```python
def mark_download_success(self, file: File, course_id: int):
    # 标记文件下载成功
    # - 设置 download_status = 'success'
    # - 重置 consecutive_failures = 0
    # - 清除 last_failed_reason
```

### 保存断点续传信息

**位置**: `moodle_dl/database.py` (第 1310 行)

```python
def save_incomplete_download(self, file_id: int, file_path: str, 
                             file_url: str, downloaded_bytes: int, 
                             total_bytes: int, ...):
    # 保存未完成下载的进度
    # 用于断点续传
```

### 下载服务回调

**位置**: `moodle_dl/downloader/download_service.py` (第 214 行)

```python
def status_callback(self, event: DlEvent, task: Task, **extra_args):
    if event == DlEvent.FAILED:
        # 下载失败 → 调用 save_failed_file()
        self.database.save_failed_file(...)
    elif event == DlEvent.FINISHED:
        # 下载成功 → 调用 save_file() + mark_download_success()
        self.database.save_file(...)
        self.database.mark_download_success(...)
```

---

## 📊 索引优化

为提高查询性能，数据库创建了以下索引：

```sql
-- files 表索引
CREATE INDEX idx_files_saved_to ON files(saved_to);
CREATE INDEX idx_download_status ON files(download_status);
CREATE INDEX idx_consecutive_failures ON files(consecutive_failures);
CREATE INDEX idx_files_time_stamp ON files(time_stamp);
CREATE INDEX idx_course_id ON files(course_id);

-- incomplete_downloads 表索引
CREATE INDEX idx_incomplete_status ON incomplete_downloads(status);
CREATE INDEX idx_incomplete_attempts ON incomplete_downloads(attempts);
CREATE INDEX idx_incomplete_last_update ON incomplete_downloads(last_update_time);
```

**性能影响**:
- 按状态查询: 从 O(n) → O(log n)
- 按课程统计: 从 全表扫描 → 索引扫描
- 查询失败文件: 快 10-100 倍

---

## 🎯 设计亮点

### 1. 双重标识系统

**优点**:
- ✅ `saved_to`: 传统方式，向后兼容
- ✅ `download_status`: 明确状态，便于查询
- ✅ 两者结合，确保准确性

**示例**:
```python
# 方式 1: 传统判断
is_downloaded = file.saved_to != ''

# 方式 2: 状态判断
is_downloaded = file.download_status == 'success'

# 最佳实践: 两者结合
is_downloaded = (file.saved_to != '' and 
                 file.download_status == 'success')
```

### 2. 失败追踪机制

**功能**:
- ✅ 记录连续失败次数
- ✅ 保存失败原因（最多 500 字符）
- ✅ 支持智能重试策略
- ✅ 超过阈值需人工干预

**重试策略**:
```python
def can_retry(file) -> bool:
    return (file.download_status == 'failed' and 
            file.consecutive_failures < 5)
```

### 3. 断点续传支持

**优点**:
- ✅ 独立表存储进度
- ✅ 支持 HTTP Range 请求
- ✅ ETag/Last-Modified 验证
- ✅ 自动清理完成的记录

**流程**:
```python
# 1. 下载中断时保存进度
save_incomplete_download(file_id, path, url, 
                         downloaded_bytes, total_bytes)

# 2. 重新下载时检查
incomplete_info = get_incomplete_download(file_id)
if incomplete_info and server_supports_range:
    # 从断点处继续
    resume_from = incomplete_info['downloaded_bytes']
    
# 3. 完成后清理
mark_download_complete(file_id)
```

---

## 🔧 常见操作

### 重置所有文件为未下载

```bash
moodle-dl --reset-downloaded-files
```

**数据库操作**:
```sql
UPDATE files
SET saved_to = '',
    download_status = 'pending',
    time_stamp = <current_time>,
    modified = 0,
    moved = 0,
    notified = 0
WHERE saved_to != '';
```

### 重试失败的文件

失败文件会在下次运行 `moodle-dl` 时自动重试（如果 `consecutive_failures < 5`）。

### 清理旧的断点续传记录

```bash
# 自动清理超过 7 天的未完成下载记录
# 在 database.py 中实现
cleanup_old_incomplete_downloads(days_old=7)
```

---

## ⭐ 重要细节 1：失败文件的 saved_to 处理

### 执行时间线

```
1. Task 初始化
   └─ file.saved_to = '' (空字符串)

2. Task.run() 执行
   └─ 进入 _prepare_download() 阶段

3. 设置目标路径 ⭐
   └─ self.set_path()
   └─ file.saved_to = '/full/path/to/file.pdf'

4. 开始下载
   └─ _execute_download()
   └─ file.saved_to 保持不变

5. 下载失败（异常）
   └─ 进入 _handle_error()
   └─ PT.remove_file(file.saved_to)  # 删除文件
   └─ file.saved_to 仍然保留路径！ ⭐

6. 保存失败记录
   └─ save_failed_file(task.file, ...)
   └─ 数据库记录包含 saved_to 路径 ⭐
```

### 为什么失败时也记录 saved_to？

1. **支持智能重试**: 重试时无需重新计算路径
2. **失败文件统计**: 可以查询哪些文件在哪个位置失败
3. **错误诊断**: 管理员可以检查路径权限/空间问题
4. **数据一致性**: 成功和失败的文件都有 saved_to
5. **恢复下载**: 修复问题后可以继续下载到原路径

### 代码位置

```python
# moodle_dl/downloader/task.py

async def _prepare_download(self) -> bool:
    """准备下载环境"""
    PT.make_dirs(self.destination)
    
    # ⭐ 关键：在下载前就设置目标路径
    self.set_path()  # Line 1261
    logging.debug('[%d] Starting downloading of: %s', 
                  self.task_id, self.file.saved_to)
    # ... 继续下载 ...

async def _handle_error(self, dl_err: Exception):
    """错误处理"""
    # 删除失败的文件
    PT.remove_file(self.file.saved_to)  # Line 1418
    
    # ⭐ 但 self.file.saved_to 仍然保留路径值
    self.report_failure()  # 触发 FAILED 事件
```

---

## ⭐ 重要细节 2：下载成功后不删除失败记录

### 核心行为

**❌ 不会删除失败记录**  
**✅ 而是 UPDATE（更新）同一条记录**

### 状态转换

```
失败记录 (file_id=12345)
├─ download_status = 'failed'
├─ download_attempts = 1
├─ consecutive_failures = 1
├─ last_failed_reason = 'Network timeout'
└─ last_failed_at = 1700000000

         ↓ 重试下载成功

同一条记录 (file_id=12345) ⭐
├─ download_status = 'success'        ✅ 已更新
├─ download_attempts = 1              ⭐ 保留历史
├─ consecutive_failures = 0           ✅ 已重置
├─ last_failed_reason = NULL          ✅ 已清除
└─ last_failed_at = 1700000000        ⭐ 保留历史
```

### SQL 操作

```sql
-- 下载成功时执行 UPDATE，而不是 DELETE
UPDATE files
SET download_status = 'success',
    last_download_at = ?,
    consecutive_failures = 0,
    last_failed_reason = NULL
WHERE course_id = ?
  AND module_id = ?
  AND content_fileurl = ?;
```

### 保留的历史信息

| 字段 | 失败时 | 成功后 | 用途 |
|------|--------|--------|------|
| `download_attempts` | 1, 2, 3... | **保持不变** ⭐ | 统计：经过几次尝试才成功 |
| `last_failed_at` | 时间戳 | **保持不变** ⭐ | 审计：曾经何时失败过 |
| `consecutive_failures` | 1, 2, 3... | **重置为 0** ✅ | 重试逻辑判断 |
| `last_failed_reason` | 错误信息 | **清除 (NULL)** ✅ | 错误诊断 |
| `download_status` | 'failed' | **更新为 'success'** ✅ | 状态查询 |

### 设计优势

#### 1️⃣ 简化数据库结构
```
每个文件 = 一条记录（file_id 不变）
vs
每次失败/成功 = 新记录（file_id 不断变化）
```

#### 2️⃣ 保留审计追踪
```sql
-- 查询曾经失败但最终成功的文件
SELECT 
    content_filename,
    download_attempts,
    last_failed_at,
    last_download_at
FROM files 
WHERE download_status = 'success'
  AND download_attempts > 1;  -- 尝试次数 > 1 说明曾失败过
```

#### 3️⃣ 便于统计分析
```sql
-- 统计下载成功率
SELECT 
    CASE 
        WHEN download_attempts = 1 THEN '一次成功'
        WHEN download_attempts <= 3 THEN '重试后成功（1-3次）'
        ELSE '重试多次后成功（>3次）'
    END as success_type,
    COUNT(*) as count
FROM files 
WHERE download_status = 'success'
GROUP BY success_type;
```

#### 4️⃣ 查询性能更好
- 无需 JOIN 多表
- 无需聚合历史记录
- 直接查询单表即可

### 代码位置

```python
# moodle_dl/downloader/download_service.py (Line 228)

elif event == DlEvent.FINISHED:
    # 1. 保存文件信息
    self.database.save_file(task.file, task.course.id, task.course.fullname)
    
    # 2. 标记下载成功（UPDATE，不是 DELETE）
    self.database.mark_download_success(task.file, task.course.id)
    
    self.status.files_downloaded += 1
```

```python
# moodle_dl/database.py (Line 1055)

def mark_download_success(self, file: File, course_id: int):
    """标记文件下载成功，重置失败计数器"""
    
    cursor.execute(
        """UPDATE files  -- ⭐ UPDATE 而非 DELETE
        SET download_status = 'success',
            last_download_at = ?,
            consecutive_failures = 0,
            last_failed_reason = NULL
        WHERE course_id = ?
          AND module_id = ?
          AND content_fileurl = ?
        """,
        (current_time, course_id, file.module_id, file.content_fileurl)
    )
```

---

## 📚 相关文档

- `moodle_dl/database.py` - 数据库核心实现
- `moodle_dl/downloader/download_service.py` - 下载服务
- `moodle_dl/downloader/task.py` - 下载任务（包含 set_path 逻辑）
- `moodle_dl/cli/database_manager.py` - 数据库管理工具
- `IMPROVEMENT_RECOMMENDATIONS.md` - 改进建议（包含数据库优化）

---

## 📝 总结

### 状态分类方式

| 状态 | 主要判断 | 辅助判断 |
|------|---------|---------|
| **未下载** | `saved_to = ''` | `download_status = 'pending'` |
| **已下载** | `saved_to != ''` | `download_status = 'success'` |
| **下载失败** | `download_status = 'failed'` | `consecutive_failures >= 1` |
| **部分下载** | 存在于 `incomplete_downloads` | `status = 'pending'` |

### 关键特性

✅ **双重标识**: 路径 + 状态，确保准确  
✅ **失败追踪**: 次数 + 原因，智能重试  
✅ **断点续传**: 独立表 + Range 请求  
✅ **性能优化**: 关键字段建立索引  
✅ **向后兼容**: 保留传统 `saved_to` 判断  

---

**文档版本**: 1.0  
**最后更新**: 2025年11月  
**维护者**: AI Assistant

