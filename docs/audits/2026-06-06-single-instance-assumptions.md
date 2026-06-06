# 单实例假设 (Single-Instance Assumption) 审计报告

**项目**: Moodle-DL
**扫描路径**: /Users/linqilan/CodingProjects/moodle/Moodle-DL
**范围**: moodle_dl/{cli, config.py, database.py, main.py, downloader/, moodle/, notifications/, utils.py}
**测试基线**: 2226 个已通过
**日期**: 2026-06-06
**模式**: "两个 workspace (A, B) 同时跑" 时的语义冲突 / 数据竞争

---

## 概念模型

moodle-dl 通过 CLI 参数 `-p / --path` 决定 workspace 根目录 (默认 cwd)。
在该 workspace 下, config 位于 `config.json`, state DB 位于 `moodle_state.db`, 运行锁 `running.lock`, 日志 `MoodleDL.log`, responses 日志 `responses.log`。
**理论上两个 workspace 完全隔离, 但代码中残留了若干把 "workspace == 全局" 的假设。**

---

## 🔴 高严重性

### 1. `ProcessLock.lock()` 是 TOCTOU 竞态, 不是真锁

**位置**: `moodle_dl/utils.py:1109-1119`
```python
@staticmethod
def lock(dir_path: str):
    path = Path(dir_path) / 'running.lock'
    if Path(path).exists():           # ← check
        raise ProcessLock.LockError(...)
    Path(path).touch()                # ← use
```
**调用**: `moodle_dl/main.py:1053` `ProcessLock.lock(config.get_misc_files_path())`

**问题**:
1. **TOCTOU**: `exists()` 与 `touch()` 之间无原子性保证。两个进程几乎同时检测到不存在, 都会 `touch`, 锁失效。
2. **跨 workspace 共享目录**: `config.get_misc_files_path()` 默认回退到 `opts.path` (per-workspace), 但用户可能用 `--log-file-path` 把它指向同一个目录, 或在某些 wizard 流程里通过 `set_property('misc_files_path', ...)` 把多个 workspace 指向同一目录, 此时进程 A 持有 workspace-X 锁, 进程 B 在 workspace-Y 启动直接被拒。
3. **崩溃后残留**: `unlock()` 静默吞掉 `OSError` (utils.py:1127), 进程崩溃/Ctrl-C 后 `running.lock` 一直存在, 启动直接报 "A downloader is already running"。
4. **注释自己承认**: "Race conditions will occur!" (utils.py:1097-1098)。

**两个 workspace 同时跑的具体表现**:
- A,B 两进程 `cd /ws/A` 和 `cd /ws/B`, 若 `misc_files_path` 默认 = cwd, 实际锁定 `/ws/A/running.lock` 与 `/ws/B/running.lock`, 互相不阻塞 (OK, 这部分是对的)。
- 但若任一 workspace 通过 config 把 `misc_files_path` 改成共享目录 (例如 `~/.moodle-dl`), A 持锁期间 B 在自己的 workspace 也启动, 锁拒绝服务。
- 若两进程同时启动 (cron 撞车), 都过了 `exists()` 检查, 都 `touch()` 成功, 同步写 DB 时 SQLite WAL 兜底但锁语义已失, 还会出现 "process A 退出后删除 process B 的锁" 的互相 kill。

**严重性**: 高 — 锁不可信, 跨 workspace 配置偏移时直接导致拒绝服务。

**修复方向**:
- 改用 `fcntl.flock(fd, LOCK_EX | LOCK_NB)` 或 `filelock` 库 (注释里已经提到)。
- 文件名加入 PID 后缀, 防止崩溃残留 (`running.<pid>.lock`)。
- 在 `lock()` 内追加 `Path(path).write_text(os.getpid())` 便于排查。
- `unlock()` 前检查 PID 是否一致, 否则拒绝删除别人的锁。

---

### 2. `StateRecorder._query_cache` 跨 workspace 共享同一个 StateRecorder 实例

**位置**: `moodle_dl/database.py:123-134`
```python
def __init__(self, config: ConfigHelper, opts: MoodleDlOpts):
    self.opts = opts
    self.db_file = PT.make_path(config.get_misc_files_path(), 'moodle_state.db')
    # 🆕 查询缓存存储
    self._query_cache: Dict[str, tuple] = {}        # {cache_key: (data, timestamp)}
    self._cache_locks: Dict[str, bool] = {}         # 防止缓存击穿
```

**cache_key 构造**: `moodle_dl/database.py:1750-1762`
```python
def _get_cache_key(self, method_name: str, *args, **kwargs) -> str:
    key_parts = [method_name] + [str(arg) for arg in args] + [f"{k}={v}" for k, v in kwargs.items()]
    key_str = "|".join(key_parts)
    return f'{method_name}:{hashlib.md5(key_str.encode()).hexdigest()}'
```

**问题**:
- cache_key 不包含 `db_file` / workspace 路径, **只包含 `method_name` + args + kwargs**。
- `get_stored_files()` (database.py:687) 与 `get_old_files()` (database.py:703) 完全不传参数, 任何 workspace 调它们都返回相同的 cache_key。
- **TTL = 300s**, 在这 5 分钟内, 跨进程 / 跨 workspace 调用会读到陈旧数据。
- 更隐蔽: cache 的 `self._query_cache` 是 StateRecorder 实例属性, 进程内 OK; 但 `task.py:2410-2411` 在 **每个 task 完成后会新建 StateRecorder**:
  ```python
  config = ConfigHelper(self.opts.global_opts)
  database = StateRecorder(config, self.opts)
  database.mark_download_complete(self.file.file_id, dest_path)
  ```
  新实例的缓存是空的, 这是正确的; 但同一个进程里既有 `main.py:494` 的主 recorder 又有 task 内的小 recorder, 缓存状态不一致。
- 缓存命中时 **不会触发 `INCOMPLETE` 状态变更的传播**, 任务 A 修改了文件状态, 任务 B 仍在用旧 snapshot 做 diff。

**两个 workspace 同时跑的具体表现**:
- 进程 A 跑 workspace-A, 缓存 `get_stored_files` 的 5000 个 file rows。
- 5 分钟内切到 workspace-B, 由于 `db_file` 不同, A 进程读 B 的 DB 时用 A 的 cache_key, **返回 A 的旧数据** — 等于脏读, 但不报错, 用户根本不知道。

**严重性**: 高 — 缓存无 workspace 维度, 直接导致跨 workspace 数据错配; 也是 STALE-READ 隐患。

**修复方向**:
- `cache_key` 中加入 `self.db_file` 或 `id(self)`:
  ```python
  return f'{self.db_file}:{method_name}:{hashlib.md5(key_str.encode()).hexdigest()}'
  ```
- 或者把 `_query_cache` 做成 `LRU(maxsize=...)` + workspace 命名空间。
- 把 `task.py:2410` 的 "task 局部 StateRecorder" 改成复用主实例 (避免多 recorder 一致性分歧)。

---

### 3. `Task._get_cached_mozilla_cookie_jar()` 把 cookie jar 挂在共享 `self.opts` 上, 跨 task 共享

**位置**: `moodle_dl/downloader/task.py:193-206`
```python
def _get_cached_mozilla_cookie_jar(self):
    if self.opts.cookies_text is None:
        return None
    cache_key = '_moodle_dl_cookie_jar_cache'
    text_key = '_moodle_dl_cookie_jar_cache_text'
    if getattr(self.opts, text_key, None) != self.opts.cookies_text:
        cookie_jar = MoodleDLCookieJar(StringIO(self.opts.cookies_text))
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
        setattr(self.opts, text_key, self.opts.cookies_text)
        setattr(self.opts, cache_key, cookie_jar)
        setattr(self.opts, '_moodle_dl_cookie_jar_cache', None)   # ← bug: 把刚赋的 cache 立刻置 None
    return getattr(self.opts, cache_key)
```

**问题**:
1. **多 task 共用同一 `opts.cookies_text` 来源**: 看起来是为省解析开销, 但 ThreadPoolExecutor 中多个 task 同时调用, cookie jar 内部 `http.cookiejar.CookieJar` 线程不安全 (cpython 没问题, 但 `load` / `set_cookie` 调用之间会有 race)。
2. **自身 bug**: 第 204 行 `setattr(self.opts, '_moodle_dl_cookie_jar_cache', None)` 把 `cache_key` 拼写错了 (写成了带 cache_key 名字的字段, 但赋的值是 None), 实际是把 `_moodle_dl_cookie_jar_cache` 这个新建的 cache 又立刻清空。下次 task 进来会 `getattr(...)` 取 None, 触发重 load — 缓存失效。
3. 整段逻辑: 第 197 行定义 `cache_key = '_moodle_dl_cookie_jar_cache'`, 第 203 行 `setattr(self.opts, cache_key, cookie_jar)`, 第 204 行 `setattr(self.opts, '_moodle_dl_cookie_jar_cache', None)` 用的字面量与 `cache_key` 变量名相同 (因为变量值就是字面量), 所以这一行 **确实** 把刚赋值的 cache 立刻清空。

**两个 workspace 同时跑的具体表现**:
- 同一进程内 task 并发: 共享 `self.opts` 上的 jar, 第一个 task 写入后被自己第二行清空, 后续 task 全部重新 parse。
- 跨 workspace: 假设通过 `--global-opts` 复用 `opts` 传入, 第一个 workspace 的 cookies 文本被第二个 workspace 误用 (见 #4)。

**严重性**: 高 — 自带 bug + 跨 task race; 隐式共享, 难诊断。

**修复方向**:
- 干掉 cache_key 拼写, 把 `setattr(self.opts, '_moodle_dl_cookie_jar_cache', None)` 删掉, 它的目的应该是 aiohttp 缓存而非 mozilla jar 缓存。
- 把 cookie jar 缓存移到 `self` (Task 实例) 或用 `threading.local`, 不挂 `opts`。
- 若必须跨 task 共享, 外面包 `threading.Lock`。

---

### 4. `Task` 反复 `ConfigHelper(self.opts.global_opts) + StateRecorder(...)` 产生游离 recorder

**位置**: `moodle_dl/downloader/task.py:2408-2412`, `moodle_dl/database.py:2411`
```python
if self.file.file_id is not None:
    config = ConfigHelper(self.opts.global_opts)
    database = StateRecorder(config, self.opts)
    database.mark_download_complete(self.file.file_id, dest_path)
```

**问题**:
- 每次 task 结束都新建 `StateRecorder` 实例, 内部 `self._query_cache = {}` 是空的, 不会与主 recorder 互相污染缓存 (这点比 #2 好)。
- **但**: 重新 `ConfigHelper(...)` 会再次 `__init__` (config.py:202-220), 触发 `StateRecorder(self, opts)` 一次 (config.py:219), 校验所有表存在 — 在每个 task 结束都做一次, 浪费 I/O, 而且并发 task 同一时刻 N 个连接打 SQLite (WAL 模式可承受, 但高并发会堆积)。
- 如果 `self.opts.global_opts` 已经被另一个 workspace 改过, 这里的 `config` 拿到的是污染后的 view。

**两个 workspace 同时跑的具体表现**:
- workspace A 的主进程跑一半, 另一个进程 (cron / IDE) 用 `global_opts` 同步触发 task 完成逻辑, 数据落到错误的 DB。

**严重性**: 高 — task 内部绕过主架构, 创建短命 recorder, 容易出现 "主进程想着 A 数据库, task 内部想着 B 数据库"。

**修复方向**:
- 把 `config` 和 `database` 作为 `Task` 的构造参数传入, 复用主实例。
- 至少加一层 `if self._database is None: self._database = ...` 复用。

---

## 🟠 中严重性

### 5. SSO 调试文件写到固定 `/tmp/`, 跨 workspace 互相覆盖

**位置**:
- `moodle_dl/auto_sso_login.py:1069` `debug_path = '/tmp/moodle_login_uncertain.html'`
- `moodle_dl/auto_sso_login.py:1138` `screenshot_path = '/tmp/moodle_sso_login_failed.png'`
- `moodle_dl/auto_sso_login.py:1353` (test) `cookies_path='/tmp/test_cookies.txt'`
- `moodle_dl/moodle/mods/book.py:840` `debug_path = f'/tmp/playwright_course_page_{course_id}.html'`
- `moodle_dl/moodle/mods/book.py:842` `f.write(init_html)` (race 写到同一 path)
- `moodle_dl/moodle/mods/book.py:942` `f'/tmp/playwright_debug_{module_id}.html'`
- `moodle_dl/moodle/mods/book.py:1300` `tempfile.gettempdir() / 'print_book_debug.html'`

**问题**:
- 文件名不含 workspace 标识, 也不含 PID, 跨进程覆盖 (A 写到一半 B 抢着写)。
- `/tmp` 在多用户系统上是共享的, A 用户写到 `/tmp/moodle_sso_login_failed.png` 后 B 用户读到 B 的报错图, **隐私泄露 + 调试混乱**。
- `book.py:1300` 用了 `gettempdir()` 但文件名仍是固定的 `print_book_debug.html`, 同样问题。

**两个 workspace 同时跑的具体表现**:
- 同时调试两个 workspace 的 SSO, 看不出哪个截图属于哪个。
- `book.py:840` 中 `course_id` 是 Moodle 课程 ID, 不同 workspace 撞课程 ID 时也会覆盖。

**严重性**: 中 — 仅 debug 路径, 不影响主流程, 但调试体验差 + 跨用户风险。

**修复方向**:
- 改成 `PT.make_path(config.get_misc_files_path(), 'sso_debug_<pid>_<ts>.html')`。
- 测试代码里的 `/tmp/test_cookies.txt` 也应该放 `tmp_path` fixture。
- 调试截图加 `<workspace_basename>_<pid>_<ts>` 命名空间。

---

### 6. `RequestHelper.log_responses_to` 写到 `responses.log`, 跨进程覆盖

**位置**: `moodle_dl/moodle/request_helper.py:52-56`
```python
self.log_responses_to = None
if opts.log_responses:
    self.log_responses_to = PT.make_path(config.get_misc_files_path(), 'responses.log')
    with open(self.log_responses_to, 'w', encoding='utf-8') as response_log_file:
        response_log_file.write('JSON Log:\n\n')
```

**问题**:
- 文件名固定 `responses.log`, 路径来自 `config.get_misc_files_path()` (per-workspace, OK)。
- `'w'` 模式 **清空已有内容**, 同一 workspace 两个进程同时跑, 后启动的进程会把先启动的日志清掉。
- 写入 (request_helper.py:327) 用 `'a'` 追加, 但初始化用 `'w'` — 进程 A 启动, 写入几行, 进程 B 启动, 立刻清空, A 的写入失败 / 错位。
- 同样在 `request_helper.py:327` 的 `with open(self.log_responses_to, 'a', ...)` 没有 lock, 并发写会出现行交错 (虽然行内 JSON 完整, 但顺序不可信)。

**两个 workspace 同时跑的具体表现**:
- 同 workspace 并发 (例如 main + cli 子命令): 互相覆盖 + 顺序错乱。
- 跨 workspace: 默认路径已隔离, 不会冲突, 但若用户把 `misc_files_path` 配成共享, 立刻爆炸。

**严重性**: 中 — 只在 `--log-responses` 开启时触发, 但触发即丢数据。

**修复方向**:
- 文件名加 PID: `responses_<pid>.log`。
- 或者放弃清空策略, 改成 append-only。
- 加 `threading.Lock` 保护 `log_response()` 的写入。

---

### 7. `Response` log file 在 main.py 之外仍由 `RequestHelper` 反复打开 (TOCTOU)

**位置**: `moodle_dl/moodle/request_helper.py:325-332`
```python
def log_response(self, function: str, data: Dict[str, Any], url: str, json_result: Dict[str, Any]) -> None:
    if self.opts.log_responses and function not in ['tool_mobile_get_autologin_key']:
        with open(self.log_responses_to, 'a', encoding='utf-8') as response_log_file:
            response_log_file.write(f'URL: {url}\n')
            ...
```

**问题**:
- 每次 API 调用都 `open/close`, 高频场景下 (几百个并发) 大量系统调用。
- 跨 task 并发写入靠 OS 互斥, 行级别交错风险中等 (512 字节以下 POSIX append 一般原子, 但跨 4 KiB 块可能拆)。
- 没有任何 `os.O_APPEND` flag 显式声明, 依赖 `open(mode='a')` 的默认行为 (在多数 OS 上是 `O_APPEND`, 但不应依赖)。

**两个 workspace 同时跑**: 同 #6。

**严重性**: 中 — 数据完整性可接受, 但 `mode='a'` 默认行为跨平台不可靠, 跨 workspace 共享路径时必出乱。

**修复方向**:
- 整个 `log_responses_to` 句柄保存为 `self.log_file_handle` 在 `__init__` 打开, 在 `close()` / context manager 中关闭。
- 显式 `open(..., 'a', buffering=1)` (line-buffered) + 跨进程 fcntl 锁。

---

### 8. `MoodleDLCookieJar` 写盘无锁, 跨 task 同一 cookie 文件竞争

**位置**: `moodle_dl/moodle/request_helper.py:97-101`
```python
if cookie_jar_path is not None:
    for cookie in session.cookies:
        cookie.expires = 2147483647
    session.cookies.save(ignore_discard=True, ignore_expires=True)   # ← 多 task 串写
```

**问题**:
- `cookie_jar.save()` 写整个文件 (truncate + write), **多 task 并发写同一 cookie 文件会产生交错 / 文件截断**。
- 虽然 task.py 用了 `_clone_mozilla_cookie_jar` (line 209) 先克隆, 但 `request_helper.post_URL` (line 86) 是用 `MoodleDLCookieJar(cookie_jar_path)` 重新 load, 然后 `save()` 全量覆盖。
- 跨 task 并发: A 在 `save()` 还没 flush, B 开始 `save()`, A 写到一半的内容会被 B 覆盖丢失。

**两个 workspace 同时跑**:
- 同 workspace 多 task: cookie 文件被截断 / 错乱。
- 跨 workspace 但 `cookies_path` 共享 (例如 `~/.moodle-dl/cookies.txt`): A 写完被 B 写覆盖, 认证状态错位。

**严重性**: 中 — 间歇性故障, 难复现。

**修复方向**:
- 写 cookie 前后加 `fcntl.flock(LOCK_EX)`。
- 改用 `tempfile` 原子写: 写到 `.tmp`, `os.replace()`。
- 把 cookie 改成只在 DB 存储 (cookie_handler.py:23-25 已经在做, 但 request_helper 还在写文件, 路径不一致)。

---

## 🟡 低严重性

### 9. `_query_cache` 无上限 + 无锁, 长时间运行内存膨胀

**位置**: `moodle_dl/database.py:1791-1807`
```python
def _clear_cache(self, pattern: Optional[str] = None):
    if pattern is None:
        self._query_cache.clear()
```

**问题**:
- `_query_cache` 永不清空 (除非显式 `_clear_cache`), 任务多时内存泄漏。
- `_cache_locks: Dict[str, bool] = {}` 标注 "防止缓存击穿" 但 `self._get_cached()` 中 **没有任何 `_cache_locks` 的使用代码** (grep 验证: 无 `_cache_locks[...] = True`)。注释是空头支票。
- 同一个 `StateRecorder` 实例跨多个 workspace 复用时 (例如测试 fixture), cache 不会清空, 上一个 workspace 的 `_query_cache` 内容泄漏到下一个。

**严重性**: 低 — 单次进程影响有限, 但 "防止缓存击穿" 注释与代码脱节, 死代码。

**修复方向**:
- 实施 `_cache_locks` 的双重检查 (Double-Checked Locking), 否则删掉。
- 用 `cachetools.LRUCache(maxsize=256)`。
- 显式 `if len(self._query_cache) > N: self._query_cache.clear()`。

---

### 10. `ConfigHelper` 加载时副作用 (StateRecorder 隐式初始化)

**位置**: `moodle_dl/config.py:202-220`
```python
def __init__(self, opts: MoodleDlOpts):
    ...
    self._db_file = str(Path(opts.path) / 'moodle_state.db')
    try:
        from moodle_dl.database import StateRecorder
        StateRecorder(self, opts)
    except Exception as e:
        raise RuntimeError(...)
```

**问题**:
- 每次 `ConfigHelper(opts)` 都 **强制打开 SQLite**, 校验所有表存在, 任何 SQLite 故障立刻抛 RuntimeError, 把 CLI 启动都堵死。
- 在 `task.py:2410` 每次 task 完成都 `ConfigHelper(self.opts.global_opts) + StateRecorder(...)`, 实际上是 *两次* 连接 (config.py:219 + task.py:2411)。
- 若两个 workspace 用同一个 `global_opts` 复用 opts, 但又通过 wizard 在中途改了 `opts.path` → config.py 旧 `_db_file` 残留。

**两个 workspace 同时跑的具体表现**:
- 大量 task 期间: SQLite 连接风暴。
- 跨 workspace 复用 `opts` 时: `_db_file` 字符串拼接用的是 init 时的 `opts.path`, 中途改路径不生效。

**严重性**: 低 — 不直接破坏, 但每次新建 ConfigHelper 都有副作用, 难用于只读 / 视图场景。

**修复方向**:
- 把 "init 校验" 拆成单独的 `ConfigHelper.ensure_database_ready()` 方法, 默认不在 `__init__` 调用。
- `__init__` 只做 property 加载, 任何需要 DB 的方法再 lazy-init。

---

## Top 3 应该改的

### 🥇 1. `ProcessLock` 改成 fcntl 真锁 + PID 文件 (修复 #1)
**理由**: 锁语义的失守会直接导致 workspace 互拒 / 死锁残留 / 静默双跑。代码注释自己已经说 "consider using fcntl.flock()", 改起来 30 行内, ROI 最高。

### 🥈 2. `StateRecorder._query_cache` 加入 workspace 维度 (修复 #2)
**理由**: 缓存键缺失 db_file 是直接的数据错配源, TTL 5 分钟意味着污染窗口长。一旦切 workspace 而忘记 `clear_cache`, 用户拿到的是别 workspace 的陈旧文件列表, 难以察觉。

### 🥉 3. `Task` 不再内部 `ConfigHelper + StateRecorder` (修复 #4)
**理由**: 每次 task 完成都做两次 SQLite 连接 + 全表校验, 而且在多 workspace 场景下会拿到错的 database。改成主 recorder 复用既减少 I/O, 又消除跨 workspace 状态不一致。

---

## 附录: 扫描方法

1. 关键词 `running.lock | fcntl | flock | FileLock | .lock` 扫进程锁相关。
2. 关键词 `/tmp/ | /var/ | ~/.moodle-dl | tempfile | gettempdir` 扫硬编码路径。
3. 关键词 `_query_cache | class-level cache | module-level state` 扫全局缓存。
4. 逐文件读 `utils.py::ProcessLock`, `database.py::StateRecorder.__init__ / _get_cache_key`, `task.py::_get_cached_mozilla_cookie_jar`, `request_helper.py::log_response`, `auto_sso_login.py` 调试路径, `book.py` 调试路径。
5. 验证 `config.py::get_misc_files_path` 默认 = `opts.path` (per-workspace), `config.py:866, 870`, 故 lock / DB 默认 per-workspace; 但 cookie 路径 (`cookies_path` 由 wizard 决定) 不一定。

**未发现但需注意**:
- `ProgressTracker` (downloader/progress_tracker.py) 全部实例属性, 跨 task 不共享, 安全。
- `NotificationService` 系列 (notifications/*) 全部构造时接受 config, 实例级状态, 安全。
- `auth_session_manager.py` 无 module-level 全局变量, 安全。
- `ip_validator.py` 的 `IP_DETECTION_SERVICES` 是类常量只读, 安全。
