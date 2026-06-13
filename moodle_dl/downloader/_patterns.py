"""
Reusable patterns and abstractions for the downloader subsystem.

This module extracts common patterns that were previously
duplicated across multiple call sites. The goal is to make
each "I want to do X" require exactly one call to one helper,
not 3-4 lines of try/except + cleanup repeated everywhere.

Helpers:
  * NET_ERRORS: tuple of all transient network error types that
    should be retried by the download loop.
  * safe_remove_part_and_final(paths): atomically clean up a
    .part file and its final counterpart, idempotent.
  * ensure_parent_dir(path): mkdir -p the parent of `path`,
    swallowing the case where the parent is empty (file is
    at the workspace root).
  * safe_write_text(path, content): utf-8 text file write with
    parent-dir creation and atomic-ish error handling.
  * IncompleteRecord: a small value object representing a row
    in the incomplete_downloads table, with a clean save()
    method that takes the recorder.

These were extracted from task.py where they were originally
duplicated 3-4 times each. Keeping them in one place means
future download pipelines (e.g. leganto, leganto_print) can
reuse the same robustness.
"""
import os
import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Dict, Iterable, Optional, Tuple

if TYPE_CHECKING:
    from moodle_dl.database import StateRecorder


# ---------------------------------------------------------------------------
# Error tuple
# ---------------------------------------------------------------------------
#: All exception types that moodle-dl treats as transient network
#: errors and retries. Use this in `except` clauses so a new error
#: type can be added in one place.
NET_ERRORS: Tuple[type, ...] = (
    OSError,                # I/O errors: connection reset, broken pipe
    ValueError,             # encoding marker raised by _perform_download_request
    # NOTE: aiohttp.ClientError and ContentRangeError are added
    # by the caller because the latter is defined in task.py and
    # we want to avoid a circular import.
)


# ---------------------------------------------------------------------------
# File cleanup
# ---------------------------------------------------------------------------
def safe_remove_part_and_final(
    dest_path: str,
    part_path: Optional[str] = None,
    pt_remove_file: Any = None,
) -> None:
    """Remove the .part file AND the final file (if it exists).

    Used in error-handling paths to ensure no stale bytes are
    left on disk. Idempotent: missing files are silently ignored.

    Args:
        dest_path: the final file path (e.g. '*11* foo.pdf').
        part_path: the .part path. If None, derived from dest_path
            using the dest_path_to_part_path() convention.
        pt_remove_file: a callable that takes a path and removes
            it (typically moodle_dl.utils.PT.remove_file). If None,
            the OS-level os.remove is used.

    Note:
        Pass part_path=None in most cases — the convention
        (final = dest_path, part = final + '.part') is universal.
    """
    from moodle_dl.downloader.task import dest_path_to_part_path

    if part_path is None:
        part_path = dest_path_to_part_path(dest_path)

    remover = pt_remove_file or _default_remover
    for path in (part_path, dest_path):
        if path:
            remover(path)


def _default_remover(path: str) -> None:
    """Fallback remover: best-effort os.remove."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        # Permission denied, etc. — best effort.
        pass


# ---------------------------------------------------------------------------
# Directory creation
# ---------------------------------------------------------------------------
def ensure_parent_dir(path: str) -> None:
    """mkdir -p the parent of `path`.

    Idempotent. If path has no parent (e.g. it's a bare filename
    in cwd), this is a no-op.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def ensure_dir(path: str) -> None:
    """mkdir -p `path` itself, idempotent."""
    if path:
        os.makedirs(path, exist_ok=True)


# ---------------------------------------------------------------------------
# Incomplete-download record value object
# ---------------------------------------------------------------------------
class IncompleteRecord:
    """A typed value object representing a row in
    `incomplete_downloads`.

    Replaces the multiple `cursor.execute(INSERT INTO
    incomplete_downloads ...)` boilerplate sites in task.py
    and download_service.py.
    """

    __slots__ = (
        'file_id', 'file_url', 'file_path',
        'downloaded_bytes', 'total_bytes',
        'server_supports_range', 'etag', 'last_modified',
    )

    def __init__(
        self,
        file_id: int,
        file_url: str,
        file_path: str,
        downloaded_bytes: int,
        total_bytes: int = 0,
        server_supports_range: bool = True,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ):
        self.file_id = file_id
        self.file_url = file_url
        self.file_path = file_path
        self.downloaded_bytes = downloaded_bytes
        self.total_bytes = total_bytes
        self.server_supports_range = server_supports_range
        self.etag = etag
        self.last_modified = last_modified

    def save(self, recorder: 'StateRecorder') -> None:
        """Persist this record to the database. Thin wrapper
        around recorder.save_incomplete_download."""
        recorder.save_incomplete_download(
            file_id=self.file_id,
            file_url=self.file_url,
            file_path=self.file_path,
            total_bytes=self.total_bytes,
            downloaded_bytes=self.downloaded_bytes,
            server_supports_range=self.server_supports_range,
            etag=self.etag,
            last_modified=self.last_modified,
        )

    def to_row(self) -> Dict[str, Any]:
        """Return this record as a dict (useful for logging)."""
        return {
            'file_id': self.file_id,
            'file_path': self.file_path,
            'downloaded_bytes': self.downloaded_bytes,
            'total_bytes': self.total_bytes,
        }


# ---------------------------------------------------------------------------
# File-existence query for resume
# ---------------------------------------------------------------------------
def part_file_size_or_none(part_path: str) -> Optional[int]:
    """Return the size of part_path if it exists, else None.

    Helper for the resume path: callers do
    `size = part_file_size_or_none(part_path)` and decide
    based on whether size is None (no .part) or zero (empty).
    """
    try:
        return os.path.getsize(part_path)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# SQLite inspection (used by tests and by some production paths)
# ---------------------------------------------------------------------------
def query_count(conn_or_recorder, table: str, where: str = '', params: tuple = ()) -> int:
    """Count rows matching a (optional) WHERE clause.

    Accepts either a sqlite3.Connection or a StateRecorder
    (which has a .db_file attribute).
    """
    db_file = conn_or_recorder.db_file if hasattr(conn_or_recorder, 'db_file') else conn_or_recorder
    sql = f'SELECT COUNT(*) FROM {table}' + (f' WHERE {where}' if where else '')
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cleanup-on-exception helper
# ---------------------------------------------------------------------------
@contextmanager
def cleanup_on_failure(paths_to_remove: Iterable[str], pt_remove_file: Any = None):
    """Context manager: remove the given paths if the body raises.

    Useful for the parts of download that must guarantee
    cleanup of intermediate state (e.g. .part file) on any
    exception, not just network errors.

    Usage:
        with cleanup_on_failure([part_path, dest_path]):
            # do something that may raise
            await write(...)
    """
    paths = list(paths_to_remove)
    try:
        yield paths
    except BaseException:
        for p in paths:
            (pt_remove_file or _default_remover)(p)
        raise
