# 配置验证框架 - 快速参考

## 🚀 快速开始

### 命令行验证

```bash
# 验证配置
python validate_config.py

# 自动修复
python validate_config.py --auto-fix

# JSON 输出
python validate_config.py --json
```

### 代码中使用

```python
from moodle_dl.config_validator import validate_config_file

result = validate_config_file('config.json')
if result.is_valid:
    print('✅ 配置有效')
else:
    print(result.get_summary())
```

---

## 📋 验证层次

| 层次 | 检查内容 |
|------|---------|
| 1️⃣ 结构 | 必需字段、未知字段 |
| 2️⃣ 类型 | 字符串、整数、列表、布尔 |
| 3️⃣ 范围 | 域名格式、Token 长度 |
| 4️⃣ 逻辑 | 课程 ID 冲突、通知配置 |
| 5️⃣ 安全 | 路径安全、敏感信息 |

---

## ⚠️ 常见错误

| 错误 | 原因 | 修复 |
|------|------|------|
| 域名包含协议 | `https://moodle.com` | 使用 `moodle.com` |
| 路径缺少 `/` | `moodle` | 使用 `/moodle` |
| 课程 ID 冲突 | 同时在下载和排除列表 | 从一个列表移除 |
| 类型错误 | `"123"` 而非 `123` | 使用正确类型 |
| 通知配置不完整 | 缺少必需字段 | 补充所需字段 |

---

## 🔧 自动修复

支持修复的问题：
- ✅ 移除域名中的协议
- ✅ 添加路径前导斜杠
- ✅ 解决课程 ID 冲突
- ✅ 修复逻辑不一致
- ✅ 转换类型错误

---

## 📚 更多信息

详见: [CONFIG_VALIDATION_FRAMEWORK.md](CONFIG_VALIDATION_FRAMEWORK.md)

