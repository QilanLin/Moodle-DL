# 状态管理中断问题全面检查报告

## 🔍 检查范围

检查代码库中所有可能在操作中途中断导致状态丢失的场景。

---

## ✅ 已发现并修复的问题

### 1️⃣ --retry-failed 中断问题 ✅ 已修复

**问题描述：**
```bash
# 步骤 1: 运行重试
moodle-dl --retry-failed
# 有 100 个失败文件

# 步骤 2: 重置所有状态为 'pending'
reset_failed_file_for_retry() 把 100 个文件从 'failed' 改为 'pending'

# 步骤 3: 下载了 10 个后按 Ctrl+C 中断

# 步骤 4: 重新运行
moodle-dl --retry-failed
# 问题：那 90 个 'pending' 状态的文件不会被找到！
```

**修复方案：**
- 使用 `retrying` 状态代替 `pending`
- 查询条件改为 `WHERE download_status IN ('failed', 'retrying')`
- 中断后重新运行会继续找到 `retrying` 状态的文件

**影响范围：**
- `reset_failed_file_for_retry()`
- `get_failed_files()`
- `get_failed_files_with_course_info()`
- `get_failed_files_summary()`

**Commit:** `006d90b`

---

## ✅ 正常下载流程检查（无问题）

### 2️⃣ 首次下载流程 ✅ 无问题

**流程分析：**
```
步骤 1: 发现新文件
  └─ new_file() → 插入数据库，download_status = 'pending'
  
步骤 2: 创建下载任务
  └─ Task.__init__() → 初始化任务对象
  
步骤 3: 顺序执行下载（单线程）
  └─ for task in all_tasks:
        await task.run()
        ├─ 成功 → DlEvent.FINISHED → save_file() + mark_download_success()
        │         status = 'success' ✅
        └─ 失败 → DlEvent.FAILED → save_failed_file()
                  status = 'failed' ✅

步骤 4: Ctrl+C 中断
  └─ 已下载的文件: status = 'success' ✅
  └─ 已失败的文件: status = 'failed' ✅
  └─ 未处理的文件: status = 'pending' ✅ (等待下次运行)
```

**为什么没问题：**
1. **即时状态更新**：每个文件下载完成（成功/失败）后立即更新数据库
2. **原子性操作**：每个文件的状态更新是独立的事务
3. **不提前改状态**：不会在下载前批量改变所有文件的状态
4. **幂等性**：中断后重新运行，`pending` 状态的文件会继续被处理

**代码证据：**
```python
# download_service.py:214-237
def status_callback(self, event: DlEvent, task: Task, **extra_args):
    if event == DlEvent.FAILED:
        # 立即保存失败状态
        self.database.save_failed_file(...)
    elif event == DlEvent.FINISHED:
        # 立即保存成功状态
        self.database.save_file(...)
        self.database.mark_download_success(...)
```

---

## ✅ 其他潜在场景检查

### 3️⃣ 断点续传 (Incomplete Downloads) ✅ 无问题

**机制：**
```sql
-- 独立的 incomplete_downloads 表
CREATE TABLE incomplete_downloads (
    download_id INTEGER PRIMARY KEY,
    file_id INTEGER,
    downloaded_bytes INTEGER,
    total_bytes INTEGER,
    status TEXT DEFAULT 'pending',
    ...
);
```

**流程：**
1. 下载中断 → 保存进度到 `incomplete_downloads` 表
2. 原 `files` 表的状态保持 `pending`（未改变）
3. 重新运行 → 从 `incomplete_downloads` 读取进度
4. 完成下载 → 清理 `incomplete_downloads` 记录

**为什么没问题：**
- 断点续传信息存储在独立表中
- 不影响主 `files` 表的状态
- 中断后可以恢复

---

### 4️⃣ 批量删除文件 ✅ 无问题

**代码：**
```python
# download_service.py:246
def real_run(self):
    # 在下载开始前删除应该删除的文件
    self.database.batch_delete_files(self.courses)
```

**为什么没问题：**
- 删除操作在下载开始前完成
- 是一次性操作，不是逐步改变状态
- 中断不会影响已标记 `deleted=1` 的文件

---

### 5️⃣ 文件修改/移动 ✅ 无问题

**流程：**
```python
# database.py:1023-1028
def move_file(self, old_file: File, new_file: File, ...):
    # 更新旧文件：设置 moved=1
    UPDATE files SET notified=0, moved=1 WHERE file_id=?
    # 插入新文件：download_status='pending'
    INSERT INTO files ...
```

**为什么没问题：**
- 原子性：每个文件的移动是独立操作
- 新旧文件状态独立处理
- 中断不会导致状态混乱

---

## 🎯 检查结论

### ✅ 已确认的问题

| 场景 | 问题 | 状态 | Commit |
|------|------|------|--------|
| `--retry-failed` 中断 | 失败文件丢失 | ✅ 已修复 | `006d90b` |

### ✅ 已确认安全的场景

| 场景 | 原因 | 状态 |
|------|------|------|
| 首次下载中断 | 即时状态更新，不提前批量改状态 | ✅ 无问题 |
| 断点续传 | 独立表管理，不影响主状态 | ✅ 无问题 |
| 批量删除 | 下载前完成，一次性操作 | ✅ 无问题 |
| 文件移动 | 原子性操作，独立处理 | ✅ 无问题 |

---

## 🔑 关键设计原则

### ✅ 好的模式（首次下载）

```python
# 不提前改状态，处理时才改
for file in pending_files:
    try:
        download(file)
        # 成功后立即更新状态
        mark_success(file)  # pending → success
    except:
        # 失败后立即更新状态
        mark_failed(file)   # pending → failed
    # 未处理的保持 pending
```

**优点：**
- ✅ 中断后状态清晰：success/failed/pending
- ✅ 幂等性：重新运行继续处理 pending
- ✅ 不丢失任何文件

---

### ❌ 坏的模式（旧的 retry-failed）

```python
# 提前批量改状态
for file in failed_files:
    reset_status(file)  # failed → pending (❌ 问题在这！)

# 然后开始下载
for file in reset_files:
    download(file)
    # 如果这里中断，pending 状态的文件下次找不到！
```

**问题：**
- ❌ 提前批量改状态
- ❌ 中断后 pending 文件不在 failed 列表
- ❌ 下次运行找不到这些文件

---

### ✅ 修复后的模式

```python
# 使用专门的 'retrying' 状态
for file in failed_files:
    reset_status(file)  # failed → retrying (✅ 新状态！)

# 下载时
for file in retrying_files:
    download(file)
    # 中断后，retrying 状态的文件仍可被找到！

# 查询条件
WHERE download_status IN ('failed', 'retrying')
```

**优点：**
- ✅ 中断后可恢复
- ✅ retrying 状态明确标识"正在重试"
- ✅ 不丢失文件

---

## 🛡️ 预防措施建议

### 1. 状态转换原则

**❌ 避免：**
- 提前批量改变状态
- 使用通用状态（如 `pending`）标记特殊操作

**✅ 推荐：**
- 即时更新（处理时才改状态）
- 使用专门状态（如 `retrying`、`downloading`）

### 2. 查询覆盖

**确保查询包含所有中间状态：**
```sql
-- ❌ 不完整
WHERE download_status = 'failed'

-- ✅ 完整
WHERE download_status IN ('failed', 'retrying')
```

### 3. 幂等性

**确保操作可重复执行：**
- 同一个 `pending` 文件可以多次尝试下载
- 同一个 `retrying` 文件可以继续重试
- 重新运行不会导致状态混乱

---

## 📊 影响评估

### 修复前

```
用户场景：下载 100 个失败文件，中途中断
├─ 已下载: 10 个 ✅ (状态正确)
├─ 还未下载: 90 个 ❌ (状态 pending，下次找不到)
└─ 重新运行: 只重试 0 个 ❌ (丢失 90 个文件)
```

### 修复后

```
用户场景：下载 100 个失败文件，中途中断
├─ 已下载: 10 个 ✅ (状态 success)
├─ 还未下载: 90 个 ✅ (状态 retrying，可被找到)
└─ 重新运行: 继续重试 90 个 ✅ (不丢失文件)
```

---

## 📝 总结

1. **已发现 1 个问题**：`--retry-failed` 中断后文件丢失 ✅ 已修复
2. **其他场景均正常**：首次下载、断点续传、批量删除、文件移动
3. **修复策略有效**：引入 `retrying` 状态解决中断问题
4. **无需额外修复**：其他场景设计合理，不会出现类似问题

**建议：**
- 保持当前的即时状态更新机制
- 避免引入新的"提前批量改状态"操作
- 如需新的批量操作，使用专门的中间状态

---

## 🔧 相关 Commits

- `006d90b` - 修复 --retry-failed 中断后失败文件丢失的问题

