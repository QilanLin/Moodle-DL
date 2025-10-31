# Moodle-DL 文档索引

## 📚 用户指南

### [浏览器 Cookie 导出完整指南](BROWSER_COOKIE_EXPORT.md)

**适用于**：所有用户

**内容**：
- ✅ 支持的浏览器（12 种）
- ✅ 如何导出 cookies（包括 Zen, Waterfox, Arc）
- ✅ Cookie 文件位置
- ✅ 常见问题排查
- ✅ 使用示例

**何时阅读**：
- 首次设置 moodle-dl
- 下载失败提示需要 cookies
- 使用非主流浏览器（Zen, Waterfox, Arc）

---

### [认证完整指南](AUTHENTICATION.md)

**适用于**：所有用户

**内容**：
- ✅ Token vs Cookies 的区别
- ✅ 普通登录 vs SSO 登录
- ✅ Cookie 自动刷新机制
- ✅ 日志级别（INFO vs DEBUG）
- ✅ 认证问题排查

**何时阅读**：
- 理解 Token 和 Cookies 的作用
- 遇到认证错误
- 需要调试下载问题

---

### [Kalvidres 处理指南](KALVIDRES_GUIDE.md)

**适用于**：下载 Kaltura 视频的用户

**内容**：
- ✅ 什么是 Kalvidres
- ✅ moodle-dl 如何处理 Kalvidres
- ✅ 技术实现细节
- ✅ 常见问题

**何时阅读**：
- Kaltura 视频下载失败
- 视频描述文本未提取
- 理解 Kalvidres 处理流程

---

## 🔧 技术参考

### [API vs HTML 对比](API_VS_HTML_COMPARISON.md)

**适用于**：开发者、高级用户

**内容**：
- ✅ Moodle API 的能力和限制
- ✅ 何时需要访问 HTML
- ✅ API 和 HTML 提取的对比

**何时阅读**：
- 理解为什么某些内容需要 cookies
- 贡献代码
- 深入理解 moodle-dl 工作原理

---

### [模块类型支持](MODULE_TYPE_SUPPORT.md)

**适用于**：开发者、高级用户

**内容**：
- ✅ Moodle 的模块类型
- ✅ moodle-dl 如何分类模块
- ✅ 不同类型的处理方式

**何时阅读**：
- 添加新模块类型支持
- 理解模块分类逻辑
- 调试模块处理问题

---

### [Moodle API 限制](MOODLE_API_LIMITATIONS.md)

**适用于**：开发者、高级用户

**内容**：
- ✅ Moodle API 的已知限制
- ✅ 哪些内容无法通过 API 获取
- ✅ 解决方案和替代方法

**何时阅读**：
- 理解为什么需要 cookies
- 遇到 API 无法访问的内容
- 贡献代码

---

## 📖 项目文档

### [README](README.md)

**适用于**：所有用户

**内容**：
- ✅ 项目简介
- ✅ 安装指南
- ✅ 快速开始
- ✅ 基本使用

**何时阅读**：
- 首次了解 moodle-dl
- 安装和配置

---

### [贡献指南](CONTRIBUTING.md)

**适用于**：贡献者

**内容**：
- ✅ 如何贡献代码
- ✅ 代码规范
- ✅ 提交 PR 流程

**何时阅读**：
- 想要贡献代码
- 报告 bug
- 提出新功能建议

---

### [行为准则](CODE_OF_CONDUCT.md)

**适用于**：所有参与者

**内容**：
- ✅ 社区行为准则
- ✅ 预期行为
- ✅ 举报机制

**何时阅读**：
- 参与社区讨论
- 报告不当行为

---

## 🗺️ 快速导航

### 常见任务

| 任务 | 阅读文档 |
|------|---------|
| 首次设置 moodle-dl | [README](README.md) → [浏览器 Cookie 导出](BROWSER_COOKIE_EXPORT.md) |
| 导出浏览器 cookies | [浏览器 Cookie 导出](BROWSER_COOKIE_EXPORT.md) |
| 下载失败 (401 错误) | [认证指南](AUTHENTICATION.md) → [浏览器 Cookie 导出](BROWSER_COOKIE_EXPORT.md) |
| Kaltura 视频下载失败 | [Kalvidres 指南](KALVIDRES_GUIDE.md) |
| 理解 Token 和 Cookies | [认证指南](AUTHENTICATION.md) |
| 使用 Zen/Waterfox/Arc | [浏览器 Cookie 导出](BROWSER_COOKIE_EXPORT.md) |
| 调试问题 | [认证指南](AUTHENTICATION.md) (启用 --verbose) |
| 贡献代码 | [贡献指南](CONTRIBUTING.md) |

---

### 按用户类型

#### 普通用户

推荐阅读顺序：
1. [README](README.md) - 了解项目
2. [浏览器 Cookie 导出](BROWSER_COOKIE_EXPORT.md) - 设置 cookies
3. [认证指南](AUTHENTICATION.md) - 理解认证
4. [Kalvidres 指南](KALVIDRES_GUIDE.md) - 视频下载

#### 高级用户

推荐阅读顺序：
1. 上述所有用户指南
2. [API vs HTML 对比](API_VS_HTML_COMPARISON.md)
3. [Moodle API 限制](MOODLE_API_LIMITATIONS.md)
4. [模块类型支持](MODULE_TYPE_SUPPORT.md)

#### 开发者

推荐阅读顺序：
1. 所有用户指南和技术参考
2. [贡献指南](CONTRIBUTING.md)
3. 源代码

---

## 📝 文档更新历史

### 2025-10-31: 文档重组

**合并的文档**：
- ✅ 浏览器 cookie 相关文档合并为 `BROWSER_COOKIE_EXPORT.md`
- ✅ 认证相关文档合并为 `AUTHENTICATION.md`
- ✅ Kalvidres 相关文档合并为 `KALVIDRES_GUIDE.md`

**删除的文档**：
- ❌ AUTO_DETECT_EXPLAINED.md（过时）
- ❌ BROWSER_COOKIES_GUIDE.md（已合并）
- ❌ BROWSER_SUPPORT_CORRECTED.md（已合并）
- ❌ CUSTOM_BROWSER_SUPPORT.md（已合并）
- ❌ AUTHENTICATION_REQUIREMENTS.md（已合并）
- ❌ NO_COOKIES_BEHAVIOR.md（已合并）
- ❌ LOGGING_DETAILS.md（已合并）
- ❌ COOKIE_LIFETIME_GUIDE.md（已合并）
- ❌ CLEANUP_SUMMARY.md（临时文档）
- ❌ INTEGRATION_COMPLETE.md（临时文档）
- ❌ kalvidres_extracted_generic.md（临时文档）
- ❌ KALVIDRES_EXTRACTION_SUMMARY.md（已合并）
- ❌ KALVIDRES_ISSUE_ANALYSIS.md（过程性文档）
- ❌ KALVIDRES_PROCESSING_GUIDE.md（已合并）
- ❌ GENERIC_TEXT_EXTRACTION_GUIDE.md（已合并）

**结果**：
- 📉 文档数量：21 → 9 (-12 个)
- 📈 文档质量：更清晰、更易导航
- ✅ 移除了重复和过时内容

---

## 💡 提示

- 📖 所有文档使用 Markdown 格式
- 🔗 文档之间相互链接，方便导航
- 💬 如果发现文档问题，请[提交 Issue](https://github.com/C0D3D3V/Moodle-DL/issues)
- ✨ 欢迎贡献文档改进

---

**快速开始**：[README](README.md) → [浏览器 Cookie 导出](BROWSER_COOKIE_EXPORT.md) → [认证指南](AUTHENTICATION.md)
