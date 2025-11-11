# 方案B实现：分离会话管理

## 实现时间
2025-11-11

## 问题背景

两个版本的权衡问题：
- **上一个版本（book6）**：能成功获取 Print Book HTML，但无法下载章节的视频和附件
- **当前版本（feature/print-book）**：能正常下载章节的视频和附件，但无法获取 Print Book HTML

根本原因是认证方式冲突：
- Print Book 需要 **cookies 认证**（Web UI）
- 章节内容需要 **token 认证**（Mobile API）

## 解决方案B：完全分离会话

### 新执行流程

```
┌─────────────────────────────────────────┐
│ Step 1: 获取 Print Book                   │
│ ├─ 使用 Playwright（独立浏览器进程）      │
│ ├─ 加载 cookies（SSO 认证）              │
│ └─ 返回 print_book_html                 │
└─────────────┬───────────────────────────┘
              │
┌─────────────▼───────────────────────────┐
│ Step 2: 处理章节内容                      │
│ ├─ 使用 Mobile API（core_contents）     │
│ ├─ 用 aiohttp 下载章节 HTML（token 认证）│
│ ├─ 提取 Kaltura 视频                     │
│ └─ 填充 chapters_by_id                   │
└─────────────┬───────────────────────────┘
              │
┌─────────────▼───────────────────────────┐
│ Step 3: 组合 Print Book 和章节映射       │
│ ├─ 如果 print_book_html 存在且有章节     │
│ │  └─ 使用 _create_linked_print_book_html │
│ ├─ 如果只有 print_book_html              │
│ │  └─ 直接添加（无映射）                  │
│ └─ 添加所有章节文件                      │
└─────────────────────────────────────────┘
```

### 关键改动

#### 1. 执行顺序调整（book.py:82-93）

**之前（旧）**：先处理章节，再获取 Print Book
**现在（新）**：先获取 Print Book，再处理章节

```python
# Step 1: 使用 Playwright + cookies 获取 Print Book（独立的认证方式）
logging.info('📖 Step 1: Fetching Print Book HTML with Playwright (独立会话)')
print_book_html, print_book_url = await self._fetch_print_book_html(module_id, course_id)

# Step 2: 使用 Mobile API + aiohttp + token 获取章节内容（完全独立的认证方式）
logging.info('📖 Step 2: Processing chapters from Mobile API (core_contents)')
book_contents = self.get_module_in_core_contents(course_id, module_id, core_contents).get('contents', [])
```

#### 2. 处理逻辑简化（book.py:277-325）

**之前**：在处理完章节后再获取 Print Book，重复调用 `_fetch_print_book_html`
**现在**：直接使用已获取的 print_book_html，避免重复调用

```python
# Step 3: 现在章节内容已处理，使用 Print Book（已在 Step 1 中获取）
if print_book_html and chapters_by_id:
    # 应用章节映射（视频链接等）
    ...
elif print_book_html:
    # 直接添加 Print Book（无映射）
    ...
else:
    # Print Book 不可用，仅使用章节内容
    ...
```

### 认证方式隔离

| 阶段 | 工具 | 认证方式 | 状态影响 |
|------|------|--------|---------|
| Step 1 | Playwright | cookies (SSO) | 独立浏览器进程 |
| Step 2 | aiohttp | token (Mobile API) | 独立 HTTP 会话 |
| Step 3 | Python | 内存操作 | 无网络请求 |

### 好处

✅ **完全隔离**
- Playwright 和 aiohttp 使用完全独立的会话
- 互不干扰、互不污染

✅ **独立失败处理**
- Print Book 失败不影响章节下载
- 章节下载失败不影响 Print Book

✅ **更好的诊断**
- 清晰的日志顺序：Print Book → 章节 → 组合
- 可以独立排查每个步骤

✅ **兼容性**
- 适用于不同权限配置的 Moodle 实例
- 即使 Print Book 权限不足，章节内容仍可完整下载

## 实现细节

### 文件修改
- `moodle_dl/moodle/mods/book.py`：重新排序执行流程

### 无需修改的组件
- `_fetch_print_book_html()`：仍然使用 Playwright + cookies
- `_fetch_chapter_html()`：仍然使用 aiohttp + token
- `_create_linked_print_book_html()`：仍然处理 Print Book 映射
- `result_builder.py`：无需修改

## 预期结果

### 最佳情况（两者都成功）
```
✅ Print Book HTML：完整的单页版本（有视频链接）
✅ 章节内容：
   ├─ 章节 HTML
   ├─ 附件（PPT、PDF）
   └─ Kaltura 视频
```

### 降级情况1（Print Book 失败）
```
❌ Print Book HTML：无法获取
✅ 章节内容：完全可用（无 Print Book 映射）
```

### 降级情况2（章节失败）
```
✅ Print Book HTML：可用（可能包含嵌入式内容链接）
❌ 章节内容：无法获取
```

### 降级情况3（两者都失败）
```
❌ Print Book HTML：无法获取
❌ 章节内容：无法获取
```

## 测试清单

- [ ] 安装修改后的代码：`pip install -e .`
- [ ] 运行下载：`moodle-dl`
- [ ] 验证 Print Book：检查是否成功生成或记录日志
- [ ] 验证章节内容：检查视频和附件是否下载
- [ ] 查看日志：确认执行顺序为 Step 1 → Step 2 → Step 3
- [ ] 检查是否还有 404 或其他错误

## 后续优化（可选）

如果还有问题，可以考虑：

1. **增强 Print Book 权限检查**
   - 在 Step 1 前检查是否有权限
   - 提前返回而不是等待超时

2. **增加重试逻辑**
   - Print Book 获取失败时自动重试
   - 使用不同的 cookies 源

3. **并行处理**（未来）
   - Step 1 和 Step 2 可以并行执行（现在是序列）
   - 使用 asyncio.gather() 提升性能
