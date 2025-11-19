# 📚 JSON 元数据文件解释

**日期**: 2025-11-19  
**相关文件**: `moodle_dl/moodle/result_builder.py`

---

## ❓ 问题

为什么下载课程内容时会看到 `.json` 文件，比如：
- `02 [Mandatory] Week 1 - Recorded Lecture 1 Handouts.json`
- `05 [Mandatory] Week 1 - Recorded Lecture 2 Handouts.json`
- `[Mandatory] Week 1 - Large Group Tutorial Handouts.json`

---

## 📖 答案

这些 `.json` 文件**不是** Moodle 服务器上的真实文件，而是 `moodle-dl` 在下载过程中**自动生成的元数据文件**。

---

## 🔍 详细解释

### 1️⃣ 元数据文件的来源

当 `moodle-dl` 下载 Resource 模块中的文件（PDF、MP4、Word 文档等）时，它同时创建一个对应的 `.json` 元数据文件。

**代码位置**: `moodle_dl/moodle/mods/resource.py` (第 176 行)

```python
resource_files.append(self.create_metadata_file(metadata))
```

### 2️⃣ 元数据文件包含的信息

每个 `.json` 文件包含该资源的完整 Moodle 元数据：

```json
{
  "resource_id": 9161613,
  "course_id": 134659,
  "module_id": 9161613,
  "name": "[Mandatory] Week 1 - Recorded Lecture 1 Handouts",
  "display": {
    "mode": "Download",
    "mode_name": "Download",
    "options": {
      "showsize": true,
      "showtype": true,
      "showdate": true
    }
  },
  "file_info": {
    "total_files": 1,
    "total_size": 2048000,
    "details": [
      {
        "filename": "[Mandatory] Week 1 - Recorded Lecture 1 Handouts.pdf",
        "mimetype": "application/pdf",
        "filesize": 2048000,
        "timemodified": 1634567890
      }
    ]
  },
  "settings": {
    "revision": 1,
    "tobemigrated": 0,
    "legacyfiles": 0
  },
  "timestamps": {
    "timemodified": 1634567890
  },
  "features": {
    "archetype": "resource",
    "intro_support": true,
    "completion_tracks_views": true
  }
}
```

### 3️⃣ 其他模块也这样做

不仅是 Resource 模块，很多其他模块也生成类似的元数据文件：

| 模块 | 生成的文件 |
|------|----------|
| **Resource** | `filename.json` |
| **Glossary** | `term_metadata.json` |
| **Data** | `entry_metadata.json` |
| **Chat** | `session_*.json` |
| **Feedback** | `questions.json`, `analysis.json` |
| **Lesson** | `grade`, 答案和反馈 |
| **Quiz** | `questions.json` |

### 4️⃣ 为什么不删除这些文件？

✅ **完整性**: 保留所有 Moodle 数据和元数据  
✅ **调试**: 元数据有助于理解和验证数据  
✅ **分析**: 可以分析课程结构和配置  
✅ **备份**: 完整保存所有信息，防止数据丢失  
✅ **恢复**: 如果主文件丢失，元数据可帮助恢复  

---

## 🔧 改进措施

### ✅ 已实现

从 v8 版本开始，`.json` 文件被正确识别为**系统文件**：

1. **不添加索引前缀**
   - 修改前: `03 [Mandatory] Week 1 - Recorded Lecture 1 Handouts.json`
   - 修改后: `[Mandatory] Week 1 - Recorded Lecture 1 Handouts.json` ✅

2. **改进的系统文件识别**
   ```python
   @staticmethod
   def _is_system_file(filename: str) -> bool:
       # 所有 .json 文件都被识别为系统文件
       if filename_lower.endswith('.json'):
           return True
       # ... 其他检查
   ```

3. **位置索引现在正确分配**
   ```
   02 [Mandatory] Week 1 - Recorded Lecture 1 Handouts.pdf       (索引: 02) ✅
      [Mandatory] Week 1 - Recorded Lecture 1 Handouts.json       (无索引)   ✅
   03 [Mandatory] Week 1 - Recorded Lecture 2 Handouts.pdf       (索引: 03) ✅
      [Mandatory] Week 1 - Recorded Lecture 2 Handouts.json       (无索引)   ✅
   ```

### 🚀 未来可能的改进

1. **配置选项**: 添加 "下载元数据文件" 的开关（默认: 是）
2. **目录隔离**: 将元数据文件分离到 `.metadata/` 子目录
3. **压缩存储**: 可选择将所有元数据打包为单个 archive
4. **查询工具**: 提供工具查询和分析元数据

---

## 📊 测试验证

所有测试都已更新并通过验证：

```
test_is_system_file ✅
  - 隐藏文件（.hidden、.DS_Store）识别为系统文件
  - 所有 .json 文件识别为系统文件
  - 普通文件（.pdf、.mp4、.doc）不识别为系统文件
```

详见: `tests/test_filename_prefix_indexing.py`

---

## 💡 关键知识点

| 特性 | 说明 |
|------|------|
| **生成时机** | 下载 Resource 模块文件时自动生成 |
| **命名规则** | 与主文件同名，只改为 `.json` 扩展名 |
| **大小** | 通常只有几 KB（包含元数据） |
| **位置** | 与主文件在同一目录 |
| **索引前缀** | ❌ 不添加前缀（系统文件） |
| **用途** | 保留文件的 Moodle 元数据 |
| **删除安全性** | 🟢 安全删除，不影响功能 |

---

## 🎯 总结

`.json` 元数据文件是 moodle-dl 的**设计特性**，用于完整保留课程数据。现在它们被正确识别和处理，不会获得前缀索引。

如果不需要这些文件，可以安全删除，但保留它们有助于：
- 🔍 了解文件信息
- 📊 分析课程结构
- 🛡️ 完整备份数据

---


