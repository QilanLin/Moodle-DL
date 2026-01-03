# Semgrep 安全扫描报告

**扫描日期**: 2026-01-03
**项目**: Moodle-DL
**扫描范围**: 95 个 Python 文件
**规则运行**: 291 条规则
**发现问题**: 10 个

---

## 执行摘要

本次安全扫描使用 Semgrep 对 Moodle-DL 项目进行了全面的安全审计，重点关注认证、网络请求、数据库操作和文件处理等安全敏感区域。

### 问题统计

| 严重程度 | 数量 | 说明 |
|---------|------|------|
| **P0 - 严重** | 4 | SQL 注入风险 |
| **P1 - 高危** | 5 | 弱加密、动态 URL、SSL 配置 |
| **P2 - 中危** | 1 | SSL 加密套件配置 |
| **总计** | 10 | - |

### 扫描覆盖范围

✅ **认证和 Cookie 管理** (5 个文件) - 无问题
✅ **网络和数据处理** (4 个文件) - 1 个问题
⚠️  **数据库和配置管理** (4 个文件) - 4 个问题
⚠️  **全项目扫描** (95 个文件) - 10 个问题
✅ **Moodle 模块处理器** (28 个文件) - 无问题
✅ **内容提取器** (10 个文件) - 无问题

---

## 详细问题列表

### 🔴 P0 - 严重问题（需立即修复）

#### 1. SQL 注入风险 (4 个实例)

**文件**: `moodle_dl/database.py`
**规则**: `python.lang.security.audit.formatted-sql-query.formatted-sql-query`
**规则**: `python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query`

**问题描述**:
使用格式化字符串构建 SQL 查询，可能导致 SQL 注入攻击。攻击者可以通过控制 `table_name` 或 `index_name` 参数来执行恶意 SQL 代码。

**位置 1**:
```python
文件: moodle_dl/database.py:135
代码: c.execute(f'DROP TABLE IF EXISTS {table_name};')
```

**位置 2**:
```python
文件: moodle_dl/database.py:152
代码: c.execute(f'DROP INDEX IF EXISTS {index_name};')
```

**修复建议**:
1. **最佳方案**: 使用参数化查询
   ```python
   # 对于表名和索引名，需要使用白名单验证
   allowed_tables = ['table1', 'table2', 'table3']
   if table_name not in allowed_tables:
       raise ValueError(f"Invalid table name: {table_name}")
   c.execute(f'DROP TABLE IF EXISTS {table_name};')
   ```

2. **替代方案**: 使用严格的输入验证
   ```python
   import re
   if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
       raise ValueError(f"Invalid table name format: {table_name}")
   ```

**风险等级**: 🔴 **P0 - 严重**
- 可能导致数据库被攻击
- 可能造成数据泄露或数据丢失
- 需要立即修复

---

### 🟠 P1 - 高危问题（应尽快修复）

#### 2. 动态 URL 使用 urllib (1 个实例)

**文件**: `moodle_dl/downloader/task.py`
**规则**: `python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected`

**问题描述**:
使用动态值调用 `urllib.request.urlopen()`。urllib 支持 `file://` 协议，如果 URL 被恶意控制，攻击者可能读取服务器上的任意文件。

**位置**:
```python
文件: moodle_dl/downloader/task.py:857
代码: with urllib.request.urlopen(url_to_download) as response:
```

**修复建议**:
1. **验证 URL 格式**:
   ```python
   from urllib.parse import urlparse

   parsed_url = urlparse(url_to_download)
   if parsed_url.scheme not in ['http', 'https']:
       raise ValueError(f"Unsupported URL scheme: {parsed_url.scheme}")

   # 验证域名是否在允许列表中
   allowed_domains = ['trusted-domain.com', 'another-trusted.com']
   if parsed_url.netloc not in allowed_domains:
       raise ValueError(f"Unauthorized domain: {parsed_url.netloc}")

   with urllib.request.urlopen(url_to_download) as response:
       ...
   ```

2. **使用 requests 库**（推荐）:
   ```python
   import requests

   response = requests.get(url_to_download, timeout=30)
   # requests 库默认更安全，且不支持 file:// 协议
   ```

**风险等级**: 🟠 **P1 - 高危**
- 可能导致任意文件读取
- 可能泄露敏感配置文件
- 建议尽快修复

---

#### 3. 弱哈希算法 - SHA1 (3 个实例)

**文件**: `moodle_dl/moodle/result_builder.py`
**规则**: `python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1`

**问题描述**:
使用 SHA1 哈希算法，该算法已被证明不安全，不具备抗碰撞性，不适合用于加密签名或数据完整性验证。

**位置 1**:
```python
文件: moodle_dl/moodle/result_builder.py:483
代码: m = hashlib.sha1()
```

**位置 2**:
```python
文件: moodle_dl/moodle/result_builder.py:646
代码: m = hashlib.sha1()
```

**位置 3**:
```python
文件: moodle_dl/moodle/result_builder.py:749
代码: m = hashlib.sha1()
```

**修复建议**:
使用 SHA256 或 SHA3 替代 SHA1：

```python
# 替换前
m = hashlib.sha1()

# 替换后
m = hashlib.sha256()
```

**注意**: 如果这些哈希用于文件缓存或数据指纹，升级到 SHA256 后需要清空现有缓存，因为哈希值会改变。

**风险等级**: 🟠 **P1 - 高危**
- SHA1 已被证明存在碰撞攻击
- 不适合安全敏感场景
- 建议尽快修复

---

#### 4. 未验证的 SSL 上下文 (1 个实例)

**文件**: `moodle_dl/utils.py`
**规则**: `python.lang.security.unverified-ssl-context.unverified-ssl-context`

**问题描述**:
使用 `ssl._create_unverified_context()` 创建未验证 SSL 证书的上下文，这将允许不安全的连接而不验证 SSL 证书，容易遭受中间人攻击。

**位置**:
```python
文件: moodle_dl/utils.py:963
代码: ssl_context = ssl._create_unverified_context()  # pylint: disable=protected-access
```

**修复建议**:
使用默认的 SSL 上下文，它会正确验证证书：

```python
# 替换前
ssl_context = ssl._create_unverified_context()

# 替换后
ssl_context = ssl.create_default_context()

# 如果需要自定义证书验证
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = True
ssl_context.verify_mode = ssl.CERT_REQUIRED
```

**如果确实需要跳过验证**（仅用于测试环境）:
```python
import warnings
warnings.warn("Using unverified SSL context - only for testing!", SecurityWarning)
ssl_context = ssl._create_unverified_context()
```

**风险等级**: 🟠 **P1 - 高危**
- 容易遭受中间人攻击
- 可能泄露敏感数据
- 建议尽快修复

---

### 🟡 P2 - 中危问题（建议修复）

#### 5. SSL 加密套件配置 (1 个实例)

**文件**: `moodle_dl/utils.py`
**规则**: `python.lang.security.audit.insecure-transport.ssl.no-set-ciphers.no-set-ciphers`

**问题描述**:
使用 `set_ciphers('ALL')` 设置了所有加密套件，包括不安全的弱加密套件。Python 的 ssl 模块默认会禁用不安全的加密套件，手动设置可能会降低安全性。

**位置**:
```python
文件: moodle_dl/utils.py:972
代码: ssl_context.set_ciphers('ALL')
```

**修复建议**:
删除此行，让 Python 使用默认的安全加密套件：

```python
# 删除这行
# ssl_context.set_ciphers('ALL')

# 或者使用更安全的配置
ssl_context.set_ciphers('DEFAULT')  # 使用默认安全套件
```

**风险等级**: 🟡 **P2 - 中危**
- 可能降低 SSL/TLS 连接的安全性
- 可能使用已被证明不安全的加密算法
- 建议修复

---

## 修复优先级清单

### 立即修复（P0）

1. ✅ **修复 SQL 注入风险** - `moodle_dl/database.py`
   - 行 135, 152
   - 添加白名单验证或严格的输入验证
   - 测试所有数据库操作

### 尽快修复（P1）

2. ✅ **修复动态 URL 问题** - `moodle_dl/downloader/task.py`
   - 行 857
   - 添加 URL 格式和域名验证
   - 考虑迁移到 requests 库

3. ✅ **升级哈希算法** - `moodle_dl/moodle/result_builder.py`
   - 行 483, 646, 749
   - 替换 SHA1 为 SHA256
   - 注意：需要清空现有缓存

4. ✅ **修复 SSL 上下文** - `moodle_dl/utils.py`
   - 行 963
   - 使用 `ssl.create_default_context()`
   - 确保证书验证已启用

### 建议修复（P2）

5. ✅ **优化 SSL 加密套件** - `moodle_dl/utils.py`
   - 行 972
   - 删除 `set_ciphers('ALL')`
   - 使用默认安全配置

---

## 代码示例

### SQL 注入修复示例

```python
# moodle_dl/database.py

# ❌ 不安全的代码
c.execute(f'DROP TABLE IF EXISTS {table_name};')
c.execute(f'DROP INDEX IF EXISTS {index_name};')

# ✅ 安全的代码（白名单验证）
ALLOWED_TABLES = {'files', 'courses', 'tokens'}
ALLOWED_INDICES = {'idx_files_url', 'idx_courses_id'}

def drop_table_safely(cursor, table_name):
    """安全地删除表"""
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"Table name not in whitelist: {table_name}")

    # 额外的格式验证
    import re
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
        raise ValueError(f"Invalid table name format: {table_name}")

    cursor.execute(f'DROP TABLE IF EXISTS {table_name};')
```

### URL 验证修复示例

```python
# moodle_dl/downloader/task.py

# ❌ 不安全的代码
with urllib.request.urlopen(url_to_download) as response:
    ...

# ✅ 安全的代码
def download_file_safely(url):
    """安全地下载文件"""
    from urllib.parse import urlparse

    parsed = urlparse(url)

    # 验证协议
    if parsed.scheme not in ['http', 'https']:
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")

    # 验证域名（根据实际情况调整）
    allowed_domains = {
        'moodle.example.com',
        'cdn.example.com'
    }
    if parsed.netloc not in allowed_domains:
        raise ValueError(f"Unauthorized domain: {parsed.netloc}")

    # 使用 requests（更安全）
    import requests
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.content
```

### SSL 上下文修复示例

```python
# moodle_dl/utils.py

# ❌ 不安全的代码
ssl_context = ssl._create_unverified_context()
ssl_context.set_ciphers('ALL')

# ✅ 安全的代码
def create_ssl_context(verify=True):
    """创建 SSL 上下文"""
    context = ssl.create_default_context()

    if not verify:
        # 仅用于测试环境
        import warnings
        warnings.warn("SSL verification disabled - only for testing!")
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    return context

ssl_context = create_ssl_context(verify=True)
```

---

## 验证和测试

### 修复后验证步骤

1. **SQL 注入修复验证**
   ```bash
   # 运行数据库操作测试
   python -m pytest tests/test_database.py -v
   # 尝试传入恶意表名
   python -c "from moodle_dl.database import Database; db = Database(); db.drop_table('\"; DROP TABLE files; --')"
   ```

2. **URL 验证修复验证**
   ```bash
   # 测试 URL 验证
   python -c "from moodle_dl.downloader.task import download_file_safely; download_file_safely('file:///etc/passwd')"
   # 应该抛出 ValueError: Unsupported URL scheme: file
   ```

3. **哈希算法升级验证**
   ```bash
   # 清空现有缓存
   rm -rf ~/.moodle-dl/cache/
   # 重新运行下载，确保使用新的哈希算法
   moodle-dl
   ```

4. **SSL 上下文修复验证**
   ```bash
   # 测试 SSL 连接
   python -c "import ssl; ctx = ssl.create_default_context(); print(ctx.check_hostname)"
   # 应该输出: True
   ```

---

## 安全建议

### 短期改进（1-2 周）

1. ✅ 修复所有 P0 级别问题（SQL 注入）
2. ✅ 修复所有 P1 级别问题（加密、URL、SSL）
3. ✅ 添加输入验证单元测试
4. ✅ 更新文档说明安全最佳实践

### 中期改进（1-2 月）

1. 定期执行安全扫描（每月一次）
2. 集成 Semgrep 到 CI/CD 流程
3. 添加依赖项安全扫描（pip-audit）
4. 实施安全代码审查流程

### 长期改进（3-6 月）

1. 实施 Security Headers
2. 添加运行时应用自我保护（RASP）
3. 进行渗透测试
4. 建立安全漏洞披露流程

---

## 工具和资源

### 推荐工具

- **Semgrep**: `https://semgrep.dev/` - 代码安全扫描
- **pip-audit**: `pip install pip-audit` - 依赖项安全审计
- **Bandit**: `pip install bandit` - Python 安全检查
- **Safety**: `pip install safety` - 依赖项漏洞扫描

### 参考资料

- [OWASP Python Security](https://owasp.org/www-project-python-security/)
- [CWE-89: SQL Injection](https://cwe.mitre.org/data/definitions/89.html)
- [CWE-319: Cleartext Transmission](https://cwe.mitre.org/data/definitions/319.html)
- [Python ssl Documentation](https://docs.python.org/3/library/ssl.html)

---

## 结论

本次 Semgrep 安全扫描发现了 **10 个安全问题**，其中：
- **4 个 P0 级别**（严重）- 需要立即修复
- **5 个 P1 级别**（高危）- 应尽快修复
- **1 个 P2 级别**（中危）- 建议修复

**好消息**：
- 认证和 Cookie 管理模块没有安全问题
- 35+ Moodle 模块处理器安全良好
- 内容提取器没有安全风险

**需要关注**：
- 数据库操作存在 SQL 注入风险
- 网络请求需要 URL 验证
- 加密算法需要升级
- SSL 配置需要加强

建议优先修复 P0 和 P1 级别的问题，以显著提升项目的安全性。

---

**报告生成时间**: 2026-01-03
**扫描工具**: Semgrep 1.146.0
**规则集**: auto (1063 rules)
