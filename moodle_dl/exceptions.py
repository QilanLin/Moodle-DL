"""
Moodle-DL 异常类型定义

根据错误类型对异常进行分类，以便实现更智能的重试机制：
- 网络错误：可重试（超时、连接失败等）
- API 错误：不可重试（Moodle 返回错误响应）
- 认证错误：不可重试（Token 失效、权限不足）
"""


class MoodleDLException(Exception):
    """Moodle-DL 基础异常类"""

    pass


class MoodleNetworkError(MoodleDLException):
    """
    网络连接错误（可重试）

    包括：
    - 连接超时
    - DNS 解析失败
    - 连接中断
    - 服务器暂时不可用 (503)
    - 请求超时 (408)
    - 冲突 (409)
    - 请求过多 (429)
    """

    pass


class MoodleAPIError(MoodleDLException):
    """
    Moodle API 错误（通常不可重试）

    包括：
    - API 返回无效 JSON
    - API 返回错误响应
    - 资源不存在 (404)
    - 方法不允许 (405)
    - 服务器内部错误 (500)
    """

    pass


class MoodleAuthError(MoodleDLException):
    """
    认证失败（不可重试）

    包括：
    - Token 失效
    - 权限不足 (403)
    - 未授权 (401)
    - 需要登录
    """

    pass


class RequestRejectedError(MoodleAPIError):
    """
    请求被 Moodle 系统拒绝

    继承自 MoodleAPIError，保持向后兼容
    当 Moodle 系统返回错误响应时抛出此异常
    """

    pass
