# 重试机制改进说明

## 改进概述

实施了优先级3的改进：更智能的错误处理和重试机制

## 主要改进

### 1. 新的异常类型体系 (`moodle_dl/exceptions.py`)

创建了专门的异常类型文件，按照错误类型分类：

```
MoodleDLException (基础异常)
├── MoodleNetworkError (可重试)
│   └── 网络连接错误、超时、DNS 失败等
├── MoodleAPIError (通常不可重试)
│   ├── RequestRejectedError (向后兼容)
│   └── API 错误、无效 JSON、资源不存在等
└── MoodleAuthError (不可重试)
    └── Token 失效、权限不足、未授权等
```

**优点**：
- 清晰区分可重试和不可重试的错误
- 允许上层代码根据异常类型做出不同的处理
- 保持向后兼容性（RequestRejectedError 仍然可用）

### 2. 指数退避重试机制

**改进前**：
- 固定重试5次
- 固定延迟1秒
- 所有错误一视同仁

**改进后**：
- 智能区分错误类型
- 指数退避：1秒 → 2秒 → 4秒 → 8秒 → 16秒
- 只重试可重试的错误

**重试时间线对比**：

```
旧机制：
尝试1 失败 → 等待1秒 → 尝试2 失败 → 等待1秒 → ... → 尝试5 失败
总耗时：约 4 秒

新机制：
尝试1 失败 → 等待1秒 → 尝试2 失败 → 等待2秒 → 尝试3 失败 → 等待4秒 → 尝试4 失败 → 等待8秒 → 尝试5 失败
总耗时：约 15 秒
```

**优点**：
- 给服务器更多恢复时间
- 减少对服务器的压力
- 提高成功率

### 3. 智能错误分类

#### 网络错误（可重试）
- `aiohttp.ClientError` - 客户端连接错误
- `asyncio.TimeoutError` - 请求超时
- `OSError` - 系统级错误
- HTTP 408 (Request Timeout)
- HTTP 409 (Conflict)
- HTTP 429 (Too Many Requests)
- HTTP 503 (Service Unavailable)

#### API 错误（通常不可重试）
- `ContentTypeError` - 响应类型错误
- `JSONDecodeError` - JSON 解析失败
- HTTP 404 (Not Found)
- HTTP 500 (Internal Server Error)
- 其他 HTTP 错误

#### 认证错误（不可重试）
- HTTP 401 (Unauthorized)
- HTTP 403 (Forbidden)
- `invalidtoken` - Token 失效
- `accessdenied` - 访问被拒绝
- `nopermission` - 权限不足

### 4. 更好的日志输出

**改进后的警告日志**：
```
网络错误，2秒后重试 (尝试 2/5): [TimeoutError]
网络错误，4秒后重试 (尝试 3/5): [ClientConnectorError: Cannot connect to host]
```

**优点**：
- 用户可以清楚了解重试进度
- 便于调试网络问题
- 中文提示更友好

## 技术实现

### 修改的文件

1. **新增文件**：
   - `moodle_dl/exceptions.py` - 异常类型定义

2. **修改文件**：
   - `moodle_dl/moodle/request_helper.py` - 重试逻辑改进

### 核心代码改进

#### async_post 方法

```python
async def async_post(self, function: str, data: Dict[str, str] = None, timeout: int = 60) -> Dict:
    base_delay = 1  # 初始延迟1秒
    attempt = 0

    while attempt < self.MAX_RETRIES:
        try:
            # 发送请求
            ...
        except aiohttp.client_exceptions.ClientResponseError as req_err:
            # 根据 HTTP 状态码分类
            if req_err.status in [401, 403]:
                raise MoodleAuthError(...)  # 不可重试
            elif req_err.status in [408, 409, 429, 503]:
                pass  # 可重试，继续到重试逻辑
            else:
                raise MoodleAPIError(...)  # 不可重试
        except ...:
            # 其他错误处理

        # 重试逻辑（只有可重试的错误才会到达这里）
        attempt += 1
        if attempt < self.MAX_RETRIES:
            delay = base_delay * (2 ** (attempt - 1))  # 指数退避
            logging.warning("网络错误，%d秒后重试 (尝试 %d/%d): %s", ...)
            await asyncio.sleep(delay)
        else:
            raise MoodleNetworkError(...)
```

## 向后兼容性

- `RequestRejectedError` 仍然可以从 `moodle_dl.moodle.request_helper` 导入
- 现有代码无需修改即可继续工作
- 新代码可以使用更精确的异常类型

## 测试结果

✅ 语法检查通过
✅ 导入测试成功
✅ `moodle-dl --help` 正常运行
✅ 现有异常导入向后兼容

## 预期效果

1. **提高成功率**：通过指数退避和智能重试，提高在网络不稳定情况下的成功率
2. **减少服务器压力**：不立即重试，给服务器更多恢复时间
3. **更好的用户体验**：
   - 认证错误立即失败，不浪费时间重试
   - 网络错误智能重试，提高成功率
   - 中文错误提示更友好
4. **更好的调试**：
   - 详细的重试日志
   - 明确的错误分类
   - 便于定位问题

## 未来改进空间

- [ ] 实现重试队列（统一管理失败的请求）
- [ ] 根据服务器响应动态调整重试策略
- [ ] 记录重试统计信息用于分析
- [ ] 支持自定义重试策略配置
