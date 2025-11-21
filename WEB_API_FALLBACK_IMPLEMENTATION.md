# Web API Fallback 实现文档

> **最后更新**: 2025-01-XX  
> **状态**: ✅ 已完成 (26/26, 100%)

## 概述

为所有 Moodle Mobile Download API 添加对应的 Web API fallback，确保在 Mobile API 失败时（如版本不支持、API 不可用等）能够从 `core_course_get_contents` 中提取模块信息。

**实现完成日期**: 2025-01-XX  
**验证状态**: ✅ 所有模块已通过质量检查（见 `QUALITY_CHECK_FALLBACK_IMPLEMENTATION.md`）

## 实现策略

### 核心修改

1. **`common.py` - `fetch_mod_entries` 方法**
   - 移除版本检查，总是尝试调用 `real_fetch_mod_entries`
   - 只有当 Mobile API 和 Web API fallback 都失败时，才提示版本不支持

2. **`common.py` - 添加通用方法**
   - `extract_modules_from_core_contents`: 从 `core_contents` 中提取指定类型的模块

### 实现模式

每个模块需要：

1. **修改 `real_fetch_mod_entries` 方法**
   ```python
   # 首先尝试使用 Mobile API
   try:
       response = await self.client.async_post(
           'mod_{modname}_get_{modname}s_by_courses',
           self.get_data_for_mod_entries_endpoint(courses),
       )
       {modname}s = response.get('{modname}s', [])
   except (RequestRejectedError, Exception) as e:
       # Mobile API 失败，尝试 Web API fallback
       logging.debug(f"Mobile API 获取 {ModName} 模块失败: {e}，尝试使用 Web API fallback...")
       {modname}s = await self._fetch_{modname}s_web_api(courses, core_contents)
   ```

2. **添加 `_fetch_{modname}s_web_api` 方法**
   ```python
   async def _fetch_{modname}s_web_api(
       self, courses: List[Course], core_contents: Dict[int, List[Dict]]
   ) -> List[Dict]:
       """
       使用 Web API fallback 获取 {ModName} 模块信息。
       
       这是 mod_{modname}_get_{modname}s_by_courses 的 fallback 实现。
       通过 core_course_get_contents 获取 {modname} 模块信息。
       
       Return: 转换为与 Mobile API 相同格式的 {modname} 列表
       """
       logging.debug('🌐 使用 Web API fallback 获取 {ModName} 模块信息...')
       
       {modname}s = []
       
       # 从 core_contents 中提取 {modname} 模块
       modules_by_course = self.extract_modules_from_core_contents(courses, core_contents, '{modname}')
       
       for course in courses:
           course_id = course.id
           if course_id not in modules_by_course:
               continue
           
           for module in modules_by_course[course_id]:
               # 将 Web API 的 {modname} 模块转换为 Mobile API 的格式
               {modname} = {{
                   'id': module.get('instance', 0),
                   'coursemodule': module.get('id', 0),
                   'course': course_id,
                   'name': module.get('name', '{ModName}'),
                   'intro': module.get('description', ''),
                   'introformat': 1,
                   # ... 其他字段根据模块类型填充默认值
                   'timemodified': module.get('timemodified', 0),
               }}
               {modname}s.append({modname})
       
       if not {modname}s:
           logging.warning('⚠️ Web API fallback 未找到任何 {ModName} 模块')
           raise ValueError('Web API 未能检索任何 {ModName} 模块信息')
       
       logging.debug(f'✅ Web API fallback 成功获取 {{len({modname}s)}} 个 {ModName} 模块')
       return {modname}s
   ```

## 实现进度

### ✅ 已完成 (26/26, 100%) - 2025-01-XX 更新

**显式实现 Web API Fallback (23 个模块)**:
1. **assign.py** - `_fetch_assignments_web_api`
2. **bigbluebuttonbn.py** - `_fetch_bigbluebuttonbns_web_api`
3. **book.py** - `_fetch_books_web_api`
4. **chat.py** - `_fetch_chats_web_api`
5. **choice.py** - `_fetch_choices_web_api`
6. **data.py** - `_fetch_databases_web_api`
7. **feedback.py** - `_fetch_feedbacks_web_api`
8. **folder.py** - `_fetch_folders_web_api`
9. **forum.py** - 已有 fallback (优雅降级)
10. **glossary.py** - `_fetch_glossaries_web_api`
11. **h5pactivity.py** - `_fetch_h5pactivities_web_api`
12. **imscp.py** - `_fetch_imscps_web_api`
13. **label.py** - `_fetch_labels_web_api`
14. **lesson.py** - `_fetch_lessons_web_api`
15. **lti.py** - `_fetch_ltis_web_api`
16. **page.py** - `_fetch_pages_web_api`
17. **quiz.py** - `_fetch_quizzes_web_api`
18. **resource.py** - `_fetch_resources_web_api`
19. **scorm.py** - `_fetch_scorms_web_api`
20. **survey.py** - `_fetch_surveys_web_api`
21. **url.py** - `_fetch_urls_web_api`
22. **wiki.py** - `_fetch_wikis_web_api`
23. **workshop.py** - 已有 fallback (优雅降级)

**特殊处理（无需显式 fallback）(3 个模块)**:
1. **calendar.py** - 使用 `core_calendar_get_calendar_events` (已有 fallback 逻辑)
2. **qbank.py** - 已从 `core_course_get_contents` 提取（无需 Mobile API）
3. **subsection.py** - 已从 `core_course_get_contents` 提取（无需 Mobile API）

**状态**: ✅ **所有 26 个模块都已实现 fallback 或特殊处理**

## 注意事项

1. **字段映射**: Web API 的字段名可能与 Mobile API 不同，需要正确映射
2. **默认值**: Web API 可能缺少某些字段，需要填充合理的默认值
3. **特殊处理**: 某些模块（如 book, lesson）需要额外的结构处理
4. **错误处理**: 如果 Web API fallback 也失败，应该抛出异常，让 `fetch_mod_entries` 处理

## 参考

- 官方 Moodle Mobile App: `moodle_mobile_app_official_repo_for_reference/`
- 官方 Moodle Core: `moodle_official_repo_for_reference/`
- 官方开发文档: `devdocs_official_repo_for_reference/`

