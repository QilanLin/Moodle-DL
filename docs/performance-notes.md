# Moodle-DL Performance Notes

The single-threaded sequential download flow (see
`moodle_dl/downloader/download_service.py:556-571`) is
intentional — concurrent downloads were tested but disabled
because they triggered KCL rate-limiting. The performance
recommendations below are for the parts that are NOT
constrained by rate limiting.

## High-impact (recommended)

### 1. Lazily compute `destination` and `filename` (skip the cost for tasks that will be skipped)

**Current behavior** (task.py:354-355): every Task
constructor eagerly computes:

```python
self.destination = self.gen_path(options.download_path, course, file)
self.filename = self._file_ops.generate_filename_with_index(file)
```

These run even for tasks that will be skipped by
`may_perform_network_io` (metadata files, data: URLs,
description HTML, etc.). The cost is:
- 1 `to_valid_name()` call (regex + replace) on the file name
- 1 `make_path()` call (`os.path.join`) on the directory
- 1 `position_in_section` lookup on the File object

**Real impact**: For a 38000-file course set with maybe 20%
skipped (7600 files), that's 7600 wasted path/filename
computations. Each is fast (~1µs) but it adds up to ~10ms
of pure Python overhead, plus makes the Task object's
`__init__` ~10% slower for skipped tasks.

**Fix** (small, low-risk):
```python
@property
def destination(self) -> str:
    if self._destination is None:
        self._destination = self.gen_path(
            self.opts.download_path, self.course, self.file,
        )
    return self._destination

# similar for filename
```

Add `self._destination = None` and `self._filename = None` in
`__init__`. The `Path(self.destination) / self.filename` call
sites don't need to change.

### 2. Cache `to_valid_name()` results within a single Task (used many times)

`PT.to_valid_name` is called in many places:
- `gen_path` (once per task)
- `generate_filename_with_index` (once per task)
- `move_old_file` (1-2 times)
- `rename_old_file`
- `set_path` (1+ times)
- `create_target_file` (1+ times)

For one task that has a renamed file, `to_valid_name` may
be called 3-5 times with the same input. A `functools.lru_cache`
on the function (with bounded size) would eliminate the
redundancy.

**Fix** (trivial):
```python
# in moodle_dl/utils.py
from functools import lru_cache

class PathTools:
    @staticmethod
    @lru_cache(maxsize=4096)
    def to_valid_name(name: str, is_file: bool) -> str:
        # existing implementation
        ...
```

### 3. Reduce the throttle jitter for 38k+ file downloads

**Already done in this commit** (3efc49f): jitter reduced
from 1.0 to 0.5, saving up to 4 min per run.

## Medium-impact

### 4. Batch DB writes in the download loop

**Current behavior**: Each task calls
`database.new_file(self.file, self.course.id, ...)` once
when it succeeds. For 38000 files, that's 38000 individual
SQLite transactions.

**Fix**: Open a single transaction per `_display_download_summary`
cycle (e.g. every 100 files). Move `new_file` to
`database.buffered_new_file()` and have the buffer flush
on a timer or threshold. This is straightforward but
requires schema-aware logic.

**Expected impact**: 2-3x throughput on SQLite-bound
workloads. For 38000 files, that could shave 1-2 minutes.

### 5. Skip duplicate `is_filtered_external_domain` work

`is_filtered_external_domain` parses the URL on every call.
For 38000 tasks, that's 38000 URL parses even though the
result is usually the same per (course, file). Cache
results per-task or per-file.

**Fix**: Add a `@cached_property` decorator on the result.

### 6. Pre-allocate the status `bytes_downloaded` array for atomic updates

`status.bytes_downloaded += bytes_received` is racy under
asyncio (though Python's GIL makes the increment atomic,
the += is a read-modify-write that could lose updates
under heavy concurrency). Use `status.delta_bytes(...)` or
just trust the GIL — depending on the concurrency model.

## Low-impact

### 7. Avoid the per-call `set()` in `_rewrite_html_resource_links_after_task`

Each task calls this. The set is built fresh. For 1000
HTML files this is 1000 sets. Use a class-level cache.

### 8. Move the `os.makedirs` for `dest_path` to lazy

`create_target_file` calls `PT.touch_file(target_path)` which
calls `os.makedirs`. For tasks that are skipped before
running, this is wasted. Trivial savings.

## Constraints / things NOT to optimize

- **Sequential download** (intentional — KCL rate limits)
- **Synchronous `requests.Session`** (used for SSO cookie
  flow; can't easily async-ify)
- **Per-task `asyncio.run_in_executor` for yt-dlp** (yt-dlp is
  blocking; can't avoid)
- **Per-file DB write** (one write per file is the right
  granularity for crash recovery)

## Profile first

Before optimizing, profile a real run. The main hot paths
in the previous investigation were:
1. `get_mozilla_jar()` (3.8 sec / 1000 calls) — fixed via
   caching in the cookie manager
2. `set_utime()` per file (1.2 sec / 1000 files) — could be
   batched
3. `progress_tracker.update()` log noise — already
   throttled by snapshot comparison
4. Sentry SDK init overhead — already lazy

A 5-minute `cProfile` run on a real download will reveal
where the remaining 1-2 minutes per 38000 files are spent.
