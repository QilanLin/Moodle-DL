# Mobile API 到 Web API Fallback 策略

## 🎯 概述

当前实现中，许多 Mobile API 调用缺少对应的 Web Service API fallback。这个文档规划如何为每个 Mobile API 实现对应的 Web API fallback，提高系统的可靠性和适配范围。

---

## 📊 审计结果

### 当前状态

| 类别 | 数量 | 比例 | 状态 |
|------|------|------|------|
| ✅ 已实现 fallback | 2 | 22% | 部分完成 |
| ❌ 待实现 fallback | 6 | 67% | 高优先级 |
| ⚠️ 特殊情况 (无 Web 等效) | 1 | 11% | 无法实现 |

### API 对应清单

#### ✅ 已实现的 Fallback

1. **core_enrol_get_users_courses** → `core_course_get_courses`
   - Mobile API: 获取用户已enrolled的课程
   - Web API Fallback: 通过 `core_course_get_courses` + context check
   - 实现位置: `moodle_dl/moodle/course_validator.py`
   - 状态: ✅ 完成 (2025-11-20)

2. **core_enrol_get_enrolled_users**
   - 类型: 混合 (Mobile API 名称，但实际是 Web Service)
   - 状态: ✅ 已是 Web Service API，无需额外 fallback

#### ❌ 待实现的 Fallback (按优先级)

**高优先级** (常用模块)

3. **mod_assign_get_assignments** → `core_course_get_contents`
   - 用途: 获取课程中的所有作业
   - 当前位置: `moodle_dl/moodle/mods/assign.py` line 28
   - Mobile API: `mod_assign_get_assignments`
   - Web API Fallback:
     ```
     core_course_get_contents(courseid, options[filter][modname]=assign)
     ```
   - 预期收益: 提高作业模块的适配性
   - 工作量: 中等 (需要解析 core_course_get_contents 中的 assign 模块)
   - 优先级: 🔴 高

4. **mod_forum_get_forum_discussions** → `core_course_get_contents` + parsing
   - 用途: 获取论坛中的讨论列表
   - 当前位置: `moodle_dl/moodle/mods/forum.py` line 166, 168
   - Mobile API: `mod_forum_get_forum_discussions` 或 `mod_forum_get_forum_discussions_paginated`
   - Web API Fallback:
     ```
     1. core_course_get_contents(courseid, options[filter][modname]=forum)
     2. 通过论坛 ID 获取讨论 (需要查找对应 API)
     ```
   - 预期收益: 提高论坛模块的适配性
   - 工作量: 中等 (需要多个 API 调用)
   - 优先级: 🔴 高

**中优先级** (常用但复杂)

5. **mod_assign_get_submissions** → Web Service API
   - 用途: 获取作业的所有提交
   - 当前位置: `moodle_dl/moodle/mods/assign.py` line 140
   - Mobile API: `mod_assign_get_submissions`
   - Web API Fallback:
     ```
     mod_assign_get_submissions (实际上也是 Web Service API)
     或通过 core_course_get_contents 获取作业信息
     ```
   - 预期收益: 提高提交数据的完整性
   - 工作量: 小 (API 已存在)
   - 优先级: 🟡 中

6. **mod_workshop_get_submissions** → `core_course_get_contents` + parsing
   - 用途: 获取研讨会的提交
   - 当前位置: `moodle_dl/moodle/mods/workshop.py` line 177
   - Mobile API: `mod_workshop_get_submissions`
   - Web API Fallback: 通过 core_course_get_contents 和 workshop 相关 API
   - 预期收益: 提高研讨会模块的适配性
   - 工作量: 大 (需要复杂的数据转换)
   - 优先级: 🟡 中

**低优先级** (不常用或复杂度高)

7. **mod_wiki_get_wikis** → `core_course_get_contents`
   - 用途: 获取维基列表
   - 当前位置: `moodle_dl/moodle/mods/wiki.py` line 54
   - Mobile API: `mod_wiki_get_wikis`
   - Web API Fallback: `core_course_get_contents` + wiki parsing
   - 预期收益: 提高维基模块的适配性
   - 工作量: 中等
   - 优先级: 🟢 低

8. **mod_forum_get_discussion_posts** → Web Service API
   - 用途: 获取讨论的帖子列表
   - 当前位置: `moodle_dl/moodle/mods/forum.py` line 216, 218
   - Mobile API: `mod_forum_get_discussion_posts` 或 `mod_forum_get_forum_discussion_posts`
   - Web API Fallback: 需要查找对应的 Web Service API
   - 预期收益: 提高论坛讨论的完整性
   - 工作量: 大
   - 优先级: 🟢 低

#### ⚠️ 特殊情况 (无法实现 Web API Fallback)

9. **tool_mobile_get_autologin_key**
   - 用途: 获取移动应用自动登录密钥
   - 当前位置: `moodle_dl/moodle/cookie_handler.py` line 37
   - 类型: Mobile 独占 API
   - Web API Fallback: ❌ 不存在等效的 Web Service API
   - 建议: 保留 Mobile API，可考虑实现 SSO fallback
   - 优先级: ⚠️ 无法实现

---

## 🛠️ 实施计划

### Phase 1: 高优先级 (预计 4-6 小时)

**目标:** 实现最常用的 2 个 API fallback

1. **mod_assign_get_assignments fallback**
   - 实现 fallback 方法
   - 添加单元测试
   - 集成到现有代码
   - 预计: 2 小时

2. **mod_forum_get_forum_discussions fallback**
   - 实现 fallback 方法 (考虑分页)
   - 添加单元测试
   - 集成到现有代码
   - 预计: 2-3 小时

**成果:** 提高 assign 和 forum 模块的可靠性

### Phase 2: 中优先级 (预计 3-4 小时)

**目标:** 实现 3 个中等优先级的 API fallback

1. **mod_assign_get_submissions fallback** (1.5 小时)
2. **mod_workshop_get_submissions fallback** (2 小时)
3. **其他补充** (0.5 小时)

**成果:** 完整支持提交数据获取

### Phase 3: 低优先级 (预计 3-4 小时)

**目标:** 实现剩余的 2 个 API fallback

1. **mod_wiki_get_wikis fallback**
2. **mod_forum_get_discussion_posts fallback**

**成果:** 全面覆盖所有常用模块

---

## 📐 架构设计

### 实施模式

每个 Fallback 实现应遵循统一的模式：

```python
class SomeModule(MoodleMod):
    async def _fetch_using_mobile_api(self, courses):
        """优先使用 Mobile API"""
        try:
            result = await self.client.async_post('mobile_api_name', data)
            return result
        except RequestRejectedError:
            # Fallback to Web API
            return await self._fetch_using_web_api(courses)
    
    async def _fetch_using_web_api(self, courses):
        """使用 Web API 作为 fallback"""
        # 通过 core_course_get_contents 或其他 Web Service API 获取数据
        result = await self.client.async_post('core_course_get_contents', data)
        # 转换数据格式以匹配 Mobile API 的返回结构
        return self._transform_web_api_response(result)
    
    def _transform_web_api_response(self, web_api_response):
        """将 Web API 的响应转换为 Mobile API 格式"""
        # 确保返回的数据结构一致
        pass
```

### 数据转换

关键是确保 Web API 返回的数据能被转换成与 Mobile API 相同的格式。这需要：

1. 了解两个 API 的响应结构差异
2. 实现转换函数
3. 处理字段缺失的情况

### 错误处理

```python
async def fetch_with_fallback(self, ...):
    try:
        # 尝试 Mobile API
        return await self._fetch_using_mobile_api(...)
    except (RequestRejectedError, TimeoutError, KeyError) as e:
        logging.warning(f"Mobile API failed: {e}, trying Web API...")
        try:
            return await self._fetch_using_web_api(...)
        except Exception as web_error:
            logging.error(f"Both APIs failed: {web_error}")
            raise
```

---

## 📈 预期收益

### 短期收益 (Phase 1-2)
- ✅ 提高 assign 和 forum 模块的可靠性 (覆盖最常用的 2 个模块)
- ✅ 减少 Mobile API 调用失败的情况
- ✅ 改善用户体验

### 长期收益 (Phase 1-3)
- ✅ 全面覆盖所有主要模块
- ✅ 建立完整的 fallback 策略
- ✅ 提高系统整体可靠性
- ✅ 为未来的进一步扩展打下基础

### 成本
- 开发时间: 10-14 小时
- 测试时间: 4-6 小时
- 文档时间: 2-3 小时
- 总计: 16-23 小时

---

## 🔍 验证清单

- [ ] 每个 Mobile API 都有对应的 Web API 或 fallback 策略
- [ ] fallback 实现包含充分的错误处理
- [ ] 数据转换确保格式一致
- [ ] 单元测试覆盖主要场景
- [ ] 集成测试通过
- [ ] 文档更新完整
- [ ] 日志记录清晰

---

## 📚 参考资源

### Moodle 官方 API 文档

- Mobile API: `moodle_official_repo_for_reference/public/lib/external/`
- Web Service API: `moodle_official_repo_for_reference/public/webservice/`
- 官方移动应用: `moodle_mobile_app_official_repo_for_reference/src/addons/`

### 当前项目实现

- Course Validator (示例 fallback): `moodle_dl/moodle/course_validator.py`
- Mod 基类: `moodle_dl/moodle/mods/mod.py`
- 现有 fallback 参考: `moodle_dl/downloader/download_service.py` (_fetch_course_info_from_web_api)

---

## 🎯 建议行动

### 立即行动

1. **审计所有 Mobile API 调用**
   - 确认是否有其他遗漏的 Mobile API
   - 标记每个 API 是否需要 fallback

2. **优先实现 Phase 1**
   - 这将覆盖最常用的 2 个模块
   - 投资回报率最高

3. **建立测试框架**
   - 为 fallback 逻辑创建完整的测试
   - 测试数据转换的正确性

### 短期 (1-2 周)

- [ ] 完成 Phase 1 实现
- [ ] 编写和通过测试
- [ ] 文档完善

### 中期 (1 个月)

- [ ] 完成 Phase 2 实现
- [ ] 收集用户反馈
- [ ] 优化数据转换

### 长期 (进行中)

- [ ] 完成 Phase 3 实现
- [ ] 持续改进 fallback 策略
- [ ] 适配新的 Moodle 功能

---

## 💡 特殊注意事项

### tool_mobile_get_autologin_key 的替代方案

由于这个 API 没有 Web Service 等效，可以考虑：

1. **SSO Fallback**: 当 autologin 失败时，使用 SSO 登录
2. **Manual Cookie Input**: 允许用户手动输入 cookies
3. **Graceful Degradation**: 记录警告，继续执行其他操作

### 向后兼容性

- 保持现有的 Mobile API 调用
- Fallback 应该是透明的（自动发生）
- 不影响现有的工作流程

### 性能考虑

- Fallback 可能比 Mobile API 更慢
- 考虑实现缓存机制
- 监控 fallback 使用频率

---

**创建日期:** 2025-11-20  
**优先级:** 🟡 中等  
**预计完成:** 2025-12-15  
**负责人:** 代码质量改进团队

