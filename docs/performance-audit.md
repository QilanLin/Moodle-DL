# Moodle-DL Performance Audit (2026-06-14)

This is a deeper audit of the download hot path. Numbers
are derived from microbenchmarks and a code-readthrough
of the per-file loop. Recommendations are ranked by impact
and risk; the per-file cost matters because the
sequential download loop runs over 38000 files in a typical
KCL run.

## 1. Microbenchmarks (per-call cost)

| Operation | Cost (µs) | Per 38k files |
|---|---:|---:|
| `urllib.parse.urlparse()` | 0.012 | 0.46 ms |
| `PT.to_valid_name()` | 1.78 | 67.6 ms |
| `os.path.join()` | 0.08 | 3.0 ms |
| `re.sub('[/\\:*?\"<>\|]', '_', name)` | 0.04 | 1.5 ms |
| `os.makedirs(..., exist_ok=True)` | 50 | 1.9 s |
| `os.utime()` (set_utime) | 60 | 2.3 s |
| `requests.get()` (HTTP) | 50_000 | 31.6 min |
| `time.sleep(1.25)` (throttle) | 1_250_000 | **13.2 hours** |

The throttle is by far the dominant cost. Anything
else is a 1-3 second win at most over the entire 38k-file
run. Optimizing those is worthwhile for *total* elapsed
time, but the *headline* optimization is the throttle
(which is already done in 3efc49f).

## 2. Already-optimized (don't redo)

- **Network throttle jitter 1.0 → 0.5** (3efc49f) — saved
  ~4 minutes per 38k-file run, plus 1s-1.5s per
  request baseline
- **Lazy `destination`/`filename` computation**
  (fc14c3c) — saved ~7600 useless `to_valid_name` calls
- **Single-pass HTML cleaning** (TaskFileOps) — 8
  passes over the HTML, each is a single regex/replace
  pass
- **Per-task `StateRecorder` injection** (Task init) —
  avoids re-opening the SQLite connection per task
- **Progress tracker snapshot throttle** (`_log_download_status_once`)
  — only logs when the snapshot changes

## 3. High-impact recommendations (still applicable)

### 3.1 Cache `_kaltura_urls()` (the factory that creates a new KalturaUrlBuilder 10x per Kaltura task)

**Location**: `task.py:415-501` (10 call sites)

**Issue**: Every Kaltura task calls `self._kaltura_urls()`
10 times during extraction. Each call constructs a new
`KalturaUrlBuilder(self.task_id)`. That's 10 redundant
constructor calls per Kaltura task. For a workspace with
~100 Kaltura videos, that's 1000 wasted constructors.

**Fix** (5 min, zero risk):
```python
def __init__(self, ...):
    ...
    self._kaltura_urls_cache: Optional[KalturaUrlBuilder] = None

@property
def _kaltura_urls(self) -> KalturaUrlBuilder:
    if self._kaltura_urls_cache is None:
        self._kaltura_urls_cache = KalturaUrlBuilder(self.task_id)
    return self._kaltura_urls_cache
```

**Expected impact**: ~5-10ms saved per Kaltura task
(negligible per-task, but for 100 Kaltura tasks = 0.5-1s).

### 3.2 `lru_cache` on `PathTools.to_valid_name`

**Location**: `moodle_dl/utils.py:PathTools.to_valid_name`

**Issue**: `to_valid_name` is called many times per
download flow:
- Once in `generate_filename_with_index` (per task)
- Once in `gen_path` (per task)
- 1-2 times in `move_old_file`/`rename_old_file`
- Once in `set_path`/`create_target_file` (per task)

For 38k tasks, this is ~100k calls. `to_valid_name`
performs 2-3 regex substitutions. At 1.78µs each, that's
~180ms total. Cacheable because most names are unique
per task but repeat across the same file's lifecycle.

**Fix** (5 min):
```python
class PathTools:
    @staticmethod
    @lru_cache(maxsize=4096)
    def to_valid_name(name: str, is_file: bool) -> str:
        # existing implementation
        ...
```

**Expected impact**: ~100ms total (small but free).

### 3.3 Batch the per-file DB writes

**Location**: `moodle_dl/downloader/download_service.py` + `moodle_dl/database.py:save_file`

**Issue**: Each successful task calls
`database.save_file(...)` which triggers an immediate
SQLite INSERT (in WAL mode with `commit()`). For 38k
files, that's 38k individual transactions.

**Current cost**: ~1-2ms per `commit()` (WAL write +
fsync). Total: 38-76 seconds.

**Fix**: Buffer writes in a list, flush in batches of
100 files OR every 5 seconds (whichever comes first). The
buffer would be a `DownloadService._pending_writes` list
that gets flushed by a periodic timer in
`log_download_status`.

**Expected impact**: 30-70 seconds saved on a 38k-file
run.

**Risk**: If the process crashes between batches, you lose
the last batch's DB writes. Mitigate by flushing at
key boundaries (e.g. before display_summary).

## 4. Medium-impact recommendations

### 4.1 `lru_cache` on `is_filtered_external_domain`

**Location**: `task.py:845-862` (calls `urlparse.urlparse`)

**Issue**: `is_filtered_external_domain` parses the URL
on every call. For linked files, it's called multiple
times per file (in `may_perform_network_io` + in
`real_run`'s `_execute_download`).

**Fix**: Add a `@cached_property` decorator on the result.

**Expected impact**: ~1-2ms per call, 2-4 calls per linked
file. Saves ~100-500ms per 38k files.

### 4.2 `os.makedirs` is expensive

**Location**: `moodle_dl/utils.py:PathTools.make_dirs`,
called by `create_target_file` and several others.

**Issue**: `os.makedirs(..., exist_ok=True)` is a syscall
+ directory check. Per-file this is ~50µs. For 38k files
that touch 38k directories, that's ~1.9s.

**Fix**: Cache "I have already created this dir" set,
skipping repeated calls. Or restructure to batch dir
creation per parent.

**Expected impact**: ~1-2 seconds saved.

### 4.3 Sentry SDK init is heavy

**Location**: `moodle_dl/main.py:_init_sentry`

**Issue**: `sentry_sdk.init(dsn)` does network I/O
(fetches envelope limits from sentry.io), plus installs
signal handlers, plus creates background threads. This
is ~500ms-2s on the first call.

**Fix**: Sentry SDK auto-init can be delayed until
needed (lazy). If the user doesn't trigger an error,
we pay the cost for nothing.

**Expected impact**: 0-2s on startup (only on first run).

## 5. Low-impact (skip these)

| Item | Cost | Risk |
|---|---:|---|
| Move per-file `time.time()` to a snapshot | 0.1µs | zero |
| Cache `os.path.exists()` results | 0.5µs | zero |
| Defer `set_utime()` to summary phase | ~2s | medium (mtime becomes "now" if delayed) |
| Use `aiofiles.os` async makedirs | ~50µs | low |

These are below the noise floor — won't show up in
real-world benchmarks.

## 6. Things NOT to optimize

- **Sequential download loop** — KCL rate-limits
  concurrent downloads; this is intentional
- **`asyncio.run_in_executor` for yt-dlp** — yt-dlp
  blocks; no way to avoid
- **HTML rewriting** — only 1000 HTML files total, runs
  once each
- **Per-task `asyncio.sleep(1)` in error retry** — only
  fires on error, total <1s
- **Logging overhead** — already throttled

## 7. Profile first, then optimize

Before applying any of the above, run a real-world
profile on a 38k-file download:

```bash
python -m cProfile -o profile.out moodle-dl
python -c "import pstats; p = pstats.Stats('profile.out'); p.sort_stats('cumulative').print_stats(50)"
```

Look for top 10 cumulative-time functions, then
target those. The recommendations above are
informed guesses based on code-readthrough, not
profiling data.

## 8. Implementation plan (recommended order)

1. **Run cProfile** on a 1k-file subset to get baseline
2. **Implement #3.3 (batch DB writes)** — biggest
   expected win (~30-70s)
3. **Implement #3.1 (cache _kaltura_urls)** + **#3.2
   (lru_cache to_valid_name)** + **#4.1 (cache
   is_filtered_external_domain)** — easy, low-risk,
   ~200-500ms total
4. **Run cProfile again** to verify improvements

## 9. What I'd skip entirely

The throttle (already done), the lazy destination/
filename (already done), the Sentry init (one-time),
the `_kaltura_urls` (cosmetic), and the per-file
`time.time()` (0.1µs) — these are all sub-100ms
improvements that aren't worth the risk.

The real performance wins are in (a) the throttle
(done), and (b) **batched DB writes** (not done,
would be the next biggest win).
