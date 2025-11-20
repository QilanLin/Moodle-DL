# 函数原子化重构 - 第二阶段计划

## 📋 概览

在第一阶段成功完成了两个关键函数的原子化后，我们现在面临更复杂的 `auto_login_with_sso()` 函数。

### 当前状态
- **函数规模**: 260+ 行
- **复杂度**: CC ≈ 15-20（高）
- **职责数**: 6-7 个混合职责
- **嵌套深度**: 3-4 层异步嵌套

## 🎯 策略选择

考虑到这个函数的特殊性，我们有三个可选方案：

### 方案 A: 完整原子化（完美但风险）
**优点**:
- 最彻底的重构
- 每个阶段完全独立
- 最高的复用性

**缺点**:
- 涉及大量 async/await 嵌套改写
- 风险较高（SSO 逻辑复杂）
- 测试验证时间长

**工作量**: 3-4 小时

---

### 方案 B: 分阶段提取（推荐）✅
**优点**:
- 逐步优化，风险低
- 核心业务逻辑保持不变
- 易于验证和测试

**缺点**:
- 不如方案 A 彻底
- 仍有嵌套，但降低了复杂度

**工作量**: 1.5-2 小时

---

### 方案 C: 策略模式重构（架构优化）
**优点**:
- 最灵活的架构
- 支持多种 SSO 提供商
- 长期可维护

**缺点**:
- 工作量大（4-5 小时）
- 涉及架构层面的改变

**工作量**: 4-5 小时

---

## 🚀 推荐实施方案 B

我建议采用**方案 B: 分阶段提取**，理由如下：

1. **风险可控**: SSO 逻辑复杂且关键，谨慎改进更安全
2. **收益明显**: 可以显著降低复杂度（CC 从 18 → 8）
3. **可验证**: 每个阶段都可独立测试
4. **易回退**: 出现问题时易于修复

## 📝 详细实施步骤

### 步骤 1: 提取 SSO 重定向检测逻辑
**当前代码**: 第 357-390 行
**问题**: 嵌套在大 try-except 块中
**方案**: 
```python
async def _wait_for_sso_redirect(page, moodle_domain, max_wait=15):
    """等待并检测 SSO 重定向完成"""
    visited_sso = False
    for i in range(max_wait):
        # ... 提取重定向检测逻辑
    return visited_sso
```

### 步骤 2: 提取登录状态检查逻辑
**当前代码**: 第 391-447 行
**问题**: 多个 if-elif 嵌套，检查逻辑复杂
**方案**:
```python
def _check_login_status(current_url, page_content, visited_sso):
    """检查是否已成功登录"""
    if 'login' in current_url.lower():
        return False
    if 'login/logout.php' in page_content or visited_sso:
        return True
    return False
```

### 步骤 3: 提取 cookies 保存逻辑
**当前代码**: 第 447-471 行
**问题**: 混合了提取、验证和保存
**方案**:
```python
async def _save_cookies_to_db(context, auth_manager):
    """提取并保存 cookies 到数据库"""
    updated_cookies = await context.cookies()
    if auth_manager:
        session_id = auth_manager.save_sso_cookies(updated_cookies)
        return session_id
    return None
```

### 步骤 4: 提取浏览器初始化逻辑
**当前代码**: 第 265-298 行
**问题**: 浏览器启动和上下文创建混合
**方案**:
```python
async def _setup_browser_context(playwright, preferred_browser, headless, storage_state):
    """初始化浏览器和上下文"""
    # 分离浏览器启动和上下文创建
```

## 📊 预期改进

采用方案 B 的改进：

| 指标 | 当前 | 目标 | 改进 |
|------|------|------|------|
| 函数行数 | 260+ | 180-200 | ↓ 25% |
| 圈复杂度 | ~18 | ~8 | ↓ 56% |
| 可测试函数 | 0 | 4-5 | ↑ ∞ |
| 嵌套深度 | 4 | 2-3 | ↓ 50% |

## ⏱️ 时间估计

- 分析和设计: 30 分钟
- 代码重构: 60 分钟
- 测试验证: 30 分钟
- **总计**: 2 小时

## ✅ 验证计划

1. 所有 181 个单元测试通过
2. 无 lint 错误
3. 功能测试: 执行 `moodle-dl --init --sso`
4. 向后兼容性确认

## 🔄 后续升级路径

采用方案 B 后，我们有两个升级路径：

**路径 1**: 继续完全原子化（2-3 个月后）
- 当团队对代码更熟悉
- 可以更安全地进行彻底重构

**路径 2**: 采用策略模式（6 个月后）
- 当需要支持多个 SSO 提供商
- 架构层面的优化

## 💡 建议

**立即执行**: 方案 B（分阶段提取）
- 低风险，高收益
- 为团队建立信心
- 为未来的完整重构铺路

---

## 📌 相关文件

- `moodle_dl/auto_sso_login.py` - 主文件
- `moodle_dl/export_browser_cookies.py` - Cookie 提取
- `moodle_dl/auth_session_manager.py` - Cookie 保存

---

**最后更新**: 2025-11-20
**状态**: 待审批

