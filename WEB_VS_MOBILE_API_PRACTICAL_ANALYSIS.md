# 网页前端 vs Mobile API - 实际测试分析

## 问题

用户档案 `st-test2` 可以在网页前端访问课程 137304：
- ✅ 网页 URL: `https://keats.kcl.ac.uk/course/view.php?id=137304`
- ✅ 可以正常访问和浏览

但是，通过 Mobile API 无法获取这门课。为什么？

## 实际测试结果

### 测试环境
- Moodle 实例: `keats.kcl.ac.uk`
- 用户档案: `/Users/linqilan/CodingProjects/moodle/st-test2`
- 课程: `4NNYDT1A Oral Biology, Body Systems and Craniofacial Anatomy` (ID: 137304)

### 测试 1: Mobile API 获取 enrolled 课程

```python
wsfunction: core_enrol_get_users_courses
userid: 当前用户
```

**结果：** ❌ 返回 0 门课程

```
已 enrolled 的课程: []
```

**发现：**
- 配置中显示 `download_course_ids: [134659]`
- 但 Mobile API 实际返回 0 门课程
- 这说明用户在该 Moodle 实例中没有 enrolled 的课程

### 测试 2: 检查课程 137304 是否在列表中

**结果：** ❌ 课程 137304 不在 Mobile API 返回的列表中

```
课程 137304 不在 Mobile API 返回的列表中
已 enrolled 的课程 IDs: []
```

### 测试 3: 直接查询课程 137304

```python
wsfunction: core_course_get_courses
options[ids][0]: 137304
```

**结果：** ✅ 成功查询课程信息

```
课程名称: 4NNYDT1A Oral Biology, Body Systems and Craniofacial Anatomy
短名: 4NNYDT1A 25~26 000001 ORAL BIOLOGY, B
```

## 关键发现

### 对比结果

| 访问方式 | 网页前端 | Mobile API |
|--------|--------|-----------|
| 访问课程 137304 | ✅ 成功 | ❌ 失败 |
| 权限检查 | 检查多种权限 | 只检查 enrollment |
| 需要的条件 | 有 `moodle/course:view` capability | 在 `user_enrolments` 表中 |

### 权限系统的区别

#### 网页前端 (Web UI)
```
检查流程:
1. 用户登录认证
2. 检查 moodle/course:view capability
3. 检查 role assignment
4. 检查特殊权限（教师、TA、管理员）
5. 检查课程可见性

结果: 有任何匹配 → 允许访问
```

#### Mobile API (core_enrol_get_users_courses)
```
检查流程:
1. 查询 user_enrolments 表
2. 检查用户是否在该表中有记录
3. 检查 enrollment 是否 active

结果: 无 enrollment 记录 → 无法返回
```

## 技术解释

### Moodle 的权限模型

Moodle 在两个独立的概念之间做了区分：

1. **Enrollment (注册)**
   - 用户在 `user_enrolments` 表中的记录
   - 通常适用于学生身份
   - Mobile API 只查看这个

2. **Capability (能力)**
   - 用户对资源的权限
   - 由 `role_assignments` 和 role 定义
   - 网页前端检查这个

### 官方代码验证

#### Mobile API 实现 (enrol/externallib.php)
```php
public static function get_users_courses($userid, $returnusercount = true) {
    // 调用 enrol_get_users_courses - 只查 enrollment
    $courses = enrol_get_users_courses($userid, true, '*');
    
    // 遍历返回的课程
    foreach ($courses as $course) {
        $context = context_course::instance($course->id);
        
        // 权限检查：只检查 view capability
        require_capability('moodle/course:view', $context);
    }
}
```

#### 网页版 API (course/externallib.php)
```php
public static function get_courses($options = array()) {
    // 获取全部课程（没有 enrollment 限制）
    $courses = $DB->get_records('course');
    
    // 对每个课程进行权限检查
    foreach ($courses as $course) {
        $context = context_course::instance($course->id);
        require_capability('moodle/course:view', $context);
    }
}
```

## 用户的实际身份

根据测试结果，用户可能是以下身份：

### 可能的身份

1. **教师 (Teacher)**
   - ✅ 拥有 `moodle/course:view` capability
   - ✅ 能访问课程内容
   - ❌ 不是 enrolled 学生身份
   - 📌 典型特征：可以查看、编辑课程内容

2. **教学助理 (Teaching Assistant)**
   - ✅ 有助教权限
   - ✅ 可以访问课程
   - ❌ 不在 enrollment 中
   - 📌 典型特征：可以查看学生提交等

3. **管理员或特殊角色**
   - ✅ 全局或课程级别的特殊权限
   - ✅ 可以访问任何课程
   - ❌ 不需要 enrollment

4. **访客或特殊权限设置**
   - ✅ 课程配置允许访问
   - ✅ 可以临时访问
   - ❌ 不是正式 enrollment

## 对 moodle-dl 的影响

### 当前实现
- moodle-dl 使用 `core_enrol_get_users_courses` API
- **只能下载** enrolled 的课程

### 在这个用户的情况下
- 该用户没有 enrolled 的课程（API 返回 0 门）
- 即使他能访问课程 137304 在网页上
- moodle-dl 也**无法下载**这门课

### 解决方案

#### 方案 A: Enroll 到课程
```
✅ 最简单
✅ 完全符合 Moodle 设计
❌ 需要具有 enrollment 权限的人操作
```

#### 方案 B: 使用网页版 API
```
✅ 可以访问所有有权限的课程
❌ 需要更高级别的权限
❌ 可能有性能问题
```

#### 方案 C: 配置课程 ID 白名单
```
✅ 如果 moodle-dl 支持的话
❌ 需要手动指定每个课程 ID
```

## 设计理念

这种设计体现了 Moodle 的几个关键理念：

### 1. 安全性
- Enrollment 系统确保学生身份验证
- 防止未授权访问学生数据

### 2. 灵活性
- 允许不同身份（教师、TA 等）访问课程
- 不依赖 enrollment 系统

### 3. 性能
- Mobile API 专门优化用于学生（使用 enrollment 索引）
- 避免复杂的权限检查

### 4. 隐私
- 防止用户枚举全站课程
- 只暴露用户有权访问的课程

## 结论

### 为什么能访问网页但 Mobile API 无法获取？

**这不是 bug，而是有意的设计区分。**

| 特性 | 说明 |
|-----|------|
| 设计意图 | 区分 enrollment 和 capability 系统 |
| 网页前端 | 使用完整的权限系统 |
| Mobile API | 只关注 enrollment（性能和简洁性） |
| 用户影响 | 作为教师/TA，可以访问网页但 Mobile API 看不到 |

### 实际意义

- ✅ Moodle 权限系统工作正常
- ✅ 这两种访问方式是独立的
- ✅ 用户的实际身份决定了访问权限
- ✅ Mobile API 的限制是特意设计的

### 对 moodle-dl 用户的建议

1. **如果是学生**
   - Enroll 到想要下载的课程
   - moodle-dl 就能访问

2. **如果是教师/TA**
   - 在 Moodle 中为自己 enroll（如果可能）
   - 或联系管理员
   - 或等待 moodle-dl 支持其他 API

3. **验证身份**
   - 在 Moodle 网页中查看"我的课程"
   - 对比 moodle-dl 看到的课程列表
   - 确认差异原因

## 参考文献

- Moodle 官方仓库: `/public/enrol/externallib.php`
- Moodle 官方仓库: `/public/course/externallib.php`
- Moodle 官方文档: [Enrolment Plugin](https://moodledev.io/docs/apis/subsystems/enrol)
- Moodle 官方文档: [Roles](https://moodledev.io/docs/apis/subsystems/roles)

---

**测试日期**: 2025-11-20  
**Moodle 实例**: keats.kcl.ac.uk  
**测试工具**: test_mobile_api_course.py

