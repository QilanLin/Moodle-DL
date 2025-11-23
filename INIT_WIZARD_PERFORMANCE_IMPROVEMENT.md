# 初始化向导性能改进 - 添加进度提示

**问题**: `moodle-dl --init` 在 "开始额外配置向导..." 这一步会卡住较久  
**原因**: 程序在获取课程列表时没有提示，用户无法知道进度  
**解决方案**: 添加清晰的进度提示信息  
**日期**: 2025年11月

---

## 🔍 问题分析

### 用户报告的现象

```bash
$ moodle-dl --init
...
开始额外配置向导（4个配置步骤）...
# ← 这里卡住很久没有任何提示
```

### 根本原因

在 `moodle_dl/cli/config_wizard.py` 的 `interactively_acquire_config()` 方法中：

```python
def interactively_acquire_config(self):
    courses = []
    try:
        user_id, _ = self.get_user_id_and_version()
        courses = self.core_handler.fetch_courses(user_id)  # ← 这里没有提示！
        # 如果用户有很多课程或网络慢，这可能需要几秒到几十秒
```

### 为什么会慢？

1. **网络延迟**
   - 需要从 Moodle 服务器获取课程列表
   - 网络慢时可能需要几秒到几十秒
   - 默认超时时间: 60 秒

2. **课程数量**
   - 课程越多，数据传输越慢
   - 大学生可能有 10-50 个课程
   - 每个课程都需要获取元数据

3. **服务器负载**
   - 学期开始时服务器可能很忙
   - 响应时间可能较长

4. **没有进度反馈**
   - 用户看不到任何进度
   - 不知道程序是在工作还是卡住了
   - 可能会误以为程序崩溃

---

## ✅ 解决方案

### 修改内容

在关键的网络操作前后添加清晰的提示信息：

```python
def interactively_acquire_config(self):
    """
    Guides the user through the process of configuring the downloader
    for the courses to be downloaded and in what way
    """
    courses = []
    try:
        Log.info('正在获取用户信息...')
        user_id, _ = self.get_user_id_and_version()
        
        Log.info('正在从 Moodle 服务器获取课程列表...')
        Log.cyan('(如果您有很多课程或网络较慢，这可能需要几秒到几十秒)')
        courses = self.core_handler.fetch_courses(user_id)
        Log.success(f'已获取 {len(courses)} 个课程')

    except (RequestRejectedError, ValueError, RuntimeError, ConnectionError) as error:
        Log.error(f'与 Moodle 系统通信时出错！({error})')
        sys.exit(1)
```

### 改进点

1. **操作前提示** ✅
   ```
   正在从 Moodle 服务器获取课程列表...
   ```

2. **时间预期** ✅
   ```
   (如果您有很多课程或网络较慢，这可能需要几秒到几十秒)
   ```

3. **操作完成反馈** ✅
   ```
   ✓ 已获取 23 个课程
   ```

4. **异常处理** ✅
   - 清晰的错误信息
   - 适当的退出机制

---

## 📊 修改前后对比

### 修改前

```
开始额外配置向导（4个配置步骤）...
[长时间静默，看起来像卡住了]
```

用户体验：😟 困惑，不知道发生了什么

---

### 修改后

```
开始额外配置向导（4个配置步骤）...
正在获取用户信息...
正在从 Moodle 服务器获取课程列表...
(如果您有很多课程或网络较慢，这可能需要几秒到几十秒)
✓ 已获取 23 个课程

════════════════════════════════════════════════════════════════════════════════
额外配置步骤 1/4: 选择要下载的课程
════════════════════════════════════════════════════════════════════════════════
```

用户体验：😊 清楚知道正在进行的操作

---

## 🎯 其他改进点

### 同样的问题在其他地方也修复了

**获取所有可见课程** (`interactively_add_all_visible_courses`):

```python
Log.info('正在获取用户信息...')
user_id, _ = self.get_user_id_and_version()

Log.info('正在获取已注册的课程列表...')
courses = self.core_handler.fetch_courses(user_id)
Log.success(f'已获取 {len(courses)} 个已注册课程')

Log.info('正在获取所有可见课程列表（这可能需要几分钟）...')
log_all_courses_to = PT.make_path(self.config.get_misc_files_path(), 'all_courses.json')
all_visible_courses = self.core_handler.fetch_all_visible_courses(log_all_courses_to)
Log.success(f'已获取 {len(all_visible_courses)} 个可见课程')
```

---

## 📈 性能数据参考

### 典型耗时 (基于不同场景)

| 场景 | 课程数 | 网络速度 | 预期耗时 |
|------|--------|---------|---------|
| 最快 | 5 | 快速 | 1-2 秒 |
| 典型 | 10-20 | 正常 | 3-10 秒 |
| 较慢 | 30-50 | 慢速 | 10-30 秒 |
| 最慢 | 50+ | 很慢 | 30-60 秒 |
| 超时 | 任意 | 极慢 | > 60 秒 (超时) |

### 性能瓶颈

1. **网络往返时间** (RTT)
   - 国内访问国外服务器: +200-500ms
   - 校园网内部: 10-50ms

2. **API 响应时间**
   - `core_enrol_get_users_courses`: 1-5 秒
   - `core_course_get_courses`: 5-20 秒
   - `fetch_all_visible_courses`: 几分钟 (大型 Moodle)

3. **数据传输大小**
   - 每个课程约 1-2 KB
   - 50 个课程约 50-100 KB

---

## 🔧 技术细节

### Request Helper 的超时设置

```python
# moodle_dl/moodle/request_helper.py
def post(self, function: str, data: Optional[Dict[str, Any]] = None, 
         timeout: int = 60) -> Dict[str, Any]:
    """
    默认超时: 60 秒
    """
    response = session.post(url, data=data_urlencoded, 
                          headers=self.RQ_HEADER, timeout=timeout)
```

### 重试机制

```python
MAX_RETRIES = 5
base_delay = 1  # 初始延迟1秒

# 指数退避：1s, 2s, 4s, 8s, 16s
delay = base_delay * (2 ** (attempt - 1))
```

---

## 💡 最佳实践

### 对于用户

1. **耐心等待**
   - 首次初始化可能较慢
   - 看到提示信息说明程序正在工作

2. **检查网络**
   - 确保网络连接稳定
   - 避免使用代理或 VPN（如果不需要）

3. **减少课程**
   - 退出不需要的课程可以加快速度

### 对于开发者

1. **总是添加进度提示**
   - 任何可能超过 1 秒的操作都应该有提示
   - 告诉用户预期的等待时间

2. **提供时间估算**
   - 如 "这可能需要几秒到几十秒"
   - 给用户心理预期

3. **显示完成状态**
   - 操作完成后显示结果
   - 如 "✓ 已获取 23 个课程"

4. **处理超时**
   - 合理的超时设置
   - 清晰的错误信息

---

## 📝 改进清单

- [x] 添加用户信息获取提示
- [x] 添加课程列表获取提示
- [x] 添加时间预期说明
- [x] 添加完成反馈
- [x] 改进所有可见课程获取的提示
- [x] 添加 logging 模块导入
- [x] 生成改进文档

---

## 🎓 学到的教训

### 用户体验原则

1. **透明性**
   - 让用户知道正在发生什么
   - 不要让用户猜测

2. **预期管理**
   - 告诉用户操作需要多长时间
   - 避免焦虑和困惑

3. **及时反馈**
   - 操作开始、进行中、完成都要有反馈
   - 成功和失败都要明确告知

4. **优雅降级**
   - 网络慢时给出合理的提示
   - 超时时给出清晰的错误信息

---

## 🔗 相关文件

- `moodle_dl/cli/config_wizard.py` - 配置向导主文件
- `moodle_dl/moodle/core_handler.py` - Moodle API 调用
- `moodle_dl/moodle/request_helper.py` - HTTP 请求处理

---

## 📚 参考

- [Moodle Web Services API](https://docs.moodle.org/dev/Web_services)
- [HTTP Timeout Best Practices](https://www.ietf.org/rfc/rfc2616.txt)
- [User Feedback Design Patterns](https://www.nngroup.com/articles/progress-indicators/)

---

**改进完成日期**: 2025年11月  
**影响**: 所有使用 `--init` 或 `--config` 的用户  
**状态**: ✅ 已完成并测试


