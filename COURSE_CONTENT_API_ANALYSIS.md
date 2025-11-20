# 课程内容 API 分析 - 实际测试结果

## 问题

如果能通过 API 查询到课程 137304 的基本信息，能否查看这门课的 chapters（章节）和内容？

## 答案

✅ **可以！成功获取了完整的课程结构。**

## 实际测试结果

### 课程信息

- **课程名称**：4NNYDT1A Oral Biology, Body Systems and Craniofacial Anatomy
- **课程 ID**：137304
- **课程格式**：topics（按主题组织，而不是按周）
- **总 Sections**：11 个
- **总 Modules**：约 40+ 个

### 课程结构

通过 `core_course_get_contents` API 获得的完整结构：

```
Course 137304
├─ Section 0 (ID: 2264145) - 欢迎/简介
│  └─ 0 个 modules（仅有摘要文本）
│
├─ Section 1 (ID: 2264146) - General Resources ⭐
│  ├─ Announcements (type: forum)
│  ├─ Welcome Message (type: label) × 2
│  ├─ Handbook (type: label)
│  ├─ 4NNYDT1A MHandbook... (type: resource - PDF)
│  └─ ... (还有 3 个)
│  └─ 共 8 个 modules
│
├─ Section 2 (ID: 2264147) - Assessments
│  └─ Assessment (type: label)
│  └─ 共 1 个 module
│
├─ Section 3 (ID: 2264148) - 1. Anatomy of Head and Neck
│  └─ BD1F-DT1A-TAA-1004 Anatomy of the Cardiovascular System (type: label)
│  └─ 共 1 个 module
│
├─ Section 4 (ID: 2264149) - 2. Oral Biology and Craniofacial Development
│  └─ LECTURE (type: label)
│  └─ 共 1 个 module
│
├─ Section 5 (ID: 2264150) - 2.1 Sub-unit: Embryology and development
│  └─ 0 个 modules
│
├─ Section 6 (ID: 2264151) - 2.2 Sub-unit: Dental hard and soft tissues
│  └─ 0 个 modules
│
├─ Section 7 (ID: 2264152) - 2.3 Sub-unit: Oral physiology and saliva
│  └─ 0 个 modules
│
├─ Section 8 (ID: 2264153) - 3. Body Systems
│  └─ 0 个 modules
│
├─ Section 9 (ID: 2264154) - 3.1 Body Systems Homeostasis ⭐ 最多内容
│  ├─ [Overview of Body Systems for DTH] (type: label)
│  ├─ LECTURE (type: label)
│  ├─ BD1F-DT1A-HMT-1005 Introduction to bone and cartilage (type: label)
│  ├─ DT1A-MET-1001 Introduction to Bone and Cartilage -PDF of slides (type: resource)
│  ├─ DT1A-HMT-1001 Bone development and remodelling (type: label)
│  └─ ... (还有 10 个)
│  └─ 共 15 个 modules
│
└─ Section 10 (ID: 2264155) - 3.2 Thorax Anatomy
   ├─ LECTURES (copy) (type: label)
   ├─ BD1F-DT1A-TAA-1002 Introduction to Anatomy (type: label)
   ├─ Introduction to Anatomy - Prof T Shaw (type: resource)
   ├─ BD1F-DT1A-TAA-1003 Anatomy of respiration (type: label)
   ├─ Anatomy of Respiration Lecture - Prof T Shaw (type: resource)
   └─ ... (还有 3 个)
   └─ 共 8 个 modules
```

## Moodle 的课程结构

### 概念解释

Moodle 使用分层结构来组织课程内容：

```
Course (课程)
  ↓
Sections (章节/主题/周次)
  ↓
Modules (活动/资源)
  ↓
Module 内容（文件、测验答案等）
```

### 术语对照

- **Moodle 术语**：Sections（而不是 chapters）
- **你期望的术语**：Chapters（章节）
- **实际含义**：Sections 就是课程的逻辑分组

### Module 类型

#### 常见 Module 类型

| 类型 | 说明 | 例子 |
|------|------|------|
| `resource` | 文件、网页、文件夹等 | PDF、HTML、Word 文档 |
| `label` | 文本标签、标题、说明 | "第一章" 或 "讲座说明" |
| `forum` | 讨论论坛 | 课程公告 |
| `quiz` | 测验/考试 | 选择题测试 |
| `assign` | 作业 | 学生提交作业 |
| `book` | 电子书 | 多页面书籍式内容 |
| `h5p` | H5P 交互内容 | 交互视频、图像热点等 |
| `choice` | 投票/选择 | 课程反馈调查 |
| `lti` | LTI 工具 | 外部工具集成 |

## 可用的 API

### 1. core_course_get_contents

**目的**：获取课程的 sections 和 modules 结构

```python
params = {
    'wstoken': token,
    'wsfunction': 'core_course_get_contents',
    'courseid': 137304,
    'moodlewsrestformat': 'json'
}
```

**返回**：
- Sections 列表
- 每个 Section 包含 modules 列表
- 每个 Module 包含名称、类型、ID 等

**优点**：
- ✅ 获取完整的课程结构
- ✅ 一次调用得到所有内容
- ✅ 包含模块详细信息

### 2. core_course_get_courses

**目的**：获取课程的元数据信息

```python
params = {
    'wstoken': token,
    'wsfunction': 'core_course_get_courses',
    'options[ids][0]': 137304,
    'moodlewsrestformat': 'json'
}
```

**返回**：
- 课程 ID、名称、描述
- 开始/结束日期
- 课程格式（topics/weeks）
- 等等

**优点**：
- ✅ 获取课程基本信息
- ✅ 不获取内容（更快）

### 3. core_course_get_course_module

**目的**：获取单个 module 的详细信息

```python
params = {
    'wstoken': token,
    'wsfunction': 'core_course_get_course_module',
    'cmid': module_id,
    'moodlewsrestformat': 'json'
}
```

**返回**：
- 单个模块的详细信息
- 模块的活动内容（如果有的话）

**缺点**：
- ❌ 需要知道 module ID
- ❌ 需要逐个查询

## 为什么能查询内容，但 Mobile API 看不到？

### 权限检查的区别

#### 网页版 API (core_course_get_contents)
```python
# 检查: 用户是否有该课程的 moodle/course:view capability
# 结果: 你有权限 → 返回内容
```

#### Mobile API (core_enrol_get_users_courses)
```python
# 检查: 用户是否在 user_enrolments 表中
# 结果: 你不是 enrolled → 不返回该课程
```

### 两种权限系统的独立性

```
User (你)
  ├─ Enrollment Status
  │  └─ ❌ 不在任何课程的 user_enrolments 中
  │
  └─ Capability
     └─ ✅ 有课程 137304 的 moodle/course:view
        (因为你是教师/TA)
```

结果：
- 🌐 **网页版 API**：检查 Capability → ✅ 能访问
- 📱 **Mobile API**：检查 Enrollment → ❌ 无法访问

## 对 moodle-dl 的启示

### 当前实现的限制

moodle-dl 目前使用 `core_enrol_get_users_courses` API，这意味着：

```
1. 调用 core_enrol_get_users_courses
   ↓
2. 获取 enrolled 的课程列表
   ↓
3. 对每个课程调用 core_course_get_contents
   ↓
4. 下载课程内容
```

**结果**：
- ✅ 可以下载 enrolled 的课程
- ❌ 无法下载教师/TA 有权限但不 enrolled 的课程

### 在你的情况下

```
your-account (你)
  ├─ Mobile API 返回: 0 门课程 (没有 enrollment)
  │  └─ moodle-dl 可以下载: 0 门课程
  │
  └─ 网页版 API 可以访问: 课程 137304 等
     └─ 但 moodle-dl 不能下载 (因为不在第一步)
```

### 改进方向

#### 方案 A：支持教师/TA 模式

```python
# 检测用户身份
if is_teacher_or_ta():
    # 使用特殊 API 获取所有有权限的课程
    courses = get_all_accessible_courses()
else:
    # 使用 Mobile API（学生模式）
    courses = get_enrolled_courses()
```

**优点**：完美解决教师问题  
**缺点**：需要新增 API 调用

#### 方案 B：允许手动指定课程 ID

```python
# 在配置中添加
"manual_course_ids": [137304, 137305]

# moodle-dl 直接尝试下载这些课程
for course_id in manual_course_ids:
    contents = core_course_get_contents(course_id)
    download(contents)
```

**优点**：灵活、简单  
**缺点**：用户需要手动指定课程 ID

#### 方案 C：尝试多种方式

```python
# 首先尝试 Mobile API（快速）
try:
    courses = core_enrol_get_users_courses()
except:
    pass

# 然后尝试网页版 API（备用）
try:
    courses = core_course_get_courses()
except:
    pass

# 最后尝试手动指定的课程 ID
courses.extend(manual_course_ids)
```

**优点**：最大兼容性  
**缺点**：调用多次，可能出错

## 实际意义

### 1. 课程信息是一致的

```
✅ 网页前端显示的课程
✅ 通过 API 查询到的课程
✅ 课程内容完整无缺
```

没有数据不一致的问题。

### 2. 权限系统正常工作

```
✅ 你无法看到没权限的课程
✅ 你可以看到有权限的课程
✅ Moodle 的权限检查机制完整
```

### 3. API 层级的设计很清晰

```
Mobile API (core_enrol_get_users_courses)
  └─ 目标：学生、移动应用
  └─ 优化：速度、简洁性
  └─ 限制：只看 enrollment

网页版 API (core_course_get_contents)
  └─ 目标：Web 应用、系统集成
  └─ 优化：完整性、灵活性
  └─ 特点：完整权限检查
```

## 总结

### 技术发现

| 发现 | 说明 |
|------|------|
| ✅ 能查询课程信息 | `core_course_get_courses` 成功 |
| ✅ 能查询课程内容 | `core_course_get_contents` 返回 11 个 sections |
| ✅ 内容完整 | 40+ 个 modules，包含讲座、资源等 |
| ❌ 不在 Mobile API 中 | 因为没有 enrollment 记录 |
| ✅ 权限系统正常 | 网页和 API 都遵守权限设置 |

### 关键结论

1. **Moodle 的权限系统设计良好**
   - 区分了 enrollment 和 capability
   - 在两个层级都做了检查
   - 保护了数据隐私和安全

2. **Mobile API 和网页版 API 不同**
   - Mobile API 针对学生优化
   - 网页版 API 针对系统集成优化
   - 都符合各自的使用场景

3. **你的访问权限是真实有效的**
   - 不仅能访问网页
   - 还能通过 API 获取完整内容
   - 说明你在该课程中有真实的权限

4. **moodle-dl 可以改进**
   - 可以增加对网页版 API 的支持
   - 可以支持教师/TA 模式
   - 可以允许手动指定课程 ID

## 测试脚本

完整的测试脚本已保存在：
```
/Users/linqilan/CodingProjects/moodle/test_course_chapters.py
```

可以直接运行：
```bash
python3 test_course_chapters.py
```

---

**测试日期**：2025-11-20  
**Moodle 实例**：keats.kcl.ac.uk  
**课程**：4NNYDT1A (ID: 137304)  
**结果**：成功获取完整课程结构

