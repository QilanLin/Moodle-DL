# 网页版 API 作为 Mobile API 的 Fallback 策略

## 提议方案

> 在选择白名单后，再让用户输入他要下载的他有权限访问但没有 enrol 进去的课程，使用网页版 API 作为 Mobile API 的 fallback（用每个 Mobile API 对应的网页版 API 作为该 Mobile API 的 fallback）

## 方案分析

### 核心思想

```
用户流程：

1. 用户运行 moodle-dl --init --sso
   ↓
2. 通过 Mobile API 获取 enrolled 课程
   ├─ 如果找到课程 → 显示给用户选择白名单/黑名单
   └─ 如果没找到 → 继续
   ↓
3. 问用户："你还有其他有权限但没 enrolled 的课程吗？"
   ├─ 用户输入课程 ID（或 URL）
   └─ moodle-dl 用网页版 API 验证课程可访问性
   ↓
4. 合并两个列表
   ├─ enrolled 课程（来自 Mobile API）
   └─ 手动指定课程（来自网页版 API）
   ↓
5. 显示给用户选择白名单
   ↓
6. 下载时使用对应的 API
   ├─ enrolled 课程 → Mobile API
   └─ 手动指定课程 → 网页版 API
```

### 优势分析

✅ **优势 1：支持教师/TA 模式**
- Mobile API 只看 enrolled 课程
- 教师/TA 通常不 enrolled，只有权限
- 网页版 API fallback 完美解决这个问题

✅ **优势 2：最小改动**
- 不破坏现有 moodle-dl 逻辑
- 只是在初始化时新增可选步骤
- 下载逻辑可以保持不变（只是用不同 API）

✅ **优势 3：用户友好**
- 在 --init 时就让用户指定
- 而不是每次下载时手动指定
- 配置文件里保存这个列表

✅ **优势 4：符合 Moodle 设计**
- 充分利用网页版 API 的能力
- 不强制用户 enroll
- 尊重权限系统的设计

✅ **优势 5：灵活的 Fallback 机制**
- 每个 Mobile API 都有对应的网页版 API
- 例如：
  - `core_enrol_get_users_courses` ← `core_course_get_courses` / `core_course_get_contents`
  - 其他 API 也类似有 fallback

### 劣势分析

❌ **劣势 1：用户需要知道课程 ID**
- 教师/TA 一般知道课程 ID
- 但普通用户可能不知道
- 可以显示一个 URL 解析工具帮助用户提取 ID

❌ **劣势 2：需要额外的验证**
- 需要检查用户输入的课程是否真的可访问
- 需要处理"虚假"的课程 ID（不存在的课程）
- 需要错误提示

❌ **劣势 3：更复杂的下载逻辑**
- 需要追踪哪个课程来自哪个 API
- 下载时需要调用不同的 API
- 可能的错误处理更复杂

❌ **劣势 4：权限可能变化**
- 用户可能输入一个他认为可以访问的课程
- 但实际上权限已经被取消
- 需要在下载时再次检查

## 详细设计

### 1. 初始化流程修改

```python
# moodle_dl/main.py 中的 choose_task

def init_moodle_account(config: ConfigHelper, opts: MoodleDlOpts):
    """初始化 Moodle 账户"""
    
    # ... 现有的初始化代码 ...
    
    # 新增：获取 enrolled 课程
    logging.info("正在获取已注册的课程...")
    enrolled_courses = download_service.get_enrolled_courses()
    
    if enrolled_courses:
        # 显示 enrolled 课程
        logging.info(f"找到 {len(enrolled_courses)} 门已注册的课程")
        # ... 显示白名单/黑名单选择 ...
    
    # 新增步骤：询问是否有其他课程
    print("\n" + "=" * 80)
    print("额外步骤：手动指定课程")
    print("=" * 80)
    print("""
你可能是某些课程的教师或助教，但没有以学生身份注册这些课程。
你仍然可以通过提供课程 ID 来下载这些课程的内容。

示例：
  • 课程 ID: 137304
  • 课程 URL: https://keats.kcl.ac.uk/course/view.php?id=137304
    → 提取: 137304
""")
    
    manually_specified_ids = []
    while True:
        course_id_input = input(
            "\n输入课程 ID（或留空完成）: "
        ).strip()
        
        if not course_id_input:
            break
        
        # 验证课程 ID
        try:
            course_id = int(course_id_input)
            
            # 使用网页版 API 验证课程可访问性
            course_info = validate_course_with_web_api(
                config, course_id
            )
            
            if course_info:
                logging.info(f"✅ 课程验证成功: {course_info['fullname']}")
                manually_specified_ids.append(course_id)
            else:
                logging.error(
                    f"❌ 无法访问课程 {course_id}"
                )
        
        except ValueError:
            logging.error("请输入有效的数字课程 ID")
        except Exception as e:
            logging.error(f"验证课程时出错: {e}")
    
    # 保存手动指定的课程 ID
    if manually_specified_ids:
        config.set_property(
            'manually_specified_course_ids',
            manually_specified_ids
        )
        logging.info(
            f"已保存 {len(manually_specified_ids)} "
            f"个手动指定的课程"
        )
```

### 2. 配置文件扩展

```json
{
    "token": "451d8ccfcac580505c984527356d9f67",
    "moodle_domain": "keats.kcl.ac.uk",
    "download_course_ids": [134659],
    
    // 新增：手动指定的课程（通过网页版 API 访问）
    "manually_specified_course_ids": [137304, 137305],
    
    // ... 其他配置 ...
}
```

### 3. 下载流程修改

```python
# moodle_dl/downloader/download_service.py

class DownloadService:
    
    def gen_all_tasks(self) -> List[Task]:
        """生成所有下载任务"""
        
        all_tasks = []
        
        # 步骤 1：获取 enrolled 课程（使用 Mobile API）
        enrolled_courses = self._get_enrolled_courses()
        for course in enrolled_courses:
            tasks = self._create_tasks_for_course(
                course,
                api_source='mobile'  # 标记 API 来源
            )
            all_tasks.extend(tasks)
        
        # 步骤 2：获取手动指定的课程（使用网页版 API）
        manually_specified_ids = (
            self.config.get_property('manually_specified_course_ids', [])
        )
        
        for course_id in manually_specified_ids:
            try:
                # 使用网页版 API 获取课程内容
                course_info = self._get_course_with_web_api(course_id)
                tasks = self._create_tasks_for_course(
                    course_info,
                    api_source='web'  # 标记 API 来源
                )
                all_tasks.extend(tasks)
            
            except CourseNotAccessibleError:
                logging.error(
                    f"无法访问课程 {course_id}，跳过"
                )
            except Exception as e:
                logging.error(
                    f"获取课程 {course_id} 时出错: {e}，跳过"
                )
        
        return all_tasks
```

### 4. API 选择策略

```python
# moodle_dl/downloader/task.py

class Task:
    
    def real_run(self):
        """执行下载任务"""
        
        # 根据课程来源选择 API
        if self.api_source == 'mobile':
            # 使用 Mobile API
            self._download_with_mobile_api()
        
        elif self.api_source == 'web':
            # 使用网页版 API
            self._download_with_web_api()
    
    def _download_with_mobile_api(self):
        """使用 Mobile API 下载"""
        # 现有逻辑
        pass
    
    def _download_with_web_api(self):
        """使用网页版 API 下载"""
        # 网页版 API 的下载逻辑
        # 基本上和 Mobile API 逻辑类似，但调用不同的 API
        pass
```

### 5. 验证函数

```python
# moodle_dl/moodle/course_validator.py (新文件)

def validate_course_with_web_api(
    config: ConfigHelper,
    course_id: int
) -> Optional[Dict]:
    """
    使用网页版 API 验证课程可访问性
    
    返回：
      - 课程信息（如果可访问）
      - None（如果不可访问）
    """
    
    try:
        # 调用网页版 API
        course_info = request_helper.call_webservice(
            'core_course_get_courses',
            {'options[ids][0]': course_id}
        )
        
        if course_info and len(course_info) > 0:
            return course_info[0]
        else:
            return None
    
    except Exception as e:
        logging.debug(f"网页版 API 错误: {e}")
        return None
```

## 实现步骤

### Phase 1：基础框架（第 1 周）

- [ ] 在 `config.py` 中添加 `manually_specified_course_ids` 字段
- [ ] 在 `init` 流程中添加手动输入课程 ID 的步骤
- [ ] 实现 `validate_course_with_web_api` 验证函数
- [ ] 添加单元测试

### Phase 2：下载逻辑（第 2 周）

- [ ] 修改 `DownloadService` 支持两种数据源
- [ ] 修改 `Task` 支持两种 API 调用
- [ ] 处理 API 调用失败的情况
- [ ] 添加日志记录

### Phase 3：用户体验（第 3 周）

- [ ] 改进交互提示
- [ ] 添加 URL 解析工具（自动提取课程 ID）
- [ ] 优化错误消息
- [ ] 添加 FAQ 文档

## 潜在问题与解决方案

### 问题 1：用户输入无效的课程 ID

**解决方案**：
```python
# 在验证时捕获异常
try:
    course_id = int(user_input)
except ValueError:
    print("请输入有效的数字课程 ID")
    continue

# 检查课程是否真的可访问
if not validate_course_with_web_api(config, course_id):
    print(f"❌ 无法访问课程 {course_id}")
    print("   可能原因：")
    print("   • 课程 ID 不存在")
    print("   • 你没有访问权限")
    print("   • 课程已被删除或存档")
    continue
```

### 问题 2：权限在配置后被取消

**解决方案**：
```python
# 在每次下载时重新检查权限
def download_course(course_id, api_source):
    try:
        if api_source == 'web':
            # 再次验证权限
            if not validate_course_with_web_api(config, course_id):
                raise CourseNotAccessibleError(
                    f"课程 {course_id} 已不可访问"
                )
        
        # 继续下载
        contents = get_course_contents(course_id)
        download(contents)
    
    except CourseNotAccessibleError as e:
        logger.error(f"下载失败: {e}")
        # 继续处理其他课程
```

### 问题 3：混合不同 API 的课程列表

**解决方案**：
```python
# 在任务中标记来源
class Task:
    def __init__(self, course, api_source='mobile'):
        self.course = course
        self.api_source = api_source  # 'mobile' 或 'web'
    
    def real_run(self):
        if self.api_source == 'mobile':
            # Mobile API 逻辑
        else:
            # 网页版 API 逻辑
```

### 问题 4：网页版 API 返回 "not accessible"

**解决方案**：
```python
def validate_course_with_web_api(config, course_id):
    try:
        course_info = call_web_api(...)
        return course_info
    except ContextNotAccessibleError:
        # 课程被隐藏或你没有权限
        logger.warning(
            f"课程 {course_id} 返回 'not accessible' 错误\n"
            f"可能的原因：\n"
            f"  1. 课程被隐藏或存档\n"
            f"  2. 你的权限已被取消\n"
            f"  3. 课程不存在\n"
        )
        return None
    except Exception as e:
        logger.error(f"API 调用失败: {e}")
        return None
```

## 与现有功能的兼容性

### 白名单/黑名单功能

**现有**：
```
Mobile API → enrolled 课程 → 白名单/黑名单选择
```

**改进后**：
```
Mobile API → enrolled 课程
          ↘
           → 合并 → 白名单/黑名单选择 ← 手动指定课程 ← 网页版 API
```

### 断点续传功能

**兼容**：
- 无论哪个 API 获取的课程，都可以使用断点续传
- 在数据库中标记课程来源
- 下载时根据来源选择对应 API

### 配置验证框架

**兼容**：
- 添加新字段验证规则
- `manually_specified_course_ids` 必须是整数列表
- 验证每个 ID 是否有效

## 配置文件演化

### v1（当前）：
```json
{
    "download_course_ids": [134659]
}
```

### v2（改进后）：
```json
{
    "download_course_ids": [134659],
    "manually_specified_course_ids": [137304, 137305]
}
```

### v3（未来可能）：
```json
{
    "courses": {
        "enrolled": [134659],
        "manual": [137304, 137305],
        "whitelist": [134659, 137304],
        "blacklist": []
    }
}
```

## 对用户的影响

### 对学生用户
- ✅ 无影响（他们 enrolled 的课程仍能正常工作）
- ✅ 可选功能（如果需要可以使用）

### 对教师/TA 用户
- ✅ 获得完整的课程访问能力
- ✅ 不需要自己 enroll
- ✅ 更符合他们的工作流程

### 对系统管理员
- ✅ 可以下载他管理的所有课程
- ✅ 可以创建完整的课程备份

## 总结

| 方面 | 评分 | 说明 |
|------|------|------|
| **可行性** | ⭐⭐⭐⭐⭐ | 完全可行，技术上无障碍 |
| **用户体验** | ⭐⭐⭐⭐ | 需要一些 UX 优化 |
| **兼容性** | ⭐⭐⭐⭐⭐ | 与现有功能完全兼容 |
| **维护性** | ⭐⭐⭐⭐ | 代码清晰，容易维护 |
| **性能影响** | ⭐⭐⭐⭐⭐ | 无负面影响 |

**总体评价**：✅ 强烈推荐实现

---

**方案提出者**：用户  
**分析完成日期**：2025-11-20  
**状态**：可接受，建议实现

