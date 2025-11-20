# Moodle 权限系统分层分析

## 问题背景

在测试三门不同课程时发现了 Moodle 权限系统的分层结构：

1. **课程 134183** - 完全无法访问（甚至通过网页版 API）
2. **课程 137304** - 可以通过网页版 API 访问
3. **Mobile API** - 无法返回任何课程（enrollment 为 0）

这表明 Moodle 有一个多层次的权限检查系统。

## 实际测试结果

### 测试环境
- Moodle 实例：keats.kcl.ac.uk
- 用户档案：st-test2
- 测试课程：134183（enrollment 页面）、137304（普通课程）

### 测试数据

#### 课程 134183

```python
# Mobile API 结果
wsfunction: core_enrol_get_users_courses
Result: [] (0 门课程)

# 网页版 API - 获取课程信息
wsfunction: core_course_get_courses
Result: ❌ 错误
Error: "You cannot execute functions in the course context (course id:134183).
        The context error message was: Course or activity not accessible."

# 网页版 API - 获取课程内容
wsfunction: core_course_get_contents
Result: ❌ 错误
Error: Same as above
```

#### 课程 137304

```python
# Mobile API 结果
wsfunction: core_enrol_get_users_courses
Result: [] (0 门课程)

# 网页版 API - 获取课程信息
wsfunction: core_course_get_courses
Result: ✅ 成功
Data: {
    "id": 137304,
    "fullname": "4NNYDT1A Oral Biology, Body Systems and Craniofacial Anatomy",
    "shortname": "4NNYDT1A 25~26 000001 ORAL BIOLOGY, B",
    ...
}

# 网页版 API - 获取课程内容
wsfunction: core_course_get_contents
Result: ✅ 成功
Data: [11 sections with 40+ modules]
```

## Moodle 权限系统的三层结构

### 第 1 层：Context 检查

**位置**：`context_course::instance($course->id)`

**检查内容**：
- 课程是否存在？
- 课程是否对当前用户可访问？
- 课程是否被隐藏、删除或存档？

**你的权限状态**：
- 课程 134183：❌ "not accessible"
- 课程 137304：✅ 可访问

**如果失败**：
- 无法继续进行任何操作
- 返回 `errorcoursecontextnotvalid` 错误
- 不会进行后续的权限检查

### 第 2 层：Capability 检查

**位置**：`require_capability('moodle/course:view', $context)`

**检查内容**：
- 用户是否有该课程的查看权限？
- 用户的 role 是否包含 `moodle/course:view` capability？
- 是否有全局的课程查看权限？

**你的权限状态**：
- 课程 134183：❌ （永远无法到达此步骤）
- 课程 137304：✅ 有权限（可能是教师/TA）

**如果失败**：
- 返回权限拒绝错误
- 不返回该课程的内容

### 第 3 层：Enrollment 检查（仅 Mobile API）

**位置**：Mobile API 的 `core_enrol_get_users_courses`

**检查内容**：
- 用户是否在 `user_enrolments` 表中有记录？
- 用户的 enrollment 是否 active？

**你的权限状态**：
- 课程 134183：❌ 无 enrollment
- 课程 137304：❌ 无 enrollment

**如果失败**：
- 该课程不会出现在 Mobile API 的返回列表中
- 网页版 API 不受影响（不检查 enrollment）

## 权限检查流程对比

### Mobile API 的流程

```
Start
  ↓
Call: core_enrol_get_users_courses(userid)
  ↓
Layer 1: Enrollment 检查
  Is user in user_enrolments?
  ↓
  ❌ 你不在任何课程的 enrollment 中
  ↓
Return: [] (空列表)
  ↓
End (无法访问任何课程)
```

### 网页版 API 的流程

```
Start
  ↓
Call: core_course_get_courses(ids=[137304])
  ↓
Layer 1: Context 检查
  Is course accessible?
  ↓
  课程 134183: ❌ "not accessible"
  ├─ ⛔ 停止，返回错误
  ↓
  课程 137304: ✅ 可访问
  ├─ 继续到 Layer 2
  ↓
Layer 2: Capability 检查
  Does user have moodle/course:view?
  ↓
  课程 137304: ✅ 你有这个权限
  ├─ 继续到 Layer 3
  ↓
Layer 3: 返回课程信息
  ✅ 成功返回课程数据
  ↓
End
```

## 错误消息解释

### "Course or activity not accessible"

这个错误表示：

1. **Context 检查失败** - 这是第一层检查
2. **严重权限问题** - 不是缺少 capability，而是课程本身不可访问
3. **可能的原因**：
   - 课程被隐藏（Hidden）
   - 课程被存档（Archived）
   - 课程已删除
   - 课程有严格的访问限制
   - 你不属于任何有权访问的角色

4. **后果** - 即使是网页版 API 也无法访问

## 三门课程的权限矩阵

| 维度 | 课程 134183 | 课程 137304 | 说明 |
|------|-----------|-----------|------|
| **Enrollment** | ❌ | ❌ | 两个都不是 enrolled 身份 |
| **Context 可访问** | ❌ | ✅ | 134183 被隐藏/限制 |
| **Capability (view)** | ❌ | ✅ | 137304 你有查看权限 |
| **Mobile API 可用** | ❌ | ❌ | 都不在 Mobile API 列表中 |
| **网页版 API 可用** | ❌ | ✅ | 只有 137304 可访问 |
| **网页前端可访问** | ? | ✅ | 可能 134183 是特殊页面 |

## 关键发现

### 发现 1：网页版 API 不是万能的

**错误观点**：
> 如果我不 enrolled，用网页版 API 就可以访问任何课程

**事实**：
- 网页版 API 也有权限检查
- Context 检查是第一道防线
- 某些课程根本无法访问

### 发现 2：权限检查是多层次的

**权限系统的层级**：
1. **Context 检查**（第一层，最严格）
   - 课程是否对你可见？
   - 如果失败，其他检查都不会进行

2. **Capability 检查**（第二层）
   - 你是否有查看权限？
   - 只有 Context 检查通过才会进行此检查

3. **Enrollment 检查**（仅 Mobile API）
   - 你是否 enrolled？
   - Mobile API 专属的检查

### 发现 3：/enrol/index.php 是特殊页面

**课程 134183 的 URL**：
```
https://keats.kcl.ac.uk/enrol/index.php?id=134183
```

**这个 URL 的含义**：
- 不是课程主页（通常是 `/course/view.php`）
- 是课程的 enrollment 管理页面
- 可能是公开的注册页面
- 或者是受限的 enrollment 界面

**为什么 API 无法访问**：
- API 尝试访问课程 134183 的内容
- 课程 134183 对你不可访问（Context 检查失败）
- 返回 "not accessible" 错误

## 对 moodle-dl 的影响

### 当前实现的问题

**moodle-dl 目前假设**：
```python
# 使用网页版 API
courses = core_course_get_courses(ids=course_ids)
for course in courses:
    # 假设总是能成功
    contents = core_course_get_contents(course.id)
    download(contents)
```

**实际情况**：
- `core_course_get_courses` 可能返回错误
- 需要错误处理

### 改进方向

```python
# 改进的实现
for course_id in course_ids:
    try:
        # 检查课程是否可访问
        course_info = core_course_get_courses(ids=[course_id])
        
        # 检查课程是否有内容
        contents = core_course_get_contents(course_id)
        
        # 下载内容
        download(contents)
        
    except CourseNotAccessibleError as e:
        # 优雅地处理权限错误
        log.warning(f"无法访问课程 {course_id}: {e}")
        continue
        
    except CapabilityError as e:
        # 权限不足
        log.warning(f"权限不足访问课程 {course_id}: {e}")
        continue
```

## 设计理念分析

### 为什么 Moodle 要这样设计？

#### 1. 安全性

- **Context 检查**：防止访问隐藏或受限的课程
- **Capability 检查**：确保用户有适当的权限
- **Enrollment 检查**：Mobile API 额外的学生身份验证

#### 2. 隐私保护

- 不同用户看到不同的课程列表
- 某些课程对某些用户完全隐藏
- 防止用户枚举全站课程

#### 3. 灵活性

- 支持多种角色和权限模型
- 可以为不同用户设置不同的访问权限
- 教师/TA 可以访问不 enrolled 的课程

#### 4. 一致性

- 网页版和 API 使用相同的权限系统
- 确保数据一致性
- 防止通过 API 绕过网页权限

## 最佳实践建议

### 1. 对于 moodle-dl 开发者

```python
# ✅ 正确做法：处理所有错误
for course_id in course_ids:
    try:
        course_info = api.get_course_info(course_id)
        contents = api.get_course_contents(course_id)
        download(contents)
    except (ContextNotAccessibleError, CapabilityError) as e:
        logger.warning(f"跳过课程 {course_id}: {str(e)}")
        continue
```

### 2. 对于 moodle-dl 用户

如果遇到"无法访问课程"错误：

1. **检查你的身份**
   - 你是学生/教师/TA？
   - 你在该课程中 enrolled 了吗？

2. **检查课程设置**
   - 课程是否被隐藏？
   - 课程是否被存档？
   - 课程的访问权限如何设置？

3. **尝试网页版访问**
   - 在浏览器中访问课程
   - 如果也无法访问，说明权限确实有问题

4. **联系管理员**
   - 如果你认为应该能访问
   - 请 Moodle 管理员检查权限设置

## 总结

| 层级 | 检查内容 | Mobile API | 网页版 API | 你的状态 |
|------|--------|-----------|---------|--------|
| 1. Context | 课程可访问？ | ✅ | ✅ | 137304: ✅<br>134183: ❌ |
| 2. Capability | 有查看权限？ | - | ✅ | 137304: ✅<br>134183: ❌ |
| 3. Enrollment | 已 enrolled？ | ✅ | - | 两个: ❌ |

**结论**：

✅ Moodle 的权限系统分层设计完善  
✅ 两种 API 都有独立的权限检查  
✅ 即使知道课程 ID 也无法绕过权限系统  
✅ moodle-dl 需要优雅地处理权限错误  

---

**测试日期**：2025-11-20  
**课程**：134183（无法访问）、137304（有访问权限）  
**结论**：Moodle 权限系统是多层次、细粒度的设计

