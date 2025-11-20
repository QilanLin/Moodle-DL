# API 选择详解：为什么用网页版 API 而不是 Mobile API

## 问题

你问的很好：

> 提取课程基本信息用的是网页版 API 还是 Mobile API？
> 提取课程结构用的是网页版 API 还是 Mobile API？

## 直接答案

### 步骤 1：提取课程基本信息

```python
# 函数: core_course_get_courses
# API 类型: ✅ 网页版 API (Web Services API)
# 不是: ❌ Mobile API

# 调用代码:
wsfunction = "core_course_get_courses"
options[ids][0] = 137304

# 返回数据:
{
    "id": 137304,
    "fullname": "4NNYDT1A Oral Biology, Body Systems and Craniofacial Anatomy",
    "shortname": "4NNYDT1A 25~26 000001 ORAL BIOLOGY, B",
    "format": "topics",
    "startdate": 1756715580,
    "enddate": 1788165180,
    ...
}
```

### 步骤 2：提取课程结构

```python
# 函数: core_course_get_contents
# API 类型: ✅ 网页版 API (Web Services API)
# 不是: ❌ Mobile API

# 调用代码:
wsfunction = "core_course_get_contents"
courseid = 137304

# 返回数据:
[
    {
        "id": 2264145,
        "name": "",
        "modules": []
    },
    {
        "id": 2264146,
        "name": "General Resources",
        "modules": [
            {"name": "Announcements", "modname": "forum", ...},
            {"name": "Welcome Message", "modname": "label", ...},
            ...
        ]
    },
    ...
]
```

## 为什么不用 Mobile API？

### 场景 1：用 Mobile API 获取课程列表

```python
# 函数: core_enrol_get_users_courses
# 这是 Mobile API

wsfunction = "core_enrol_get_users_courses"
userid = 当前用户

# 权限检查:
# → 是否在 user_enrolments 表中?
# → 你的答案: ❌ 不在
# → 结果: 返回 []（空列表）

# 返回数据:
[] ← 0 门课程

# 结论: 无法进行下一步！
```

### 场景 2：即使知道课程 ID，也无法用 Mobile API 获取内容

```python
# 假设我们知道课程 ID = 137304
# 但 Mobile API 中 core_enrol_get_users_courses 已经返回空列表
# 
# 所以 Mobile API 中没有其他函数可以直接查询一个特定课程的内容
# (core_enrol_get_users_courses 是 Mobile API 的主要入口)

# 结论: Mobile API 完全无法使用
```

## 为什么选择网页版 API？

### 关键差异

| 特性 | Mobile API | 网页版 API |
|------|-----------|---------|
| **函数** | `core_enrol_get_users_courses` | `core_course_get_courses`<br>`core_course_get_contents` |
| **权限检查** | 是否 enrolled？ | 是否有 capability？ |
| **你的权限** | ❌ 不 enrolled | ✅ 有 `moodle/course:view` |
| **能看到课程 137304** | ❌ 不能 | ✅ 能 |
| **目标用户** | 学生 | 系统集成、Web 应用 |
| **优先级** | 高（Mobile App） | 低（系统集成） |

### 网页版 API 的权限检查

```php
// 官方代码 (course/externallib.php)

public static function get_courses($options = array()) {
    // 1. 获取所有课程（如果没指定 IDs）
    $courses = $DB->get_records('course');
    
    // 2. 对每个课程进行权限检查
    foreach ($courses as $course) {
        $context = context_course::instance($course->id);
        
        // 关键：检查 capability，而不是 enrollment
        require_capability('moodle/course:view', $context);
    }
    
    // 3. 返回有权限的课程
    return $coursesinfo;
}
```

**解读**：
- ✅ 不检查是否 enrolled
- ✅ 只检查是否有 `moodle/course:view` capability
- ✅ 你作为教师/TA 有这个 capability
- ✅ 所以能成功返回课程信息

## 调用流程对比

### 使用 Mobile API 的流程

```
Start
  ↓
调用 core_enrol_get_users_courses
  ↓
权限检查: 是否在 user_enrolments?
  ↓
❌ 你不在 user_enrolments
  ↓
返回 []（空列表）
  ↓
End (无法继续)
```

### 使用网页版 API 的流程

```
Start
  ↓
调用 core_course_get_courses (或 core_course_get_contents)
  ↓
权限检查: 是否有 moodle/course:view capability?
  ↓
✅ 你有这个 capability
  ↓
继续处理并返回课程信息
  ↓
Step 1 完成：返回课程名、ID、格式等
  ↓
调用 core_course_get_contents
  ↓
权限检查: 是否有 moodle/course:view capability?
  ↓
✅ 你有这个 capability
  ↓
返回课程结构（sections 和 modules）
  ↓
Step 2 完成：获得 11 个 sections，40+ 个 modules
  ↓
End (成功)
```

## 代码实现

### 测试脚本的实际代码

文件：`test_course_chapters.py`

#### 步骤 1（第 116-128 行）：获取课程基本信息

```python
params_course = {
    'wstoken': token,
    'wsfunction': 'core_course_get_courses',  # ← 网页版 API
    'options[ids][0]': course_id,
    'moodlewsrestformat': 'json'
}

async with session.get(base_url, params=params_course, ssl=False) as resp:
    result = await resp.json()
    if isinstance(result, list) and len(result) > 0:
        course = result[0]
        print(f"ID: {course.get('id')}")
        print(f"名称: {course.get('fullname')}")
        print(f"格式: {course.get('format')}")
```

#### 步骤 2（第 50-95 行）：获取课程结构

```python
params = {
    'wstoken': token,
    'wsfunction': 'core_course_get_contents',  # ← 网页版 API
    'courseid': course_id,
    'moodlewsrestformat': 'json'
}

async with session.get(base_url, params=params, ssl=False) as resp:
    result = await resp.json()
    if isinstance(result, list):
        print(f"返回 {len(result)} 个 sections")
        for section in result:
            print(f"Section: {section.get('name')}")
            modules = section.get('modules', [])
            print(f"  包含 {len(modules)} 个 modules")
```

## 关键要点

### 1. API 的名称很重要

- **Mobile API** = 官方移动 APP 使用的 API
  - 函数：`core_enrol_get_users_courses` 等
  - 特性：只返回 enrolled 课程
  - 目的：轻量级、快速、专门为移动设备优化

- **网页版 API** = Web Services API
  - 函数：`core_course_get_courses`、`core_course_get_contents` 等
  - 特性：完整的权限检查，可访问任何有权限的课程
  - 目的：系统集成、程序化访问

### 2. 权限系统的设计

```
Moodle 权限系统
├─ Enrollment (注册)
│  ├─ 什么是：用户在课程中的学生/教师身份
│  ├─ 存储位置：user_enrolments 表
│  ├─ 主要用途：Mobile API
│  └─ 你的状态：❌ 没有 enrollment
│
└─ Capability (能力)
   ├─ 什么是：用户对资源的权限
   ├─ 存储位置：role_assignments + roles 表
   ├─ 主要用途：网页版 API、前端
   └─ 你的状态：✅ 有 moodle/course:view capability
```

### 3. 为什么要区分？

**安全性**：
- 不同的访问场景使用不同的权限检查
- Mobile API 简化权限逻辑（只看 enrollment）
- 网页版 API 完整权限检查（capability）

**性能**：
- Mobile API 查询更快（简单的 enrollment 查询）
- 网页版 API 更灵活（支持复杂的权限情景）

**兼容性**：
- Mobile APP 只需要 enrolled 课程
- 系统集成可能需要访问其他课程（如教师/TA 身份）

## 实际场景解释

### 你的情况

```
User (你)
├─ Moodle 身份: 教师/TA
├─ 课程 137304:
│  ├─ Enrollment 状态: ❌ 不是学生身份
│  └─ Capability 状态: ✅ 有 moodle/course:view
│
├─ Mobile API 可以访问: ❌ 0 门课程
│  (因为 enrollment = ❌)
│
└─ 网页版 API 可以访问: ✅ 课程 137304 等
   (因为 capability = ✅)
```

### 如果你是学生

```
User (假设是学生身份)
├─ Moodle 身份: 学生
├─ 课程 137304:
│  ├─ Enrollment 状态: ✅ 是学生身份
│  └─ Capability 状态: ✅ 有 moodle/course:view
│
├─ Mobile API 可以访问: ✅ 课程 137304
│  (因为 enrollment = ✅)
│
└─ 网页版 API 可以访问: ✅ 课程 137304
   (因为 capability = ✅)
```

## 对 moodle-dl 的启示

### 当前实现

```python
# moodle-dl 目前的流程
courses = core_enrol_get_users_courses()  # Mobile API
for course in courses:
    contents = core_course_get_contents(course)  # 网页版 API
    download(contents)
```

**限制**：只能下载 enrolled 的课程

### 改进方向

```python
# 方案：支持教师/TA 模式
if user_is_teacher_or_ta():
    # 使用网页版 API
    courses = get_accessible_courses_by_capability()  # 网页版 API
else:
    # 使用 Mobile API
    courses = core_enrol_get_users_courses()  # Mobile API

for course in courses:
    contents = core_course_get_contents(course)  # 网页版 API
    download(contents)
```

## 总结表格

| 维度 | Mobile API | 网页版 API | 你的情况 |
|-----|-----------|---------|--------|
| **调用的函数** | `core_enrol_get_users_courses` | `core_course_get_courses`<br>`core_course_get_contents` | 使用网页版 |
| **权限检查方式** | enrollment 记录 | moodle/course:view capability | 两个都有检查 |
| **你的权限状态** | ❌ 无 | ✅ 有 | ✅ 有网页版 |
| **能看到课程 137304** | ❌ 否 | ✅ 是 | ✅ 通过网页版 |
| **返回的数据** | 0 门课程 | 课程信息 + 结构 | 成功获取 |
| **是否能下载** | ❌ 否 | ✅ 是 | ✅ 理论可行 |

---

**关键结论**：

✅ **步骤 1 和步骤 2 都使用的是网页版 API**，而不是 Mobile API。

❌ **为什么不用 Mobile API**：因为 Mobile API 无法看到课程 137304（你没有 enrollment）。

✅ **为什么能用网页版 API**：因为网页版 API 检查 capability，而你有 `moodle/course:view` capability。

