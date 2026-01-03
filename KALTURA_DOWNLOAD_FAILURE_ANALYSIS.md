# Kaltura 视频部分无法下载问题分析报告

## 问题现象
用户报告：部分 Kaltura 视频无法下载，但大部分可以成功下载

## 根本原因分析

### 1️⃣ Kaltura 平台的多种保护机制

根据网络搜索结果，Kaltura 视频无法下载的常见原因包括：

#### A. **DRM（数字版权管理）保护**
- 某些视频启用了 DRM 技术防止未经授权的下载
- 这会导致 yt-dlp 的 KalturaIE 提取器无法获取视频内容
- **症状**：部分视频成功提取 URL 但 yt-dlp 下载时失败

#### B. **视频格式和流媒体协议差异**
Kaltura 使用多种传输协议：
- **HLS (HTTP Live Streaming)** - 将视频分割成小片段
- **DASH (Dynamic Adaptive Streaming over HTTP)** - 自适应流媒体
- 部分视频可能使用不标准的编码或变体
- **症状**：某些特定的视频格式无法被 yt-dlp 处理

#### C. **服务器限制和访问权限**
- 某些视频设置了特定的访问限制
- 需要特定的 Cookie、Token 或认证信息
- Kaltura CDN 地址可能在不同地区有差异
- **症状**：无法连接到 Kaltura CDN 或获取视频元数据

### 2️⃣ Moodle-DL 代码中的潜在问题

#### 问题 2.1: Kaltura URL 提取失败

**文件**: `moodle_dl/downloader/task.py` (行 960-1066)

```python
# 提取 browseandembed URL - 可能失败的点 1
target_uri_match = re.search(r'name="target_link_uri"\s+value="([^"]+)"', lti_html)
if not target_uri_match:
    logging.warning('[%d] Could not find target_link_uri in lti_launch page', self.task_id)
    return None

# 提取 entry ID - 可能失败的点 2
entry_id_match = re.search(r'/entryid/([^/]+)/', browseandembed_url)
if not entry_id_match:
    logging.warning('[%d] Could not extract entry ID from browseandembed URL', self.task_id)
    return None

# 提取 partner ID - 可能失败的点 3
partner_id_match = re.search(r'partnerId\D*?(\d{6,})', browseandembed_response.text, re.MULTILINE)
if not partner_id_match:
    logging.warning('[%d] Could not extract partner ID from browseandembed page', self.task_id)
    return None
```

**可能的原因**：
- 页面结构变化导致正则表达式无法匹配
- 某些 Kaltura 实例使用不同的 HTML 结构
- JavaScript 动态生成的内容无法通过正则提取

#### 问题 2.2: CDN 地址变化

**文件**: `moodle_dl/downloader/task.py` (行 1051-1057)

```python
# 尝试从页面提取 Kaltura CDN
kaltura_cdn_match = re.search(r'["\']?https?://([a-z0-9\-.]+\.kaltura\.[a-z]{2,})', html_content)
if kaltura_cdn_match:
    kaltura_cdn = kaltura_cdn_match.group(1)
    logging.debug('[%d] Found Kaltura CDN from page: %s', self.task_id, kaltura_cdn)
else:
    # Fallback 到默认 CDN
    kaltura_cdn = 'cdnapisec.kaltura.com'
    logging.debug('[%d] Using default Kaltura CDN: %s', self.task_id, kaltura_cdn)
```

**问题**：
- 使用的默认 CDN `cdnapisec.kaltura.com` 可能不适用于所有 Kaltura 实例
- 某些大学或机构使用自定义 CDN 地址
- 如果无法从页面提取 CDN，使用通用 CDN 可能无法访问

#### 问题 2.3: yt-dlp 处理失败

**文件**: `moodle_dl/downloader/task.py` (行 549-561)

```python
except Exception as yt_err:
    logging.error('[%d] yt-dlp failed! Error: %s', self.task_id, yt_err)
    self.status.yt_dlp_failed_with_error = True

if self.status.yt_dlp_failed_with_error and not self.opts.global_opts.ignore_ytdl_errors:
    if not delete_if_successful:
        PT.remove_file(self.file.saved_to)
    raise RuntimeError(
        'yt-dlp 无法下载该 URL。'
        + '你可以通过运行 `moodle-dl --ignore-ytdl-errors` 一次来忽略此错误。'
    )
```

**问题**：
- yt-dlp 的 KalturaIE 提取器可能无法处理某些 DRM 保护的视频
- 某些 Kaltura 版本使用 yt-dlp 不支持的 API
- 未提供足够的诊断信息（只有 "无法下载"）

### 3️⃣ 具体失败场景

#### 场景 1: 多重认证要求
某些大学的 Kaltura 实例可能需要：
1. Moodle 认证
2. LTI 认证
3. Kaltura 账户认证

如果其中任何一个失败，视频就无法下载。

#### 场景 2: 地理限制或 IP 限制
某些视频可能只允许特定 IP 地址或地理位置访问。

#### 场景 3: 版本不兼容
不同大学的 Moodle 和 Kaltura 版本不同，可能导致：
- 页面结构不同
- API 端点不同
- 需要的参数不同

#### 场景 4: 特殊编码格式
某些视频可能使用：
- VP9 编码（而非常见的 H.264）
- 特殊的分辨率或帧率
- 自定义的 Kaltura 播放器配置

## 解决方案建议

### 🔧 短期修复

#### 1. 改进 CDN 检测
```python
# 添加更多 CDN 地址备选
KALTURA_CDN_FALLBACKS = [
    'cdnapisec.kaltura.com',
    'cdnbakmi.kaltura.com',
    'cdnakmi.kaltura.com',
    'cdnapi.kaltura.com',
]

# 尝试每个 CDN，找到可用的
for cdn in KALTURA_CDN_FALLBACKS:
    try:
        # 尝试连接 CDN
        response = requests.head(f'https://{cdn}/ping', timeout=5)
        if response.status_code < 400:
            kaltura_cdn = cdn
            break
    except:
        continue
```

#### 2. 改进错误诊断
```python
except Exception as yt_err:
    error_msg = str(yt_err)
    logging.error('[%d] yt-dlp failed with detailed error: %s', self.task_id, error_msg)
    
    # 诊断常见错误
    if 'DRM' in error_msg or 'protected' in error_msg:
        logging.warning('[%d] Video appears to be DRM protected', self.task_id)
    elif '404' in error_msg or 'Not Found' in error_msg:
        logging.warning('[%d] Kaltura content not found on CDN', self.task_id)
    elif 'Timeout' in error_msg:
        logging.warning('[%d] Connection timeout to Kaltura CDN', self.task_id)
```

#### 3. 添加重试机制
```python
# 对不同 CDN 地址进行多次重试
for retry in range(3):
    for cdn in KALTURA_CDN_FALLBACKS:
        try:
            # 尝试下载
            result = ydl.extract_info(kaltura_url, download=False)
            if result:
                return True
        except:
            continue
        
        # 清理 yt-dlp 缓存
        ydl.cookiejar.clear()
```

### 📊 中期改进

#### 1. 记录详细的诊断信息
```python
# 添加到数据库
failed_video_info = {
    'entry_id': entry_id,
    'partner_id': partner_id,
    'kaltura_url': kaltura_url,
    'error_type': detect_error_type(error),
    'kaltura_cdn': kaltura_cdn,
    'user_agent': self.RQ_HEADER['User-Agent'],
    'timestamp': datetime.now().isoformat()
}
```

#### 2. 实现 Kaltura API 直接下载
```python
# 绕过 yt-dlp，直接从 Kaltura API 获取视频信息
kaltura_api_url = f'https://{kaltura_cdn}/api_v3/service/media/action/get'
params = {
    'format': '1',
    'clientTag': 'kdp:v2.72.2',
    'apiVersion': '3.3.0',
    'expiry': '86400',
    'ks': get_kaltura_session_token(),
    'entryId': entry_id
}
```

#### 3. 支持多个 Kaltura 提取器
```python
# 当主提取器失败时，尝试备选方案
extractors = [
    KalturaIE,
    KalvidresLtiIE,
    KalvidresEmbeddedIE,
]

for extractor in extractors:
    try:
        result = extractor.extract(kaltura_url)
        if result:
            return result
    except:
        continue
```

### 🔬 长期研究

#### 1. 建立 Kaltura 兼容性数据库
记录不同大学的 Kaltura 版本和配置：
- 访问 `/media_gallery_static/kmc-root.html` 获取版本信息
- 测试不同 CDN 的可达性
- 记录成功和失败的视频格式

#### 2. 实现智能重试策略
根据失败类型选择重试策略：
- DRM 保护 → 放弃（无法绕过）
- 网络错误 → 指数退避重试
- 认证错误 → 刷新 Token 后重试
- CDN 错误 → 切换 CDN 后重试

#### 3. 贡献给 yt-dlp
提交 Kaltura 提取器改进和 BUG 报告到 yt-dlp 项目。

## 使用者建议

### 对当前问题的应对

1. **检查日志**：查看 `moodle-dl` 的详细日志（`-v` 或 `--verbose` 选项）
   ```bash
   moodle-dl -v 2>&1 | grep -i kaltura
   ```

2. **识别失败模式**：
   - 失败的视频是否来自同一个教师/课程？
   - 是否特定的视频大小或分辨率容易失败？
   - 是否在特定的网络环境下失败？

3. **重新尝试**：
   ```bash
   # 重试失败的下载
   moodle-dl --retry-failed
   
   # 使用不同的网络或等待几小时后重试
   # （某些 CDN 问题可能是临时的）
   ```

4. **检查 Cookie**：
   ```bash
   # 刷新 Cookie 可能解决认证问题
   moodle-dl --refresh-cookies
   moodle-dl --retry-failed
   ```

### 对开发者的建议

如果想改进 Kaltura 视频下载成功率：

1. **提供失败日志**：运行 `moodle-dl -v` 并保存输出
2. **报告具体信息**：
   - 哪些视频无法下载
   - 是否出现特定的错误信息
   - 网络环境信息
3. **参与开发**：可以帮助改进 Kaltura 支持

## 总结

Kaltura 视频部分无法下载的主要原因：

| 原因 | 概率 | 可修复性 |
|------|------|---------|
| DRM 保护 | 🔴 高 | ❌ 无法修复 |
| 正则表达式过时 | 🟡 中 | ✅ 易修复 |
| CDN 地址不适配 | 🟡 中 | ✅ 易修复 |
| yt-dlp 版本不兼容 | 🟡 中 | ✅ 中等难度 |
| 网络连接问题 | 🟢 低 | ✅ 重试即可 |
| 认证过期 | 🟢 低 | ✅ 刷新 Cookie |

**建议优先级**：
1. 改进 CDN 检测和重试机制
2. 添加更详细的错误诊断
3. 升级 yt-dlp 依赖
4. 在社区收集更多失败案例

---

**更新日期**: 2026-01-03  
**基于**: yt-dlp Kaltura 提取器分析、网络搜索结果、代码审查

