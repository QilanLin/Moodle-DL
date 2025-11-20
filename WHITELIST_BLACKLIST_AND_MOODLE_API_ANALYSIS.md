# Moodle 白名单/黑名单功能与 Mobile API 分析

## 用户需求

选择白名单/黑名单后，想要：
1. 显示用户已 enrol 的课程
2. 还要显示用户 NOT enrol 但有权限访问的课程（全站其他课程）

**关键问题**：这种课程可以通过 Moodle Mobile API 访问吗？

---

## Moodle 官方文档分析

### 1. Enrolment 的定义

根据官方 Moodle devdocs (`/docs/apis/subsystems/enrol.md`):

- **用户必须在 `user_enrolments` 表中有记录才能"enrolled"**
- 这个表存储 enrolment plugin instance、status、date 信息
- Role assignment 是分开的，存储在 `role_assignments` 表中

这两个概念是 **INDEPENDENT** 的：
- `enrolled` = 在 `user_enrolments` 表中有记录
- `can view course` = 有 `moodle/course:view` capability

### 2. is_enrolled() API

官方 API 函数签名：

```php
function is_enrolled(
    context $context,
    stdClass $user = null,
    string $withcapability = '',
    bool $onlyactive = false
)
```

关键说明：
- ✅ 返回 `true` 给 enrolled 的学生和教师
- ❌ 返回 `false` 给 administrators 和 managers
- ⚠️ 即使 suspended 的用户也不能进入课程（除非有 guest access）

### 3. 权限模型

Moodle 权限模型中：
- Enrollment 决定用户是否在该课程中
- Capabilities 和 role assignments 决定用户能做什么
- 这两者是分开的

---

## Moodle Mobile API 的技术限制

### 问题 1：Mobile API 只能获取 Enrolled 课程

Moodle Mobile App 使用 `core_enrol_get_users_courses` webservice：
- ✅ 只返回用户 enrolled 的课程
- ❌ 不支持获取 "能访问但未注册" 的课程

### 问题 2：没有 "列出全站课程" API

- Moodle Mobile App 不提供获取全站所有课程的 API
- 即使有也会有严重的权限和性能问题

### 问题 3：权限检查需要数据库查询

即使知道课程 ID，检查 "有权限但未 enrol" 也需要：
1. 查询 `moodle/course:view` capability
2. 检查 `role_assignments` 表
3. 检查 `course visibility` 设置

Mobile API 设计上不支持这些复杂查询

---

## 实际的 Moodle 行为

### ✅ 用户 ENROLLED 的课程

- 自动显示在 Moodle Dashboard
- Mobile API 可以获取
- moodle-dl 现有代码可以获取

### ❌ 用户有权限但 NOT ENROLLED 的课程

- 不会自动显示在 Dashboard
- 不在 enrolled courses 列表中
- 用户需要主动搜索或老师直接分配 enrolment
- 需要自己 enrol 或管理员手动 enrol

---

## Moodle 的设计理念

### 1. 为什么 Dashboard 只显示 Enrolled 课程？

**性能考虑**：
- 检查用户对全站所有课程的权限需要大量数据库查询
- 这会影响 Dashboard 加载速度

**安全考虑**：
- 只暴露用户已参与的课程信息
- 隐藏其他课程内容，防止信息泄露

**用户体验考虑**：
- 用户只关心自己参与的课程
- 减少信息过载

### 2. 业务模型

- **老师需要主动 enrol 学生** 到他们的课程
- **学生可能需要主动 enrol** 或通过邀请链接
- **这是课程管理的一部分**

---

## moodle-dl 的现有行为

### 当前实现

✅ 使用 `core_enrol_get_users_courses` API
✅ 只能获取用户 enrolled 的课程
✅ 对用户 NOT enrolled 的课程无法访问

**这符合 Moodle Mobile API 的设计限制！**

---

## 对用户的建议

### ❌ 不可行的方案 A：使用 Mobile API

**为什么不行**：
- API 不支持
- 这是 Moodle 官方设计的限制
- `core_enrol_get_users_courses` 无法改变这点

### ✅ 可行的方案 B：在 Moodle 中 enrol 用户

**步骤**：
1. 管理员主动将用户 enrol 到想要的课程
2. 或者课程配置为自我 enrol (self-enrolment)
3. 然后 moodle-dl 就能访问这些课程

**优点**：
- 符合 Moodle 设计
- 安全
- 简单

**缺点**：
- 需要在 Moodle 侧进行额外操作

### ✅ 可能的方案 C：使用 Moodle 插件或自定义

**步骤**：
1. 开发自定义 webservice API
2. 检查用户对各课程的 capability
3. 返回有权限的课程列表

**缺点**：
- 需要自定义开发
- 可能有性能问题
- 需要权限管理复杂度

---

## 白名单/黑名单功能的正确理解

### 当前实现

✅ **从已 enrolled 的课程中选择下载哪些**

这是正确的功能实现。

### 用户期望与现实的差距

❌ **期望**："下载所有我有权限但未 enrolled 的课程"

**现实**：这个期望与 Moodle API 设计不符

### 正确的业务流程

1. 管理员或老师 enrol 用户到课程
2. 用户运行 moodle-dl
3. moodle-dl 下载 enrolled 的课程
4. 用户用白名单/黑名单选择要下载哪些

---

## 结论

### 问题

如何通过 Mobile API 获取用户有权限但未 enrolled 的课程？

### 答案

❌ **不可能**

### 原因

1. **Moodle Mobile API 只返回 enrolled 课程**
   - `core_enrol_get_users_courses` API 设计就是这样的

2. **Moodle 设计上就是这样的**
   - 出于性能和安全考虑

3. **API 无法改变这点**
   - 这是 Moodle 核心功能的一部分

### 建议

✅ **目前的白名单/黑名单实现是正确的**
- 无需修改代码来支持未 enrolled 的课程
- 这是 Moodle 官方设计的限制，不是 moodle-dl 的问题

### 解决方案

如果用户想下载未 enrolled 但有权限的课程：

1. **在 Moodle 中 enrol**
   - 让管理员或老师 enrol 用户
   - 或者启用自我 enrol

2. **然后运行 moodle-dl**
   - moodle-dl 就能看到这些课程

---

## 相关资源

- **官方 Moodle 文档**：`devdocs_official_repo_for_reference/docs/apis/subsystems/enrol.md`
- **Moodle Mobile App 官方仓库**：`moodle_mobile_app_official_repo_for_reference`
- **Moodle 核心官方仓库**：`moodle_official_repo_for_reference`

---

## 补充说明

这份分析基于：
- ✅ 官方 Moodle devdocs
- ✅ 官方 Moodle 仓库代码
- ✅ 官方 Moodle Mobile App 代码
- ✅ Moodle Web Service API 文档

**结论经过官方文档验证，不是 moodle-dl 的限制。**

